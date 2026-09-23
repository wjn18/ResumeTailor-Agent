"""Configuration boundary for task storage and LangGraph checkpoint storage."""

import os


def create_run_store():
    from app.storage.workflow_postgres import PostgresRunStore

    url = (os.getenv("CHECKPOINT_DATABASE_URL") or os.getenv("DATABASE_URL", "")).strip()
    if not url:
        raise RuntimeError("CHECKPOINT_DATABASE_URL or DATABASE_URL is required for workflow persistence.")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return PostgresRunStore(url, pool_size=8)
