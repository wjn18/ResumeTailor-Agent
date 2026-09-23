import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from app.schemas.tailoring import TailoringDecisionRequest, FormalResumeDocument
from app.services.formal_resume_review import review_formal_resume, document_claims
from app.storage.workflow_base import RunConflict
from app.test import test_tailoring_graph_runtime as runtime_tests
from app.test.test_tailoring_graph_runtime import PassingClient
from app.test.test_tailoring_workflow import sample_jd, sample_resume
from app.workflows.tailoring_graph import build_tailoring_graph


class DocumentAuditClient(PassingClient):
    def fact_check_resume(self, jd_id, resume, draft):
        if draft.summary and "[" in draft.summary[0].section:
            self.fact_check_calls += 1
            return {"jd_id": jd_id, "resume_id": resume.resume_id, "checks": [
                {**item.model_dump(), "support_status": "partially_supported" if "fabricated" in item.sentence else "supported",
                 "issue": "No evidence" if "fabricated" in item.sentence else None}
                for item in draft.summary
            ]}
        return super().fact_check_resume(jd_id, resume, draft)


class HumanDecisionTests(unittest.TestCase):
    setUp = runtime_tests.TailoringRuntimeTests.setUp
    mock_documents = runtime_tests.TailoringRuntimeTests.mock_documents
    use_runtime = runtime_tests.TailoringRuntimeTests.use_runtime

    def generated(self):
        runtime = self.use_runtime(DocumentAuditClient())
        job = runtime.submit(sample_jd(), sample_resume())
        job = runtime.execute(job["thread_id"])
        self.assertEqual(job["status"], "awaiting_confirmation", job.get("error"))
        return runtime, job

    def decision(self, action="confirm", version=1, document=None, request_id=None):
        return TailoringDecisionRequest(request_id=request_id or uuid4(), action=action,
                                       expected_version=version, formal_resume=document)

    def test_graph_really_interrupts_and_confirmation_does_not_rerun_model(self):
        runtime, job = self.generated()
        config = {"configurable": {"thread_id": job["thread_id"]}}
        graph = build_tailoring_graph(checkpointer=runtime.store.checkpointer, human_loop=True)
        self.assertEqual(graph.get_state(config).next, ("human_decision",))
        self.assertEqual(len(graph.get_state(config).tasks[0].interrupts), 1)
        runtime.decide(job["thread_id"], self.decision())
        completed = runtime.execute(job["thread_id"])
        self.assertEqual(completed["status"], "completed", completed.get("error"))
        self.assertEqual(completed["result"]["saved_resume"]["status"], "confirmed")
        self.assertEqual(runtime.client.fact_check_calls, 1)
        self.assertEqual(runtime.client.match_calls, 1)

    def test_edit_invalidates_old_review_then_checks_exact_full_document(self):
        runtime, job = self.generated()
        document = FormalResumeDocument.model_validate(job["current_document"])
        document.education_experiences[0].school = "fabricated school"
        runtime.decide(job["thread_id"], self.decision("edit", document=document))
        self.assertIsNone(runtime.get(job["thread_id"])["result"])
        edited = runtime.execute(job["thread_id"])
        self.assertEqual(edited["status"], "needs_attention", edited.get("error"))
        self.assertEqual(edited["content_version"], 2)
        self.assertEqual(edited["reviewed_content_version"], 2)
        self.assertEqual(edited["result"]["formal_resume"], document.model_dump(mode="json"))
        self.assertTrue(any("fabricated" in c["sentence"] for c in edited["result"]["final_fact_check_report"]["checks"]))
        with self.assertRaises(RunConflict):
            runtime.decide(job["thread_id"], self.decision(version=2))
        document.education_experiences[0].school = "Original school"
        runtime.decide(job["thread_id"], self.decision("edit", 2, document))
        self.assertEqual(runtime.execute(job["thread_id"])["status"], "awaiting_confirmation")
        runtime.decide(job["thread_id"], self.decision(version=3))
        self.assertEqual(runtime.execute(job["thread_id"])["status"], "completed")
        self.assertEqual(runtime.client.revision_calls, 0)  # Human edits are never silently rewritten.

    def test_duplicate_command_is_idempotent_and_stale_versions_conflict(self):
        runtime, job = self.generated()
        decision = self.decision("edit", document=FormalResumeDocument.model_validate(job["current_document"]))
        queued = runtime.decide(job["thread_id"], decision)
        self.assertEqual(runtime.decide(job["thread_id"], decision), queued)
        edited = runtime.execute(job["thread_id"])
        self.assertEqual(edited["content_version"], 2, edited.get("error"))
        self.assertEqual(runtime.decide(job["thread_id"], decision)["content_version"], 2)
        with self.assertRaises(RunConflict):
            runtime.decide(job["thread_id"], self.decision())
        with self.assertRaises(RunConflict):
            runtime.decide(job["thread_id"], self.decision(request_id=decision.request_id))
        self.assertEqual(runtime.client.fact_check_calls, 2)
        confirm = self.decision(version=2)
        runtime.decide(job["thread_id"], confirm)
        runtime.execute(job["thread_id"])
        with self.assertRaises(RunConflict):
            runtime.decide(job["thread_id"], decision.model_copy(update={"expected_version": 2}))

    def test_failed_edit_review_retries_without_incrementing_version_or_regenerating(self):
        runtime, job = self.generated()
        runtime.decide(job["thread_id"], self.decision("edit", document=FormalResumeDocument.model_validate(job["current_document"])))
        with patch.object(runtime.client, "fact_check_resume", side_effect=RuntimeError("offline")):
            failed = runtime.execute(job["thread_id"])
        self.assertEqual(failed["failed_node"], "review_edited_document")
        self.assertEqual(failed["reviewed_content_version"], 0)
        runtime.resume(job["thread_id"])
        done = runtime.execute(job["thread_id"])
        self.assertEqual(done["status"], "awaiting_confirmation", done.get("error"))
        self.assertEqual(done["content_version"], 2)
        self.assertEqual(runtime.client.draft_calls, 1)

    def test_lost_final_ack_does_not_reapply_edit_to_next_interrupt(self):
        runtime, job = self.generated()
        runtime.decide(job["thread_id"], self.decision("edit", document=FormalResumeDocument.model_validate(job["current_document"])))
        original = runtime.store.update
        def fail_ack(thread_id, **changes):
            if changes.get("result"):
                raise RuntimeError("lost ack")
            return original(thread_id, **changes)
        with patch.object(runtime.store, "update", side_effect=fail_ack):
            self.assertEqual(runtime.execute(job["thread_id"])["status"], "failed")
        runtime.resume(job["thread_id"])
        recovered = runtime.execute(job["thread_id"])
        self.assertEqual(recovered["status"], "awaiting_confirmation", recovered.get("error"))
        self.assertEqual(recovered["content_version"], 2)
        self.assertEqual(runtime.client.fact_check_calls, 2)

    def test_http_export_only_after_graph_confirmation_and_blocked_during_new_edit(self):
        runtime, job = self.generated()
        task_url = f"/tailoring/tasks/{job['thread_id']}"
        saved_id = job["result"]["saved_resume"]["tailored_resume_id"]
        saved_url = f"/tailoring/saved/{saved_id}"
        document = job["current_document"]
        self.assertEqual(self.http.get(saved_url + "/docx").status_code, 409)
        for path, method in (("/confirm", self.http.post), ("/content", self.http.patch)):
            self.assertEqual(method(saved_url + path, json={"formal_resume": document}).status_code, 409)
        body = self.decision().model_dump(mode="json")
        self.assertEqual(self.http.post(task_url + "/decision", json={**body, "formal_resume": document}).status_code, 422)
        self.assertEqual(self.http.post(task_url + "/decision", json=body).status_code, 202)
        runtime.execute(job["thread_id"])
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.services.docx_export.TAILORED_RESUME_EXPORT_DIR", Path(directory)):
                downloaded = self.http.get(saved_url + "/docx")
                self.assertEqual(downloaded.status_code, 200, downloaded.text[:200] if downloaded.status_code != 200 else "")
                self.assertTrue(downloaded.content.startswith(b"PK"))
        body = self.decision("edit", document=FormalResumeDocument.model_validate(document)).model_dump(mode="json")
        self.assertEqual(self.http.post(task_url + "/decision", json=body).status_code, 202)
        self.assertEqual(self.http.get(saved_url + "/docx").status_code, 409)
        runtime.execute(job["thread_id"])
        self.assertEqual(self.http.get(saved_url + "/docx").status_code, 409)

    def test_out_of_band_document_tampering_cannot_export(self):
        runtime, job = self.generated()
        runtime.decide(job["thread_id"], self.decision())
        completed = runtime.execute(job["thread_id"])
        saved_id = completed["result"]["saved_resume"]["tailored_resume_id"]
        self.stored[saved_id].formal_resume.name = "tampered"
        self.assertEqual(self.http.get(f"/tailoring/saved/{saved_id}/docx").status_code, 409)


class FormalDocumentAuditTests(unittest.TestCase):
    def test_all_document_fields_are_audited_and_missing_checks_fail_closed(self):
        document = FormalResumeDocument(name="changed name", email="changed@example.com",
            headline="changed title", advantages=["fabricated"], work_experiences=[{
                "company": "changed employer", "job_title": "changed role", "bullets": ["moved claim"],
            }], education_experiences=[{"school": "changed school"}], related_skills=["changed skill"])
        claims = list(document_claims(document))
        self.assertEqual(len(claims), 7)
        self.assertTrue(any("changed employer" in text and "moved claim" in text for _, text in claims))
        client = DocumentAuditClient()
        with patch.object(client, "fact_check_resume", return_value={"jd_id": "x", "resume_id": "y", "checks": []}):
            report = review_formal_resume(sample_jd(), sample_resume(), document, client)
        self.assertEqual(len(report.checks), 7)
        self.assertTrue(all(c.support_status != "supported" for c in report.checks))


if __name__ == "__main__":
    unittest.main()
