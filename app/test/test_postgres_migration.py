import os
import unittest
from unittest.mock import patch

from app.schemas.jds import ParsedJD
from app.schemas.resumes import ParsedResume
from app.services.jd_parser import save_parsed_jd
from app.services.resume_parser import save_parsed_resume
from databae.init_database import SCHEMA, get_database_url


class PostgreSQLMigrationTests(unittest.TestCase):
    def test_schema_uses_jsonb_document_tables(self):
        self.assertIn("parsed_resume_documents", SCHEMA)
        self.assertIn("parsed_jd_documents", SCHEMA)
        self.assertIn("tailored_resume_documents", SCHEMA)
        self.assertGreaterEqual(SCHEMA.count("JSONB"), 7)
        self.assertNotIn("AUTOINCREMENT", SCHEMA)
        self.assertNotIn("PRAGMA", SCHEMA)

    def test_database_url_is_required_and_normalized(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                get_database_url()
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgres://user:pass@db.example/resume"},
            clear=True,
        ):
            self.assertEqual(
                get_database_url(),
                "postgresql://user:pass@db.example/resume",
            )

    @patch("app.services.resume_parser.upsert_json_document")
    def test_resume_payload_is_written_as_json_document(self, upsert):
        resume = ParsedResume(resume_id="resume_test")
        self.assertEqual(save_parsed_resume(resume), "resume_test")
        upsert.assert_called_once_with(
            "parsed_resume_documents",
            "resume_id",
            "resume_test",
            resume.model_dump(mode="json"),
        )

    @patch("app.services.jd_parser.upsert_json_document")
    def test_jd_payload_is_written_as_json_document(self, upsert):
        jd = ParsedJD(jd_id="jd_test", raw_text_length=16)
        self.assertEqual(save_parsed_jd(jd), "jd_test")
        upsert.assert_called_once_with(
            "parsed_jd_documents",
            "jd_id",
            "jd_test",
            jd.model_dump(mode="json"),
        )


if __name__ == "__main__":
    unittest.main()
