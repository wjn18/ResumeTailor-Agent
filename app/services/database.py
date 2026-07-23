from pathlib import Path
import sqlite3
from typing import Any

from databae.init_database import DB_PATH, initialize_database


TABLE_COLUMNS = {
    "users": {
        "create": ("name", "email"),
        "patch": ("name", "email"),
    },
    "original_resume": {
        "create": ("user_id", "file_path", "parsed_content_json", "version"),
        "patch": ("user_id", "file_path", "parsed_content_json", "version"),
    },
    "experience_facts": {
        "create": (
            "resume_id",
            "category",
            "entity_name",
            "fact_text",
            "verified",
            "source_location",
        ),
        "patch": (
            "resume_id",
            "category",
            "entity_name",
            "fact_text",
            "verified",
            "source_location",
        ),
    },
    "job_descriptions": {
        "create": ("user_id", "company", "job_title", "raw_text", "parsed_json"),
        "patch": ("user_id", "company", "job_title", "raw_text", "parsed_json"),
    },
    "tailored_resumes": {
        "create": (
            "master_resume_id",
            "job_description_id",
            "content_json",
            "match_score",
            "status",
        ),
        "patch": (
            "master_resume_id",
            "job_description_id",
            "content_json",
            "match_score",
            "status",
        ),
    },
    "resume_changes": {
        "create": (
            "tailored_resume_id",
            "section",
            "original_text",
            "new_text",
            "source_fact_ids",
            "status",
            "user_feedback",
        ),
        "patch": (
            "tailored_resume_id",
            "section",
            "original_text",
            "new_text",
            "source_fact_ids",
            "status",
            "user_feedback",
        ),
    },
}


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    initialize_database(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def list_rows(table_name: str, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    _ensure_table(table_name)
    with connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM {table_name} ORDER BY id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_row(table_name: str, item_id: int) -> dict[str, Any] | None:
    _ensure_table(table_name)
    with connect() as connection:
        row = connection.execute(
            f"SELECT * FROM {table_name} WHERE id = ?",
            (item_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def create_row(table_name: str, values: dict[str, Any]) -> dict[str, Any]:
    allowed_columns = TABLE_COLUMNS[_ensure_table(table_name)]["create"]
    filtered_values = _filter_values(values, allowed_columns)
    if not filtered_values:
        raise ValueError("No writable fields provided.")

    columns = tuple(filtered_values)
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)

    with connect() as connection:
        cursor = connection.execute(
            f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
            tuple(_to_database_value(filtered_values[column]) for column in columns),
        )
        connection.commit()
        item_id = cursor.lastrowid

    created = get_row(table_name, item_id)
    if created is None:
        raise RuntimeError("Created row could not be loaded.")
    return created


def patch_row(table_name: str, item_id: int, values: dict[str, Any]) -> dict[str, Any] | None:
    allowed_columns = TABLE_COLUMNS[_ensure_table(table_name)]["patch"]
    filtered_values = _filter_values(values, allowed_columns)
    if not filtered_values:
        raise ValueError("No writable fields provided.")

    assignments = ", ".join(f"{column} = ?" for column in filtered_values)
    parameters = tuple(_to_database_value(value) for value in filtered_values.values()) + (item_id,)

    with connect() as connection:
        cursor = connection.execute(
            f"UPDATE {table_name} SET {assignments} WHERE id = ?",
            parameters,
        )
        connection.commit()
        if cursor.rowcount == 0:
            return None

    return get_row(table_name, item_id)


def delete_row(table_name: str, item_id: int) -> bool:
    _ensure_table(table_name)
    with connect() as connection:
        cursor = connection.execute(f"DELETE FROM {table_name} WHERE id = ?", (item_id,))
        connection.commit()
    return cursor.rowcount > 0


def _ensure_table(table_name: str) -> str:
    if table_name not in TABLE_COLUMNS:
        raise ValueError(f"Unsupported table: {table_name}")
    return table_name


def _filter_values(values: dict[str, Any], allowed_columns: tuple[str, ...]) -> dict[str, Any]:
    return {
        column: values[column]
        for column in allowed_columns
        if column in values
    }


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    if "verified" in data:
        data["verified"] = bool(data["verified"])
    return data


def _to_database_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value
