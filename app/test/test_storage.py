"""Backend selection, dependency boundaries, and driver error translation."""

import ast
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.jds import ParsedJD
from app.schemas.resumes import ParsedResume
from app.schemas.tailoring import TailoredResumeDraft
from app.services.jd_parser import load_parsed_jd, save_parsed_jd
from app.services.resume_parser import load_parsed_resume, save_parsed_resume
from app.services.tailored_resume_storage import load_tailored_resume, save_tailored_resume
from app.storage import factory
from app.storage.base import Storage, StorageConflictError, StorageError
from app.storage.postgres import PostgresStorage


class StorageSelectionTests(unittest.TestCase):
    def setUp(self):
        factory._cached_storage.cache_clear()
        self.addCleanup(factory._cached_storage.cache_clear)
        self.storage = Mock(spec=Storage)
        self.constructor = Mock(return_value=self.storage)
        self.enterContext(patch.dict(factory.BACKENDS, {"test": self.constructor}))
        self.enterContext(patch.dict(os.environ, {
            "STORAGE_BACKEND": "test", "DATABASE_URL": "test://isolated",
        }))

    def test_startup_and_api_use_the_configured_adapter(self):
        self.storage.list_rows.return_value = []
        with TestClient(app) as client:
            self.assertEqual(client.get("/crud/users").json(), [])
        self.storage.initialize.assert_called_once_with()
        self.storage.list_rows.assert_called_once_with("users", 100, 0)
        self.constructor.assert_called_once_with("test://isolated")

    def test_all_document_services_use_the_configured_adapter(self):
        documents = {}

        def upsert(table, column, document_id, payload):
            documents[table, document_id] = payload

        self.storage.upsert_json_document.side_effect = upsert
        self.storage.load_json_document.side_effect = lambda table, column, key: documents[table, key]
        self.storage.list_tailored_resume_documents.return_value = []
        self.storage.save_tailored_resume_document.side_effect = lambda payload: upsert(
            "tailored_resume_documents", "tailored_resume_id", payload["tailored_resume_id"], payload
        )
        resume = ParsedResume(resume_id="resume_test")
        jd = ParsedJD(jd_id="jd_test", raw_text_length=10)
        save_parsed_resume(resume)
        save_parsed_jd(jd)
        self.assertEqual(load_parsed_resume(resume.resume_id), resume)
        self.assertEqual(load_parsed_jd(jd.jd_id), jd)
        saved = save_tailored_resume(
            jd, resume, TailoredResumeDraft(jd_id=jd.jd_id, resume_id=resume.resume_id)
        )
        self.assertEqual(load_tailored_resume(saved.tailored_resume_id), saved)

    def test_api_maps_neutral_errors_and_missing_rows(self):
        self.storage.create_row.side_effect = StorageConflictError("Conflict")
        self.storage.patch_row.side_effect = StorageConflictError("Conflict")
        self.storage.get_row.return_value = None
        self.storage.delete_row.return_value = False
        with TestClient(app) as client:
            self.assertEqual(client.post("/crud/users", json={"name": "A", "email": "a@b"}).status_code, 409)
            self.assertEqual(client.patch("/crud/users/1", json={"name": "A"}).status_code, 409)
            self.assertEqual(client.get("/crud/users/1").status_code, 404)
            self.assertEqual(client.delete("/crud/users/1").status_code, 404)
            self.storage.patch_row.side_effect = ValueError("No writable fields provided.")
            self.assertEqual(client.patch("/crud/users/1", json={}).status_code, 400)
            self.storage.patch_row.side_effect = None
            self.storage.patch_row.return_value = None
            self.assertEqual(client.patch("/crud/users/1", json={"name": "A"}).status_code, 404)

    def test_configuration_changes_do_not_reuse_the_previous_adapter(self):
        first, second = Mock(spec=Storage), Mock(spec=Storage)
        self.constructor.side_effect = [first, second]
        self.assertIs(factory.get_storage(), first)
        with patch.dict(os.environ, {"DATABASE_URL": "test://other"}):
            self.assertIs(factory.get_storage(), second)
        self.assertIs(factory.get_storage(), first)

    def test_legacy_cli_initializes_selected_backend(self):
        from databae.init_database import initialize_database

        initialize_database()
        self.storage.initialize.assert_called_once_with()

    def test_invalid_configuration_fails_before_connecting(self):
        with self.assertRaisesRegex(ValueError, "Unsupported STORAGE_BACKEND"):
            factory.create_storage(backend="unknown")
        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
            factory.create_storage(database_url="  ")
        self.constructor.assert_not_called()

    def test_postgresql_is_default_and_construction_does_not_connect(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgres://user@localhost/test"}, clear=True):
            with patch("app.storage.postgres.psycopg.connect") as connect:
                self.assertIsInstance(factory.get_storage(), PostgresStorage)
                connect.assert_not_called()

    def test_application_layers_do_not_import_concrete_storage(self):
        app_root = Path(__file__).resolve().parents[1]
        for folder in ("api", "services", "workflows"):
            for path in (app_root / folder).glob("*.py"):
                with self.subTest(path=path.name):
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                    imports = []
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            imports.extend(alias.name for alias in node.names)
                        elif isinstance(node, ast.ImportFrom):
                            imports.append(node.module or "")
                    self.assertFalse(any(
                        name.startswith(("psycopg", "sqlite3", "app.storage.postgres", "databae"))
                        for name in imports
                    ), imports)


class PostgresErrorTests(unittest.TestCase):
    @patch("app.storage.postgres.psycopg.connect")
    def test_connection_errors_are_translated(self, connect):
        cause = psycopg.OperationalError("driver details")
        connect.side_effect = cause
        with self.assertRaises(StorageError) as caught:
            PostgresStorage("unused").list_rows("users")
        self.assertIs(caught.exception.__cause__, cause)
        self.assertNotIn("driver details", str(caught.exception))

    @patch("app.storage.postgres.psycopg.connect")
    def test_commit_errors_are_translated(self, connect):
        connection = connect.return_value.__enter__.return_value
        cause = psycopg.IntegrityError("deferred constraint")
        connection.commit.side_effect = cause
        with self.assertRaises(StorageConflictError) as caught:
            PostgresStorage("unused").create_row("users", {"name": "A", "email": "a@b"})
        self.assertIs(caught.exception.__cause__, cause)

    @patch("app.storage.postgres.psycopg.connect")
    def test_invalid_identifiers_and_empty_writes_never_reach_driver(self, connect):
        storage = PostgresStorage("unused")
        operations = [
            lambda: storage.list_rows("users; DROP TABLE users"),
            lambda: storage.load_json_document("parsed_resume_documents", "wrong_id", "id"),
            lambda: storage.upsert_json_document("users", "id", "id", {}),
            lambda: storage.create_row("users", {"id": 12}),
            lambda: storage.patch_row("users", 1, {}),
        ]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
        connect.assert_not_called()
