from pathlib import Path
import sqlite3


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "resume_tailor.sqlite3"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    created_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS original_resume (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    parsed_content_json TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS experience_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    fact_text TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    source_location TEXT,
    created_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (resume_id) REFERENCES original_resume(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS job_descriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    company TEXT NOT NULL,
    job_title TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    parsed_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tailored_resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    master_resume_id INTEGER NOT NULL,
    job_description_id INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    match_score REAL NOT NULL DEFAULT 0 CHECK (match_score >= 0 AND match_score <= 100),
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (master_resume_id) REFERENCES original_resume(id) ON DELETE CASCADE,
    FOREIGN KEY (job_description_id) REFERENCES job_descriptions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS resume_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tailored_resume_id INTEGER NOT NULL,
    section TEXT NOT NULL,
    original_text TEXT,
    new_text TEXT NOT NULL,
    source_fact_ids TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    user_feedback TEXT,
    FOREIGN KEY (tailored_resume_id) REFERENCES tailored_resumes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_original_resume_user_id
    ON original_resume(user_id);

CREATE INDEX IF NOT EXISTS idx_experience_facts_resume_id
    ON experience_facts(resume_id);

CREATE INDEX IF NOT EXISTS idx_job_descriptions_user_id
    ON job_descriptions(user_id);

CREATE INDEX IF NOT EXISTS idx_tailored_resumes_master_resume_id
    ON tailored_resumes(master_resume_id);

CREATE INDEX IF NOT EXISTS idx_tailored_resumes_job_description_id
    ON tailored_resumes(job_description_id);

CREATE INDEX IF NOT EXISTS idx_resume_changes_tailored_resume_id
    ON resume_changes(tailored_resume_id);
"""


def initialize_database(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA foreign_keys = ON;")


if __name__ == "__main__":
    initialize_database()
    print(f"SQLite database initialized at: {DB_PATH}")
