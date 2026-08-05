from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path

from psycopg.types.json import Jsonb

from app.schemas.jds import ParsedJD
from app.schemas.resumes import ParsedResume
from app.schemas.tailoring import (
    FactCheckReport,
    FormalResumeDocument,
    RequirementMatchReport,
    SavedTailoredResume,
    SavedTailoredResumeSummary,
    TailoredResumeDraft,
)
from app.services.database import connect

# Compatibility symbol only. Tailored resume JSON is stored in PostgreSQL.
TAILORED_RESUME_DATA_DIR = Path("app/data/tailored_resumes")


def save_tailored_resume(
    jd: ParsedJD,
    resume: ParsedResume,
    draft: TailoredResumeDraft,
    initial_draft: TailoredResumeDraft | None = None,
    formal_resume: FormalResumeDocument | None = None,
    match_report: RequirementMatchReport | None = None,
    fact_check_report: FactCheckReport | None = None,
    final_fact_check_report: FactCheckReport | None = None,
) -> SavedTailoredResume:
    now = datetime.now(timezone.utc).isoformat()
    saved_resume = SavedTailoredResume(
        tailored_resume_id=f"tailored_{uuid4().hex[:12]}",
        display_name=build_unique_display_name(jd),
        generated_at=now,
        jd_id=jd.jd_id,
        resume_id=resume.resume_id,
        company=jd.company,
        job_title=jd.job_title,
        draft=draft,
        initial_draft=initial_draft,
        formal_resume=formal_resume,
        match_report=match_report,
        fact_check_report=fact_check_report,
        final_fact_check_report=final_fact_check_report,
        status="draft",
        updated_at=now,
    )
    _write_tailored_resume(saved_resume)
    return saved_resume


def load_tailored_resume(tailored_resume_id: str) -> SavedTailoredResume:
    with connect() as connection:
        row = connection.execute(
            "SELECT payload FROM tailored_resume_documents WHERE tailored_resume_id = %s",
            (tailored_resume_id,),
        ).fetchone()
    if row is None:
        raise FileNotFoundError(tailored_resume_id)
    return SavedTailoredResume.model_validate(row["payload"])


def list_tailored_resumes() -> list[SavedTailoredResumeSummary]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM tailored_resume_documents ORDER BY generated_at DESC"
        ).fetchall()
    return [
        SavedTailoredResumeSummary.model_validate({
            key: value
            for key, value in row["payload"].items()
            if key in SavedTailoredResumeSummary.model_fields
        })
        for row in rows
    ]


def build_unique_display_name(jd: ParsedJD) -> str:
    base_name = build_display_name(jd)
    existing_names = {item.display_name for item in list_tailored_resumes()}
    if base_name not in existing_names:
        return base_name
    suffix = 2
    while f"{base_name} {suffix}" in existing_names:
        suffix += 1
    return f"{base_name} {suffix}"


def build_display_name(jd: ParsedJD) -> str:
    company = (jd.company or "未知公司").strip()
    job_title = (jd.job_title or "未知岗位").strip()
    return f"{company}{job_title}简历"


def update_formal_resume(
    tailored_resume_id: str, formal_resume: FormalResumeDocument
) -> SavedTailoredResume:
    saved_resume = load_tailored_resume(tailored_resume_id)
    updated = saved_resume.model_copy(update={
        "formal_resume": formal_resume,
        "status": "draft",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "confirmed_at": None,
        "docx_file_name": None,
    })
    _write_tailored_resume(updated)
    return updated


def mark_tailored_resume_confirmed(
    tailored_resume_id: str,
    formal_resume: FormalResumeDocument,
    docx_file_name: str,
) -> SavedTailoredResume:
    saved_resume = load_tailored_resume(tailored_resume_id)
    now = datetime.now(timezone.utc).isoformat()
    confirmed = saved_resume.model_copy(update={
        "formal_resume": formal_resume,
        "status": "confirmed",
        "updated_at": now,
        "confirmed_at": now,
        "docx_file_name": docx_file_name,
    })
    _write_tailored_resume(confirmed)
    return confirmed


def _write_tailored_resume(saved_resume: SavedTailoredResume) -> None:
    payload = saved_resume.model_dump(mode="json")
    with connect() as connection:
        connection.execute(
            """INSERT INTO tailored_resume_documents
                   (tailored_resume_id, payload, status, generated_at, updated_at)
               VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
               ON CONFLICT (tailored_resume_id) DO UPDATE
               SET payload = EXCLUDED.payload,
                   status = EXCLUDED.status,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                saved_resume.tailored_resume_id,
                Jsonb(payload),
                saved_resume.status,
                saved_resume.generated_at,
            ),
        )
        connection.commit()
