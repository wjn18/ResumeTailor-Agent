"""Durable execution tests against an isolated PostgreSQL schema per test."""

import os
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from app.main import app
from app.services.tailored_resume_storage import (
    list_tailored_resumes, load_tailored_resume, mark_tailored_resume_confirmed,
)
from app.storage import factory
from app.storage.postgres import PostgresStorage
from app.storage.workflow_base import RunBusy, RunConflict
from app.storage.workflow_postgres import PostgresRunStore
from app.test.test_tailoring_graph_runtime import PassingClient, TransientFailureClient
from app.test.test_tailoring_workflow import sample_jd, sample_resume
from app.workflows.tailoring_runtime import TailoringRuntime
from app.schemas.tailoring import FormalResumeDocument, TailoringDecisionRequest
from app.test.test_tailoring_decisions import DocumentAuditClient


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for PostgreSQL integration tests")
class DurableWorkflowTests(unittest.TestCase):
    def setUp(self):
        base_url = os.environ["TEST_DATABASE_URL"]
        schema = "workflow_test_" + uuid4().hex
        with psycopg.connect(base_url) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        def cleanup():
            with psycopg.connect(base_url) as conn:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

        self.addCleanup(cleanup)
        self.url = make_conninfo(base_url, options=f"-csearch_path={schema}")
        self.enterContext(patch.dict(os.environ, {
            "DATABASE_URL": self.url, "CHECKPOINT_DATABASE_URL": self.url,
            "STORAGE_BACKEND": "postgresql",
        }))
        factory._cached_storage.cache_clear()
        self.addCleanup(factory._cached_storage.cache_clear)
        PostgresStorage(self.url).initialize()

    def runtime(self, llm=None):
        runtime = TailoringRuntime(client=llm or PassingClient(), store=PostgresRunStore(self.url))
        self.addCleanup(runtime.close)
        return runtime

    def wait_status(self, runtime, thread_id, statuses):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = runtime.get(thread_id)
            if job["status"] in statuses:
                return job
            time.sleep(0.03)
        self.fail(f"Task did not reach {statuses}; last status: {job['status']}")

    def test_initial_checkpoint_survives_runtime_restart(self):
        first = self.runtime()
        job = first.submit(sample_jd(), sample_resume(), initial_only=True)
        self.assertEqual(first.execute(job["thread_id"])["status"], "initial_ready")
        first.close()
        llm = PassingClient()
        second = self.runtime(llm)
        self.assertTrue(second.get(job["thread_id"])["preview"])
        second.resume(job["thread_id"])
        result = second.execute(job["thread_id"])
        self.assertEqual(result["status"], "awaiting_confirmation", result.get("error"))
        self.assertEqual(llm.match_calls, 0)
        self.assertEqual(llm.draft_calls, 0)
        self.assertEqual(llm.fact_check_calls, 1)
        self.assertEqual(result["draft_version"], result["audit_version"])

    def test_process_crash_is_automatically_recovered_without_rebuilding(self):
        first = self.runtime()
        job = first.submit(sample_jd(), sample_resume())
        script = '''
import os
from app.storage.workflow_postgres import PostgresRunStore
from app.workflows.tailoring_runtime import TailoringRuntime
from app.test.test_tailoring_graph_runtime import PassingClient
class CrashingClient(PassingClient):
    def fact_check_resume(self, *args):
        os._exit(23)
runtime = TailoringRuntime(client=CrashingClient(), store=PostgresRunStore(os.environ['CHECKPOINT_DATABASE_URL']))
runtime.execute(os.environ['CRASH_TEST_THREAD_ID'])
'''
        env = {**os.environ, "CRASH_TEST_THREAD_ID": job["thread_id"]}
        child = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=20)
        self.assertEqual(child.returncode, 23, child.stderr.decode(errors="replace"))
        interrupted = first.get(job["thread_id"])
        self.assertEqual(interrupted["status"], "running")
        self.assertEqual(interrupted["current_node"], "fact_check_initial")
        llm = PassingClient()
        restored = self.runtime(llm)
        restored.start_workers(count=1, poll_seconds=0.03)
        result = self.wait_status(restored, job["thread_id"], {"awaiting_confirmation", "failed"})
        self.assertEqual(result["status"], "awaiting_confirmation", result.get("error"))
        self.assertEqual(llm.match_calls, 0)
        self.assertEqual(llm.draft_calls, 0)
        self.assertEqual(len(list_tailored_resumes()), 1)

    def test_separate_connections_cannot_execute_the_same_task(self):
        first, second = self.runtime(), self.runtime()
        job = first.submit(sample_jd(), sample_resume())
        with first.store.lease(job["thread_id"]):
            with self.assertRaises(RunBusy):
                second.execute(job["thread_id"])
        result = second.execute(job["thread_id"])
        self.assertEqual(result["status"], "awaiting_confirmation")
        self.assertEqual(first.execute(job["thread_id"])["result"], result["result"])
        self.assertEqual(len(list_tailored_resumes()), 1)

    def test_lock_connection_loss_prevents_stale_executor_writes(self):
        first, second = self.runtime(), self.runtime()
        job = first.submit(sample_jd(), sample_resume())
        with first.store.lease(job["thread_id"]) as lease:
            pid = lease.checkpointer.conn.info.backend_pid
            with psycopg.connect(self.url, autocommit=True) as conn:
                conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
            self.assertEqual(second.execute(job["thread_id"])["status"], "awaiting_confirmation")
            with self.assertRaises(psycopg.Error):
                lease.update(status="running")
        self.assertEqual(second.get(job["thread_id"])["status"], "awaiting_confirmation")

    def test_cancel_running_task_stops_after_inflight_node_without_saving(self):
        entered, release = Event(), Event()

        class BlockingClient(PassingClient):
            def fact_check_resume(self, *args):
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("Test release timed out")
                return super().fact_check_resume(*args)

        runtime = self.runtime(BlockingClient())
        job = runtime.submit(sample_jd(), sample_resume())
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(runtime.execute, job["thread_id"])
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(runtime.cancel(job["thread_id"])["status"], "cancelling")
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5)["status"], "cancelled")
        self.assertEqual(list_tailored_resumes(), [])
        with self.assertRaises(RunConflict):
            runtime.resume(job["thread_id"])

    def test_queued_cancel_and_save_boundary(self):
        runtime = self.runtime()
        job = runtime.submit(sample_jd(), sample_resume())
        self.assertEqual(runtime.cancel(job["thread_id"])["status"], "cancelled")
        self.assertEqual(runtime.execute(job["thread_id"])["status"], "cancelled")
        other = runtime.submit(sample_jd(), sample_resume())
        with runtime.store.lease(other["thread_id"]) as lease:
            self.assertTrue(lease.begin_finalizing())
            with self.assertRaises(RunConflict):
                runtime.cancel(other["thread_id"])

    def test_cancel_wins_over_racing_failure_or_pause(self):
        runtime = self.runtime()
        for status in ("failed", "queued", "initial_ready"):
            with self.subTest(status=status):
                job = runtime.submit(sample_jd(), sample_resume())
                with runtime.store.lease(job["thread_id"]) as lease:
                    lease.update(status="running")
                    runtime.cancel(job["thread_id"])
                    lease.update(status=status)
                self.assertEqual(runtime.get(job["thread_id"])["status"], "cancelled")

    def test_failed_node_metadata_and_manual_resume(self):
        runtime = self.runtime(TransientFailureClient())
        job = runtime.submit(sample_jd(), sample_resume())
        failed = runtime.execute(job["thread_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failed_node"], "fact_check_initial")
        self.assertNotIn(job["thread_id"], runtime.store.pending())
        self.assertEqual(failed["draft_version"], 1)
        self.assertEqual(failed["audit_version"], 0)
        runtime.resume(job["thread_id"])
        self.assertEqual(runtime.execute(job["thread_id"])["status"], "awaiting_confirmation")
        self.assertEqual(runtime.client.draft_calls, 1)

    def test_save_replay_preserves_confirmed_user_edits(self):
        runtime = self.runtime()
        job = runtime.submit(sample_jd(), sample_resume())
        # Phase 2 task recovery retains its historical projection semantics.
        with runtime.store.lease(job["thread_id"]) as lease:
            lease.update(graph_version=2)
        original = runtime.store._update
        raised = False

        def fail_after_save(conn, thread_id, **changes):
            nonlocal raised
            if "result" in changes and not raised:
                raised = True
                raise RuntimeError("Lost result acknowledgement")
            return original(conn, thread_id, **changes)

        with patch.object(runtime.store, "_update", side_effect=fail_after_save):
            self.assertEqual(runtime.execute(job["thread_id"])["status"], "failed")
        saved_id = "tailored_" + job["thread_id"].removeprefix("tailoring_")
        saved = load_tailored_resume(saved_id)
        edited = saved.formal_resume.model_copy(update={"advantages": ["用户修改后的内容"]})
        mark_tailored_resume_confirmed(saved_id, edited, "confirmed.docx")
        runtime.resume(job["thread_id"])
        self.assertEqual(runtime.execute(job["thread_id"])["status"], "awaiting_confirmation")
        restored = load_tailored_resume(saved_id)
        self.assertEqual(restored.status, "confirmed")
        self.assertEqual(restored.formal_resume, edited)
        self.assertEqual(len(list_tailored_resumes()), 1)
        self.assertEqual(runtime.client.fact_check_calls, 1)

    def test_task_api_returns_id_before_work_and_restores_latest_result(self):
        runtime = self.runtime()
        with patch("app.api.tailoring.get_tailoring_runtime", return_value=runtime):
            http = TestClient(app)
            response = http.post("/tailoring/tasks", json={
                "jd": sample_jd().model_dump(), "resume": sample_resume().model_dump(),
            })
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["status"], "queued")
            self.assertNotIn("inputs", response.json())
            self.assertEqual(runtime.client.match_calls, 0)
            thread_id = response.json()["thread_id"]
            with runtime.store.lease(thread_id) as lease:
                lease.update(graph_version=2)
            runtime.execute(thread_id)
            result = http.get(f"/tailoring/tasks/{thread_id}").json()
            saved_id = result["result"]["saved_resume"]["tailored_resume_id"]
            saved = load_tailored_resume(saved_id)
            edited = saved.formal_resume.model_copy(update={"advantages": ["已确认的修改"]})
            mark_tailored_resume_confirmed(saved_id, edited, "confirmed.docx")
            refreshed = http.get(f"/tailoring/tasks/{thread_id}").json()
            self.assertEqual(refreshed["status"], "completed")
            self.assertEqual(refreshed["result"]["formal_resume"]["advantages"], ["已确认的修改"])
            self.assertEqual(http.post(f"/tailoring/tasks/{thread_id}/cancel").status_code, 409)
            self.assertEqual(http.get("/tailoring/tasks/missing").status_code, 404)

    def test_atomic_result_insert_does_not_overwrite_existing_document(self):
        storage = PostgresStorage(self.url)
        payload = {"tailored_resume_id": "same", "status": "confirmed", "generated_at": "2026-01-01T00:00:00Z"}
        self.assertEqual(storage.create_tailored_resume_document(payload), payload)
        self.assertEqual(storage.create_tailored_resume_document({**payload, "status": "draft"}), payload)

    def test_creation_is_idempotent_and_rejects_changed_input(self):
        first, second = self.runtime(), self.runtime()
        request_id = uuid4()
        original = first.submit(sample_jd(), sample_resume(), request_id=request_id)
        repeated = second.submit(sample_jd(), sample_resume(), request_id=request_id)
        self.assertEqual(original, repeated)
        self.assertEqual(len(first.store.pending()), 1)
        with self.assertRaises(RunConflict):
            second.submit(sample_jd().model_copy(update={"job_title": "Different"}), sample_resume(), request_id=request_id)

    def test_http_submit_resume_cancel_and_idempotency(self):
        runtime = self.runtime(TransientFailureClient())
        with patch("app.api.tailoring.get_tailoring_runtime", return_value=runtime):
            http = TestClient(app)
            payload = {"jd": sample_jd().model_dump(), "resume": sample_resume().model_dump(), "request_id": str(uuid4())}
            first = http.post("/tailoring/tasks", json=payload).json()
            self.assertEqual(http.post("/tailoring/tasks", json=payload).json(), first)
            payload["jd"]["job_title"] = "Changed"
            self.assertEqual(http.post("/tailoring/tasks", json=payload).status_code, 409)
            thread_id = first["thread_id"]
            runtime.execute(thread_id)
            self.assertEqual(http.get(f"/tailoring/tasks/{thread_id}").json()["status"], "failed")
            resumed = http.post(f"/tailoring/tasks/{thread_id}/resume")
            self.assertEqual(resumed.status_code, 202)
            self.assertEqual(resumed.json()["status"], "queued")
            cancelled = http.post(f"/tailoring/tasks/{thread_id}/cancel")
            self.assertEqual(cancelled.status_code, 202)
            self.assertEqual(cancelled.json()["status"], "cancelled")
            self.assertEqual(http.post(f"/tailoring/tasks/{thread_id}/resume").status_code, 409)

    def test_human_interrupt_survives_restart_and_confirm_can_be_replayed(self):
        first = self.runtime()
        job = first.submit(sample_jd(), sample_resume())
        paused = first.execute(job["thread_id"])
        self.assertTrue(paused["pending_decision"]["can_confirm"])
        first.close()
        second = self.runtime()
        decision = TailoringDecisionRequest(request_id=uuid4(), action="confirm", expected_version=1)
        second.decide(job["thread_id"], decision)
        original = second.store._update
        def fail_ack(conn, thread_id, **changes):
            if changes.get("result"):
                raise RuntimeError("Confirmation acknowledgement lost")
            return original(conn, thread_id, **changes)
        with patch.object(second.store, "_update", side_effect=fail_ack):
            self.assertEqual(second.execute(job["thread_id"])["status"], "failed")
        second.close()
        third = self.runtime()
        third.resume(job["thread_id"])
        completed = third.execute(job["thread_id"])
        self.assertEqual(completed["status"], "completed", completed.get("error"))
        self.assertEqual(third.decide(job["thread_id"], decision)["status"], "completed")
        self.assertEqual(third.client.fact_check_calls, 0)
        saved_id = completed["result"]["saved_resume"]["tailored_resume_id"]
        saved = load_tailored_resume(saved_id)
        self.assertEqual(saved.status, "confirmed")
        self.assertEqual(third.confirmed_document(job["thread_id"], saved), saved.formal_resume)
        self.assertEqual(len(list_tailored_resumes()), 1)

    def test_process_crash_during_edited_document_review_recovers_same_version(self):
        first = self.runtime(DocumentAuditClient())
        job = first.submit(sample_jd(), sample_resume())
        ready = first.execute(job["thread_id"])
        document = FormalResumeDocument.model_validate(ready["current_document"])
        document.name = "Updated name"
        first.decide(job["thread_id"], TailoringDecisionRequest(
            request_id=uuid4(), action="edit", expected_version=1, formal_resume=document,
        ))
        script = '''
import os
from app.storage.workflow_postgres import PostgresRunStore
from app.workflows.tailoring_runtime import TailoringRuntime
from app.test.test_tailoring_decisions import DocumentAuditClient
class CrashingClient(DocumentAuditClient):
    def fact_check_resume(self, *args):
        os._exit(24)
runtime = TailoringRuntime(client=CrashingClient(), store=PostgresRunStore(os.environ['CHECKPOINT_DATABASE_URL']))
runtime.execute(os.environ['CRASH_TEST_THREAD_ID'])
'''
        child = subprocess.run([sys.executable, "-c", script],
                               env={**os.environ, "CRASH_TEST_THREAD_ID": job["thread_id"]},
                               capture_output=True, timeout=20)
        self.assertEqual(child.returncode, 24, child.stderr.decode(errors="replace"))
        crashed = first.get(job["thread_id"])
        self.assertEqual(crashed["content_version"], 2)
        self.assertEqual(crashed["reviewed_content_version"], 0)
        second = self.runtime(DocumentAuditClient())
        second.start_workers(count=1, poll_seconds=0.03)
        recovered = self.wait_status(second, job["thread_id"], {"awaiting_confirmation", "failed"})
        self.assertEqual(recovered["status"], "awaiting_confirmation", recovered.get("error"))
        self.assertEqual(recovered["content_version"], 2)
        self.assertEqual(recovered["result"]["formal_resume"], document.model_dump(mode="json"))
        self.assertEqual(second.client.match_calls, 0)
        self.assertEqual(second.client.fact_check_calls, 1)

    def test_competing_decisions_only_accept_one_version_and_old_download_is_blocked(self):
        first, second = self.runtime(DocumentAuditClient()), self.runtime(DocumentAuditClient())
        job = first.submit(sample_jd(), sample_resume())
        ready = first.execute(job["thread_id"])
        document = FormalResumeDocument.model_validate(ready["current_document"])
        action = TailoringDecisionRequest(request_id=uuid4(), action="edit", expected_version=1, formal_resume=document)
        first.decide(job["thread_id"], action)
        with self.assertRaises(RunConflict):
            second.decide(job["thread_id"], action.model_copy(update={"request_id": uuid4()}))
        reviewed = second.execute(job["thread_id"])
        self.assertEqual(reviewed["content_version"], 2)
        with self.assertRaises(RunConflict):
            first.decide(job["thread_id"], TailoringDecisionRequest(request_id=uuid4(), action="confirm", expected_version=1))
        second.decide(job["thread_id"], TailoringDecisionRequest(request_id=uuid4(), action="confirm", expected_version=2))
        complete = first.execute(job["thread_id"])
        saved_id = complete["result"]["saved_resume"]["tailored_resume_id"]
        saved = load_tailored_resume(saved_id)
        first.decide(job["thread_id"], action.model_copy(update={"request_id": uuid4(), "expected_version": 2}))
        with self.assertRaises(RunConflict):
            second.confirmed_document(job["thread_id"], saved)

    def test_stale_projection_cannot_overwrite_newer_content_or_confirmation(self):
        runtime = self.runtime(DocumentAuditClient())
        job = runtime.submit(sample_jd(), sample_resume())
        ready = runtime.execute(job["thread_id"])
        old = ready["result"]["saved_resume"]
        runtime.decide(job["thread_id"], TailoringDecisionRequest(request_id=uuid4(), action="confirm", expected_version=1))
        confirmed = runtime.execute(job["thread_id"])["result"]["saved_resume"]
        storage = PostgresStorage(self.url)
        self.assertEqual(storage.project_tailored_resume_document(old), confirmed)
        doc = FormalResumeDocument.model_validate(ready["current_document"])
        doc.name = "Changed name"
        runtime.decide(job["thread_id"], TailoringDecisionRequest(request_id=uuid4(), action="edit", expected_version=1, formal_resume=doc))
        edited = runtime.execute(job["thread_id"])["result"]["saved_resume"]
        self.assertEqual(storage.project_tailored_resume_document(confirmed), edited)

    def test_task_reads_wait_for_checkpoint_pipeline_lock(self):
        runtime = self.runtime()
        job = runtime.submit(sample_jd(), sample_resume())
        with runtime.store.lease(job["thread_id"]) as lease:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with lease.checkpointer.lock:
                    future = pool.submit(lease.read)
                    time.sleep(0.03)
                    self.assertFalse(future.done())
                self.assertEqual(future.result(timeout=2)["thread_id"], job["thread_id"])

    def test_phase_two_task_upgrades_using_latest_saved_edits(self):
        first = self.runtime(DocumentAuditClient())
        job = first.submit(sample_jd(), sample_resume())
        with first.store.lease(job["thread_id"]) as lease:
            lease.update(graph_version=2)
        old = first.execute(job["thread_id"])
        saved_id = old["result"]["saved_resume"]["tailored_resume_id"]
        edited = FormalResumeDocument.model_validate(old["result"]["formal_resume"])
        edited.name = "Preserved edit"
        mark_tailored_resume_confirmed(saved_id, edited, "old.docx")
        queued = first.resume(job["thread_id"])
        self.assertEqual(queued["graph_version"], 3)
        first.close()  # Migration intent must survive restart before checkpoint changes.
        second = self.runtime(DocumentAuditClient())
        upgraded = second.execute(job["thread_id"])
        self.assertEqual(upgraded["status"], "awaiting_confirmation", upgraded.get("error"))
        self.assertEqual(upgraded["result"]["formal_resume"], edited.model_dump(mode="json"))
        self.assertEqual(upgraded["content_version"], 1)
        self.assertEqual(second.client.match_calls, 0)
        self.assertEqual(second.client.fact_check_calls, 1)
        self.assertEqual(load_tailored_resume(saved_id).status, "draft")
        self.assertEqual(len(list_tailored_resumes()), 1)
