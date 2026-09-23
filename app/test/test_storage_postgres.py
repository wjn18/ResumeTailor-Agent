"""Real PostgreSQL integration tests, enabled by TEST_DATABASE_URL.

Each test owns a randomly named schema and drops only that schema on exit.
Use a disposable test database with permission to create schemas.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.jds import ParsedJD
from app.schemas.resumes import ParsedResume
from app.schemas.tailoring import FormalResumeDocument, TailoredResumeDraft
from app.services.jd_parser import load_parsed_jd, save_parsed_jd
from app.services.resume_parser import load_parsed_resume, save_parsed_resume
from app.storage import factory
from app.storage.base import StorageConflictError
from app.storage.postgres import PostgresStorage


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for PostgreSQL integration tests")
class PostgresIntegrationTests(unittest.TestCase):
    def setUp(self):
        base_url = os.environ["TEST_DATABASE_URL"]
        schema = "storage_test_" + uuid4().hex
        with psycopg.connect(base_url) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

        def cleanup_schema():
            with psycopg.connect(base_url) as connection:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

        self.addCleanup(cleanup_schema)
        # Excluding public ensures unqualified application SQL cannot reach it.
        self.url = make_conninfo(base_url, options=f"-csearch_path={schema}")
        self.enterContext(patch.dict(os.environ, {
            "STORAGE_BACKEND": "postgresql", "DATABASE_URL": self.url,
        }))
        factory._cached_storage.cache_clear()
        self.addCleanup(factory._cached_storage.cache_clear)
        self.storage = PostgresStorage(self.url)
        self.client = self.enterContext(TestClient(app))

    def create(self, resource, payload):
        response = self.client.post(f"/crud/{resource}", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_relational_crud_json_values_and_cascade_through_api(self):
        user = self.create("users", {"name": "测试用户", "email": "test@example.org"})
        self.assertIsInstance(user["created_time"], str)
        resume = self.create("original-resumes", {
            "user_id": user["id"], "file_path": "resume.pdf",
            "parsed_content_json": {"skills": ["Python"], "name": "张三"},
        })
        fact = self.create("experience-facts", {
            "resume_id": resume["id"], "category": "work", "entity_name": "项目",
            "fact_text": "开发后端", "verified": True,
        })
        self.assertIs(fact["verified"], True)
        jd = self.create("job-descriptions", {
            "user_id": user["id"], "company": "示例", "job_title": "开发",
            "raw_text": "Python", "parsed_json": '{"requirements": ["Python"]}',
        })
        self.assertEqual(jd["parsed_json"], {"requirements": ["Python"]})
        tailored = self.create("tailored-resumes", {
            "master_resume_id": resume["id"], "job_description_id": jd["id"],
            "content_json": {"lines": []}, "match_score": 90,
        })
        change = self.create("resume-changes", {
            "tailored_resume_id": tailored["id"], "section": "summary",
            "new_text": "后端开发", "source_fact_ids": ["fact_1"],
        })
        for resource, item in (
            ("users", user), ("original-resumes", resume), ("experience-facts", fact),
            ("job-descriptions", jd), ("tailored-resumes", tailored), ("resume-changes", change),
        ):
            with self.subTest(resource=resource):
                self.assertEqual(self.client.get(f"/crud/{resource}/{item['id']}").json(), item)
                self.assertEqual(self.client.get(f"/crud/{resource}").json(), [item])

        response = self.client.patch(f"/crud/original-resumes/{resume['id']}", json={"parsed_content_json": None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["parsed_content_json"])
        self.assertEqual(response.json()["file_path"], "resume.pdf")
        self.assertEqual(self.client.delete(f"/crud/users/{user['id']}").status_code, 204)
        for resource in ("users", "original-resumes", "experience-facts", "job-descriptions", "tailored-resumes", "resume-changes"):
            self.assertEqual(self.client.get(f"/crud/{resource}").json(), [])
        self.assertEqual(self.client.get(f"/crud/users/{user['id']}").status_code, 404)
        self.assertEqual(self.client.delete(f"/crud/users/{user['id']}").status_code, 404)

    def test_integrity_conflicts_rollback_and_map_to_http_409(self):
        first = self.create("users", {"name": "A", "email": "a@example.org"})
        second = self.create("users", {"name": "B", "email": "b@example.org"})
        response = self.client.post("/crud/users", json={"name": "duplicate", "email": first["email"]})
        self.assertEqual(response.status_code, 409)
        response = self.client.patch(f"/crud/users/{second['id']}", json={"email": first["email"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.storage.get_row("users", second["id"])["email"], second["email"])
        with self.assertRaises(StorageConflictError):
            self.storage.create_row("original_resume", {"user_id": 999999, "file_path": "missing.pdf"})
        self.assertEqual(len(self.storage.list_rows("users")), 2)
        self.assertEqual(self.storage.list_rows("original_resume"), [])
        self.assertEqual(self.client.patch(f"/crud/users/{first['id']}", json={}).status_code, 400)

    def test_pagination_and_repeated_initialization_preserve_data(self):
        users = [self.create("users", {"name": str(i), "email": f"{i}@example.org"}) for i in range(3)]
        factory.initialize_database()
        self.assertEqual(self.client.get("/crud/users?limit=1&offset=1").json(), [users[1]])
        self.assertIsNone(self.storage.patch_row("users", 999999, {"name": "missing"}))

    def test_parsed_document_roundtrip_and_replacement(self):
        resume = ParsedResume(resume_id="resume_test", name="张三")
        jd = ParsedJD(jd_id="jd_test", raw_text_length=10, company="示例公司")
        save_parsed_resume(resume)
        save_parsed_jd(jd)
        self.assertEqual(load_parsed_resume(resume.resume_id), resume)
        self.assertEqual(load_parsed_jd(jd.jd_id), jd)
        resume.name = "李四"
        save_parsed_resume(resume)
        self.assertEqual(load_parsed_resume(resume.resume_id), resume)
        with self.assertRaises(FileNotFoundError):
            load_parsed_jd("missing")
        self.assertEqual(self.client.get("/tailoring/saved/missing").status_code, 404)

    def test_tailored_document_ordering_and_status_metadata(self):
        older = {"tailored_resume_id": "older", "status": "draft", "generated_at": "2025-01-01T00:00:00Z"}
        newer = {"tailored_resume_id": "newer", "status": "draft", "generated_at": "2025-02-01T00:00:00Z"}
        self.storage.save_tailored_resume_document(newer)
        self.storage.save_tailored_resume_document(older)
        confirmed = {**older, "status": "confirmed"}
        self.storage.save_tailored_resume_document(confirmed)
        self.assertEqual(self.storage.list_tailored_resume_documents(), [newer, confirmed])
        with psycopg.connect(self.url) as connection:
            rows = connection.execute(
                "SELECT status, generated_at, updated_at FROM tailored_resume_documents WHERE tailored_resume_id = %s",
                ("older",),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "confirmed")
        self.assertEqual(rows[0][1].year, 2025)
        self.assertIsNotNone(rows[0][2])

    def test_legacy_saved_resume_edit_requires_workflow_review_before_export(self):
        resume = ParsedResume(resume_id="resume_test", name="张三")
        jd = ParsedJD(jd_id="jd_test", raw_text_length=10, company="示例", job_title="开发")
        draft = TailoredResumeDraft(jd_id=jd.jd_id, resume_id=resume.resume_id)
        formal = FormalResumeDocument(name="张三", advantages=["开发后端"])
        payload = {"jd": jd.model_dump(), "resume": resume.model_dump(),
                   "draft": draft.model_dump(), "formal_resume": formal.model_dump()}
        response = self.client.post("/tailoring/save", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        url = f"/tailoring/saved/{saved['tailored_resume_id']}"
        second = self.client.post("/tailoring/save", json=payload).json()
        self.assertEqual(second["display_name"], saved["display_name"] + " 2")
        self.assertEqual(self.client.get("/tailoring/saved").json()[0]["tailored_resume_id"], second["tailored_resume_id"])
        formal.advantages = ["修改后的优势"]
        edit = self.client.patch(url + "/content", json={"formal_resume": formal.model_dump()})
        self.assertEqual(edit.status_code, 200, edit.text)
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.services.docx_export.TAILORED_RESUME_EXPORT_DIR", Path(directory)):
                confirmed = self.client.post(url + "/confirm", json={"formal_resume": formal.model_dump()})
                self.assertEqual(confirmed.status_code, 409, confirmed.text)
                download = self.client.get(url + "/docx")
                self.assertEqual(download.status_code, 409)
        reloaded = self.client.get(url).json()
        self.assertEqual(reloaded["formal_resume"]["advantages"], formal.advantages)
        self.assertIsNone(reloaded["confirmed_at"])
        self.client.patch(url + "/content", json={"formal_resume": formal.model_dump()})
        reloaded = self.client.get(url).json()
        self.assertEqual(reloaded["status"], "draft")
        self.assertIsNone(reloaded["confirmed_at"])
        self.assertIsNone(reloaded["docx_file_name"])
