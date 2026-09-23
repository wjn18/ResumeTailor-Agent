"""Stable, driver-independent persistence functions for application services."""

from typing import Any

from app.storage.factory import get_storage, initialize_database


def list_rows(table_name: str, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    return get_storage().list_rows(table_name, limit, offset)


def get_row(table_name: str, item_id: int) -> dict[str, Any] | None:
    return get_storage().get_row(table_name, item_id)


def create_row(table_name: str, values: dict[str, Any]) -> dict[str, Any]:
    return get_storage().create_row(table_name, values)


def patch_row(table_name: str, item_id: int, values: dict[str, Any]) -> dict[str, Any] | None:
    return get_storage().patch_row(table_name, item_id, values)


def delete_row(table_name: str, item_id: int) -> bool:
    return get_storage().delete_row(table_name, item_id)


def upsert_json_document(table: str, id_column: str, document_id: str, payload: dict) -> None:
    return get_storage().upsert_json_document(table, id_column, document_id, payload)


def load_json_document(table: str, id_column: str, document_id: str) -> dict:
    return get_storage().load_json_document(table, id_column, document_id)


def list_tailored_resume_documents() -> list[dict]:
    return get_storage().list_tailored_resume_documents()


def save_tailored_resume_document(payload: dict) -> None:
    return get_storage().save_tailored_resume_document(payload)


def create_tailored_resume_document(payload: dict) -> dict:
    return get_storage().create_tailored_resume_document(payload)


def project_tailored_resume_document(payload: dict) -> dict:
    return get_storage().project_tailored_resume_document(payload)
