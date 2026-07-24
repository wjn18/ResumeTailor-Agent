import json
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from app.schemas.job_description import ParsedJD
from app.schemas.resume import ParsedResume
from app.schemas.tailoring import (
    FactCheckReport,
    RequirementMatchReport,
    SavedTailoredResume,
    SavedTailoredResumeSummary,
    TailoredResumeDraft,
)


TAILORED_RESUME_DATA_DIR = Path("app/data/tailored_resumes")


def save_tailored_resume(
    jd: ParsedJD,
    resume: ParsedResume,
    draft: TailoredResumeDraft,
    match_report: RequirementMatchReport | None = None,
    fact_check_report: FactCheckReport | None = None,
) -> SavedTailoredResume:
    TAILORED_RESUME_DATA_DIR.mkdir(parents=True, exist_ok=True)
    saved_resume = SavedTailoredResume(
        tailored_resume_id=f"tailored_{uuid4().hex[:12]}",
        display_name=build_unique_display_name(jd),
        generated_at=datetime.now(timezone.utc).isoformat(),
        jd_id=jd.jd_id,
        resume_id=resume.resume_id,
        company=jd.company,
        job_title=jd.job_title,
        draft=draft,
        match_report=match_report,
        fact_check_report=fact_check_report,
    )
    file_path = _file_path(saved_resume.tailored_resume_id)

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(saved_resume.model_dump(), file, ensure_ascii=False, indent=2)

    return saved_resume


def load_tailored_resume(tailored_resume_id: str) -> SavedTailoredResume:
    with _file_path(tailored_resume_id).open("r", encoding="utf-8") as file:
        data = json.load(file)
    return SavedTailoredResume.model_validate(data)


def list_tailored_resumes() -> list[SavedTailoredResumeSummary]:
    if not TAILORED_RESUME_DATA_DIR.exists():
        return []

    saved_resumes = []
    for file_path in TAILORED_RESUME_DATA_DIR.glob("tailored_*.json"):
        with file_path.open("r", encoding="utf-8") as file:
            saved_resume = SavedTailoredResume.model_validate(json.load(file))
        saved_resumes.append(
            SavedTailoredResumeSummary(
                tailored_resume_id=saved_resume.tailored_resume_id,
                display_name=saved_resume.display_name,
                generated_at=saved_resume.generated_at,
                jd_id=saved_resume.jd_id,
                resume_id=saved_resume.resume_id,
                company=saved_resume.company,
                job_title=saved_resume.job_title,
            )
        )

    return sorted(saved_resumes, key=lambda resume: resume.generated_at, reverse=True)


def build_unique_display_name(jd: ParsedJD) -> str:
    base_name = build_display_name(jd)
    existing_names = {
        saved_resume.display_name
        for saved_resume in list_tailored_resumes()
    }
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


def _file_path(tailored_resume_id: str) -> Path:
    return TAILORED_RESUME_DATA_DIR / f"{tailored_resume_id}.json"
