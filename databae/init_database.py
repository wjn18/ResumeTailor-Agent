"""PostgreSQL schema initialization for ResumeTailor.

The package name is intentionally kept as ``databae`` for backwards compatibility.
"""

import os

import psycopg


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    created_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS original_resume (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    parsed_content_json JSONB,
    version INTEGER NOT NULL DEFAULT 1,
    created_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS experience_facts (
    id BIGSERIAL PRIMARY KEY,
    resume_id BIGINT NOT NULL REFERENCES original_resume(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    fact_text TEXT NOT NULL,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    source_location TEXT,
    created_time TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_descriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company TEXT NOT NULL,
    job_title TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    parsed_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tailored_resumes (
    id BIGSERIAL PRIMARY KEY,
    master_resume_id BIGINT NOT NULL REFERENCES original_resume(id) ON DELETE CASCADE,
    job_description_id BIGINT NOT NULL REFERENCES job_descriptions(id) ON DELETE CASCADE,
    content_json JSONB NOT NULL,
    match_score DOUBLE PRECISION NOT NULL DEFAULT 0
        CHECK (match_score >= 0 AND match_score <= 100),
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resume_changes (
    id BIGSERIAL PRIMARY KEY,
    tailored_resume_id BIGINT NOT NULL REFERENCES tailored_resumes(id) ON DELETE CASCADE,
    section TEXT NOT NULL,
    original_text TEXT,
    new_text TEXT NOT NULL,
    source_fact_ids JSONB,
    status TEXT NOT NULL DEFAULT 'pending',
    user_feedback TEXT
);

CREATE TABLE IF NOT EXISTS parsed_resume_documents (
    resume_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS parsed_jd_documents (
    jd_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tailored_resume_documents (
    tailored_resume_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    generated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_original_resume_user_id ON original_resume(user_id);
CREATE INDEX IF NOT EXISTS idx_experience_facts_resume_id ON experience_facts(resume_id);
CREATE INDEX IF NOT EXISTS idx_job_descriptions_user_id ON job_descriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_tailored_resumes_master_resume_id ON tailored_resumes(master_resume_id);
CREATE INDEX IF NOT EXISTS idx_tailored_resumes_job_description_id ON tailored_resumes(job_description_id);
CREATE INDEX IF NOT EXISTS idx_resume_changes_tailored_resume_id ON resume_changes(tailored_resume_id);
CREATE INDEX IF NOT EXISTS idx_tailored_documents_generated_at
    ON tailored_resume_documents(generated_at DESC);
"""


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL persistence.")
    # Some platforms still emit the deprecated postgres:// scheme.
    if database_url.startswith("postgres://"):
        return "postgresql://" + database_url[len("postgres://") :]
    return database_url


def initialize_database(database_url: str | None = None) -> None:
    with psycopg.connect(database_url or get_database_url()) as connection:
        connection.execute(SCHEMA)
        connection.commit()


if __name__ == "__main__":
    initialize_database()
    print("PostgreSQL database initialized.")
