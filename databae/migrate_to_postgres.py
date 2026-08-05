"""Migrate the legacy SQLite database and JSON document files to PostgreSQL.

Run after setting DATABASE_URL: python -m databae.migrate_to_postgres
"""

import json
import sqlite3
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from databae.init_database import get_database_url, initialize_database


ROOT = Path(__file__).resolve().parents[1]
SQLITE_PATH = ROOT / "databae" / "resume_tailor.sqlite3"
DOCUMENT_SOURCES = (
    (ROOT / "app" / "data" / "resumes", "parsed_resume_documents", "resume_id"),
    (ROOT / "app" / "data" / "job_descriptions", "parsed_jd_documents", "jd_id"),
    (ROOT / "app" / "data" / "tailored_resumes", "tailored_resume_documents", "tailored_resume_id"),
)
RELATIONAL_TABLES = (
    "users", "original_resume", "experience_facts", "job_descriptions",
    "tailored_resumes", "resume_changes",
)
JSON_COLUMNS = {
    "original_resume": {"parsed_content_json"},
    "job_descriptions": {"parsed_json"},
    "tailored_resumes": {"content_json"},
    "resume_changes": {"source_fact_ids"},
}


def migrate() -> None:
    database_url = get_database_url()
    initialize_database(database_url)
    with psycopg.connect(database_url) as target:
        if SQLITE_PATH.exists():
            _migrate_sqlite(target)
        for directory, table, id_column in DOCUMENT_SOURCES:
            _migrate_json_directory(target, directory, table, id_column)
        target.commit()


def _migrate_sqlite(target: psycopg.Connection) -> None:
    source = sqlite3.connect(SQLITE_PATH)
    source.row_factory = sqlite3.Row
    try:
        existing = {row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        for table in RELATIONAL_TABLES:
            if table not in existing:
                continue
            for source_row in source.execute(f"SELECT * FROM {table}"):
                row = dict(source_row)
                for column in JSON_COLUMNS.get(table, set()):
                    if row.get(column) is not None:
                        try:
                            row[column] = Jsonb(json.loads(row[column]))
                        except (TypeError, json.JSONDecodeError):
                            row[column] = Jsonb({"value": row[column]})
                columns = list(row)
                statement = sql.SQL(
                    "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT (id) DO NOTHING"
                ).format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                    sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                )
                target.execute(statement, [row[column] for column in columns])
            target.execute(
                sql.SQL(
                    "SELECT setval(pg_get_serial_sequence({}, 'id'), "
                    "COALESCE((SELECT MAX(id) FROM {}), 1), true)"
                ).format(sql.Literal(table), sql.Identifier(table))
            )
    finally:
        source.close()


def _migrate_json_directory(
    target: psycopg.Connection, directory: Path, table: str, id_column: str
) -> None:
    if not directory.exists():
        return
    for file_path in directory.glob("*.json"):
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        document_id = payload.get(id_column) or file_path.stem
        if table == "tailored_resume_documents":
            target.execute(
                """INSERT INTO tailored_resume_documents
                       (tailored_resume_id, payload, status, generated_at)
                   VALUES (%s, %s, %s, COALESCE(%s::timestamptz, CURRENT_TIMESTAMP))
                   ON CONFLICT (tailored_resume_id) DO UPDATE
                   SET payload = EXCLUDED.payload, status = EXCLUDED.status,
                       updated_at = CURRENT_TIMESTAMP""",
                (document_id, Jsonb(payload), payload.get("status", "draft"), payload.get("generated_at")),
            )
        else:
            statement = sql.SQL(
                "INSERT INTO {} ({}, payload) VALUES (%s, %s) "
                "ON CONFLICT ({}) DO UPDATE SET payload = EXCLUDED.payload, "
                "updated_at = CURRENT_TIMESTAMP"
            ).format(sql.Identifier(table), sql.Identifier(id_column), sql.Identifier(id_column))
            target.execute(statement, (document_id, Jsonb(payload)))


if __name__ == "__main__":
    migrate()
    print("Legacy SQLite and JSON data migrated to PostgreSQL.")
