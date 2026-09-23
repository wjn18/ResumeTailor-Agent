"""PostgreSQL adapter: SQL, driver values, and driver errors stay here."""

import json
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.storage.base import Storage, StorageConflictError, StorageError
from app.storage.postgres_schema import SCHEMA


TABLE_COLUMNS = {
    "users": {"create": ("name", "email"), "patch": ("name", "email")},
    "original_resume": {
        "create": ("user_id", "file_path", "parsed_content_json", "version"),
        "patch": ("user_id", "file_path", "parsed_content_json", "version"),
    },
    "experience_facts": {
        "create": ("resume_id", "category", "entity_name", "fact_text", "verified", "source_location"),
        "patch": ("resume_id", "category", "entity_name", "fact_text", "verified", "source_location"),
    },
    "job_descriptions": {
        "create": ("user_id", "company", "job_title", "raw_text", "parsed_json"),
        "patch": ("user_id", "company", "job_title", "raw_text", "parsed_json"),
    },
    "tailored_resumes": {
        "create": ("master_resume_id", "job_description_id", "content_json", "match_score", "status"),
        "patch": ("master_resume_id", "job_description_id", "content_json", "match_score", "status"),
    },
    "resume_changes": {
        "create": ("tailored_resume_id", "section", "original_text", "new_text", "source_fact_ids", "status", "user_feedback"),
        "patch": ("tailored_resume_id", "section", "original_text", "new_text", "source_fact_ids", "status", "user_feedback"),
    },
}
JSON_COLUMNS = {"parsed_content_json", "parsed_json", "content_json", "source_fact_ids"}


