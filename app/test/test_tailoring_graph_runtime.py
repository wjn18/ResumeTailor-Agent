import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.tailoring import PARTIALLY_SUPPORTED
from app.schemas.tailoring import SavedTailoredResume
from app.storage.workflow_base import RunBusy
from app.test.test_tailoring_workflow import (
    AuditedTailoringClient,
    TwoPassRevisionClient,
    sample_jd,
    sample_resume,
)
from app.workflows.tailoring_runtime import TailoringRuntime


class CountingClient(AuditedTailoringClient):
    def __init__(self):
        super().__init__()
        self.match_calls = 0
        self.draft_calls = 0

    def match_requirements(self, jd, resume):
        self.match_calls += 1
        return super().match_requirements(jd, resume)

    def rewrite_resume(self, jd, resume, match_report):
        self.draft_calls += 1
        return super().rewrite_resume(jd, resume, match_report)


class PassingClient(CountingClient):
    def rewrite_resume(self, jd, resume, match_report):
        draft = super().rewrite_resume(jd, resume, match_report)
        draft["summary"][0]["sentence"] = "使用 FastAPI 构建后端服务。"
        return draft


class NeverPassingClient(CountingClient):
    def fact_check_resume(self, jd_id, resume, draft):
        report = super().fact_check_resume(jd_id, resume, draft)
        report["checks"][0].update(
            support_status=PARTIALLY_SUPPORTED, issue="仍缺少事实依据。",
        )
        return report


class UnchangedRevisionClient(NeverPassingClient):
    def revise_after_fact_check(self, jd, resume, draft, fact_check_report):
        self.revision_calls += 1
        return draft.model_dump(mode="json")


class TransientFailureClient(CountingClient):
    def __init__(self):
        super().__init__()
        self.failed_once = False

    def fact_check_resume(self, jd_id, resume, draft):
        if not self.failed_once:
            self.failed_once = True
            raise RuntimeError("Temporary audit failure")
        return super().fact_check_resume(jd_id, resume, draft)


class TailoringRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.http = TestClient(app)
        self.payload = {
            "jd": sample_jd().model_dump(mode="json"),
            "resume": sample_resume().model_dump(mode="json"),
        }
        self.stored = {}
        self.write_patch = patch(
            "app.services.tailored_resume_storage._write_tailored_resume",
            side_effect=lambda item: self.stored.__setitem__(item.tailored_resume_id, item),
        )
        self.write_mock = self.write_patch.start()
        self.addCleanup(self.write_patch.stop)
        names = patch("app.services.tailored_resume_storage.list_tailored_resumes", return_value=[])
        names.start()
        self.addCleanup(names.stop)
        self.mock_documents()

    def mock_documents(self):
        def load(key):
            if key not in self.stored:
                raise FileNotFoundError(key)
            return self.stored[key]

        def insert(payload):
            saved = self.stored.setdefault(
                payload["tailored_resume_id"], SavedTailoredResume.model_validate(payload),
            )
            return saved.model_dump(mode="json")

        for target in ("app.services.tailored_resume_storage.load_tailored_resume", "app.api.tailoring.load_tailored_resume"):
            self.enterContext(patch(target, side_effect=load))
        self.insert_mock = self.enterContext(patch(
            "app.services.tailored_resume_storage.create_tailored_resume_document", side_effect=insert,
        ))

    def use_runtime(self, llm, **kwargs):
        runtime = TailoringRuntime(client=llm, **kwargs)
        mocked = patch("app.api.tailoring.get_tailoring_runtime", return_value=runtime)
        mocked.start()
        self.addCleanup(mocked.stop)
        return runtime

    def start(self):
        response = self.http.post("/tailoring/build/initial", json=self.payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["thread_id"]

    def review(self, thread_id):
        return self.http.post("/tailoring/build/review", json={"thread_id": thread_id})

    def test_initial_pass_is_audited_only_once_in_both_paths(self):
        for staged in (False, True):
            with self.subTest(staged=staged):
                llm = PassingClient()
                self.use_runtime(llm)
                response = self.review(self.start()) if staged else self.http.post(
                    "/tailoring/build", json=self.payload,
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "awaiting_confirmation")
                self.assertEqual(response.json()["revision_count"], 0)
                self.assertEqual(llm.fact_check_calls, 1)
                self.assertEqual(llm.revision_calls, 0)
                self.assertEqual(llm.match_calls, 1)
                self.assertEqual(llm.draft_calls, 1)

    def test_revision_limit_returns_attention_and_never_saves_in_both_paths(self):
        for staged in (False, True):
            with self.subTest(staged=staged):
                llm = NeverPassingClient()
                self.use_runtime(llm)
                response = self.review(self.start()) if staged else self.http.post(
                    "/tailoring/build", json=self.payload,
                )
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["status"], "needs_attention")
                self.assertEqual(result["revision_count"], 2)
                self.assertIsNone(result["saved_resume"])
                self.assertTrue(result["formal_resume"])
                self.assertEqual(llm.revision_calls, 2)
                self.assertEqual(
                    result["final_fact_check_report"]["checks"][0]["support_status"],
                    PARTIALLY_SUPPORTED,
                )
                repeated = self.review(result["thread_id"])
                self.assertEqual(repeated.json()["status"], "needs_attention")
                self.assertEqual(llm.revision_calls, 2)
        self.write_mock.assert_not_called()
        self.insert_mock.assert_not_called()

    def test_unchanged_draft_reuses_audit_and_keeps_failure(self):
        llm = UnchangedRevisionClient()
        self.use_runtime(llm)
        result = self.review(self.start()).json()
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(llm.fact_check_calls, 1)
        self.assertEqual(llm.revision_calls, 2)

    def test_second_revision_can_pass_through_staged_graph(self):
        llm = TwoPassRevisionClient()
        self.use_runtime(llm)
        result = self.review(self.start()).json()
        self.assertEqual(result["status"], "awaiting_confirmation")
        self.assertEqual(result["revision_count"], 2)
        self.assertEqual(llm.fact_check_calls, 3)

    def test_failed_audit_resumes_without_rebuilding(self):
        llm = TransientFailureClient()
        runtime = self.use_runtime(llm)
        thread_id = self.start()
        failed = self.review(thread_id)
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(runtime.get(thread_id)["status"], "failed")
        resumed = self.review(thread_id)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["status"], "awaiting_confirmation")
        self.assertEqual(llm.match_calls, 1)
        self.assertEqual(llm.draft_calls, 1)

    def test_failed_save_can_retry_without_rerunning_graph(self):
        llm = PassingClient()
        self.use_runtime(llm)
        thread_id = self.start()
        with patch("app.workflows.tailoring_runtime.save_tailored_resume", side_effect=RuntimeError("DB unavailable")):
            failed = self.review(thread_id)
        self.assertEqual(failed.status_code, 400)
        completed = self.review(thread_id)
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(llm.fact_check_calls, 1)
        self.assertEqual(len(self.stored), 1)

    def test_review_rejects_client_state_and_unknown_thread(self):
        self.use_runtime(PassingClient())
        thread_id = self.start()
        for extra in (self.payload, {"draft": {}}, {"match_report": {}}):
            response = self.http.post(
                "/tailoring/build/review", json={"thread_id": thread_id, **extra},
            )
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.review("tailoring_" + "0" * 32).status_code, 404)
        self.assertEqual(self.review("invalid").status_code, 422)

    def test_concurrent_review_is_rejected_without_duplicate_work(self):
        llm = PassingClient()
        runtime = self.use_runtime(llm)
        thread_id = self.start()
        with runtime.store.lease(thread_id):
            with self.assertRaises(RunBusy):
                runtime.execute(thread_id)
        self.assertEqual(llm.fact_check_calls, 0)
        self.assertEqual(self.review(thread_id).status_code, 200)

    def test_interleaved_runs_keep_their_input_snapshots(self):
        self.use_runtime(PassingClient())
        first = self.start()
        self.payload["jd"]["jd_id"] = "jd_second"
        second = self.start()
        second_result = self.review(second).json()
        first_result = self.review(first).json()
        self.assertNotEqual(first, second)
        self.assertEqual(first_result["draft"]["jd_id"], sample_jd().jd_id)
        self.assertEqual(second_result["draft"]["jd_id"], "jd_second")
        self.assertEqual(len(self.stored), 2)

    def test_failed_creation_keeps_a_resumable_task(self):
        llm = PassingClient()
        runtime = self.use_runtime(llm)
        with patch.object(llm, "match_requirements", side_effect=RuntimeError("Unavailable")):
            response = self.http.post("/tailoring/build/initial", json=self.payload)
        self.assertEqual(response.status_code, 400)
        thread_id = response.json()["detail"]["thread_id"]
        self.assertEqual(runtime.get(thread_id)["failed_node"], "match_requirements")
        self.assertEqual(self.review(thread_id).status_code, 200)

    def test_runtime_replacement_retains_task_and_its_checkpoint(self):
        first_client = PassingClient()
        runtime = self.use_runtime(first_client)
        thread_id = self.start()
        second_client = PassingClient()
        self.use_runtime(second_client, store=runtime.store)
        response = self.review(thread_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(second_client.draft_calls, 0)
        self.assertEqual(second_client.match_calls, 0)
        self.assertEqual(second_client.fact_check_calls, 1)

    def test_legacy_unresolved_report_blocks_confirmation_before_export(self):
        self.use_runtime(NeverPassingClient())
        result = self.review(self.start()).json()
        # Legacy /save remains usable for draft storage, but cannot make an
        # explicitly failed audit confirmable.
        saved = self.http.post("/tailoring/save", json={
            **self.payload,
            "draft": result["draft"],
            "formal_resume": result["formal_resume"],
            "final_fact_check_report": result["final_fact_check_report"],
        })
        self.assertEqual(saved.status_code, 200, saved.text)
        saved_id = saved.json()["tailored_resume_id"]
        with (
            patch("app.api.tailoring.load_tailored_resume", return_value=self.stored[saved_id]),
            patch("app.api.tailoring.export_formal_resume_docx") as export,
        ):
            response = self.http.post(
                f"/tailoring/saved/{saved_id}/confirm",
                json={"formal_resume": result["formal_resume"]},
            )
        self.assertEqual(response.status_code, 409)
        export.assert_not_called()


if __name__ == "__main__":
    unittest.main()