class PostgresStorage(Storage):
    def __init__(self, database_url: str):
        self._database_url = database_url

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        try:
            with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
                yield connection
        except psycopg.IntegrityError as exc:
            raise StorageConflictError("The write violates a data integrity constraint.") from exc
        except psycopg.Error as exc:
            raise StorageError("The database operation failed.") from exc

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(SCHEMA)

    def list_rows(self, table_name: str, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        _ensure_table(table_name)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table_name} ORDER BY id LIMIT %s OFFSET %s", (limit, offset)
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_row(self, table_name: str, item_id: int) -> dict[str, Any] | None:
        _ensure_table(table_name)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table_name} WHERE id = %s", (item_id,)
            ).fetchone()
        return _row_to_dict(row) if row else None

    def create_row(self, table_name: str, values: dict[str, Any]) -> dict[str, Any]:
        allowed_columns = TABLE_COLUMNS[_ensure_table(table_name)]["create"]
        filtered_values = _filter_values(values, allowed_columns)
        if not filtered_values:
            raise ValueError("No writable fields provided.")
        columns = tuple(filtered_values)
        placeholders = ", ".join("%s" for _ in columns)
        with self._connect() as connection:
            row = connection.execute(
                f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders}) RETURNING *",
                tuple(_to_database_value(column, filtered_values[column]) for column in columns),
            ).fetchone()
            connection.commit()
        return _row_to_dict(row)

    def patch_row(self, table_name: str, item_id: int, values: dict[str, Any]) -> dict[str, Any] | None:
        allowed_columns = TABLE_COLUMNS[_ensure_table(table_name)]["patch"]
        filtered_values = _filter_values(values, allowed_columns)
        if not filtered_values:
            raise ValueError("No writable fields provided.")
        assignments = ", ".join(f"{column} = %s" for column in filtered_values)
        parameters = tuple(
            _to_database_value(column, value) for column, value in filtered_values.items()
        ) + (item_id,)
        with self._connect() as connection:
            row = connection.execute(
                f"UPDATE {table_name} SET {assignments} WHERE id = %s RETURNING *", parameters
            ).fetchone()
            connection.commit()
        return _row_to_dict(row) if row else None

    def delete_row(self, table_name: str, item_id: int) -> bool:
        _ensure_table(table_name)
        with self._connect() as connection:
            row = connection.execute(
                f"DELETE FROM {table_name} WHERE id = %s RETURNING id", (item_id,)
            ).fetchone()
            connection.commit()
        return row is not None

    def upsert_json_document(self, table: str, id_column: str, document_id: str, payload: dict) -> None:
        _ensure_document_table(table, id_column)
        with self._connect() as connection:
            connection.execute(
                f"""INSERT INTO {table} ({id_column}, payload)
                    VALUES (%s, %s)
                    ON CONFLICT ({id_column}) DO UPDATE
                    SET payload = EXCLUDED.payload, updated_at = CURRENT_TIMESTAMP""",
                (document_id, Jsonb(payload)),
            )
            connection.commit()

    def load_json_document(self, table: str, id_column: str, document_id: str) -> dict:
        _ensure_document_table(table, id_column)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload FROM {table} WHERE {id_column} = %s", (document_id,)
            ).fetchone()
        if row is None:
            raise FileNotFoundError(document_id)
        return row["payload"]

    def list_tailored_resume_documents(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM tailored_resume_documents ORDER BY generated_at DESC"
            ).fetchall()
        return [row["payload"] for row in rows]

    def save_tailored_resume_document(self, payload: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO tailored_resume_documents
                       (tailored_resume_id, payload, status, generated_at, updated_at)
                   VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                   ON CONFLICT (tailored_resume_id) DO UPDATE
                   SET payload = EXCLUDED.payload,
                       status = EXCLUDED.status,
                       updated_at = CURRENT_TIMESTAMP""",
                (
                    payload["tailored_resume_id"],
                    Jsonb(payload),
                    payload["status"],
                    payload["generated_at"],
                ),
            )
            connection.commit()

    def create_tailored_resume_document(self, payload: dict) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO tailored_resume_documents
                       (tailored_resume_id, payload, status, generated_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (tailored_resume_id) DO NOTHING RETURNING payload""",
                (payload["tailored_resume_id"], Jsonb(payload), payload["status"], payload["generated_at"]),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT payload FROM tailored_resume_documents WHERE tailored_resume_id=%s",
                    (payload["tailored_resume_id"],),
                ).fetchone()
            return row["payload"]

    def project_tailored_resume_document(self, payload: dict) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE tailored_resume_documents
                   SET payload=%s, status=%s, updated_at=CURRENT_TIMESTAMP
                   WHERE tailored_resume_id=%s AND
                     (COALESCE((payload->>'content_version')::integer,0),
                      CASE WHEN status='confirmed' THEN 1 ELSE 0 END) < (%s,%s)
                   RETURNING payload""",
                (Jsonb(payload), payload["status"], payload["tailored_resume_id"],
                 payload["content_version"], int(payload["status"] == "confirmed")),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT payload FROM tailored_resume_documents WHERE tailored_resume_id=%s",
                    (payload["tailored_resume_id"],),
                ).fetchone()
            if row is None:
                raise FileNotFoundError(payload["tailored_resume_id"])
            return row["payload"]


def _ensure_document_table(table: str, id_column: str) -> None:
    allowed = {
        ("parsed_resume_documents", "resume_id"),
        ("parsed_jd_documents", "jd_id"),
        ("tailored_resume_documents", "tailored_resume_id"),
    }
    if (table, id_column) not in allowed:
        raise ValueError("Unsupported document table.")


def _ensure_table(table_name: str) -> str:
    if table_name not in TABLE_COLUMNS:
        raise ValueError(f"Unsupported table: {table_name}")
    return table_name


def _filter_values(values: dict[str, Any], allowed_columns: tuple[str, ...]) -> dict[str, Any]:
    return {column: values[column] for column in allowed_columns if column in values}


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, (datetime, date)) else value
        for key, value in dict(row).items()
    }


def _to_database_value(column: str, value: Any) -> Any:
    if column not in JSON_COLUMNS or value is None:
        return value
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {"value": value}
    return Jsonb(value)
