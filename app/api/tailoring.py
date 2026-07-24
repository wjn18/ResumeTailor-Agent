from fastapi import APIRouter, HTTPException

from app.schemas.tailoring import (
    FactCheckRequest,
    FactCheckReport,
    MatchRequest,
    RequirementMatchReport,
    RewriteRequest,
    SaveTailoredResumeRequest,
    SavedTailoredResume,
    SavedTailoredResumeSummary,
    TailoredResumeDraft,
    TailoringBuildRequest,
    TailoringBuildResponse,
)
from app.services.tailored_resume_storage import (
    list_tailored_resumes,
    load_tailored_resume,
    save_tailored_resume,
)
from app.services.tailoring import (
    build_tailored_resume,
    fact_check_resume,
    match_requirements,
    rewrite_resume,
)


router = APIRouter(prefix="/tailoring", tags=["tailoring"])


@router.post("/match", response_model=RequirementMatchReport)
def match_jd_requirements(payload: MatchRequest):
    try:
        return match_requirements(payload.jd, payload.resume)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rewrite", response_model=TailoredResumeDraft)
def rewrite_resume_for_jd(payload: RewriteRequest):
    try:
        return rewrite_resume(payload.jd, payload.resume, payload.match_report)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/fact-check", response_model=FactCheckReport)
def fact_check_tailored_resume(payload: FactCheckRequest):
    try:
        return fact_check_resume(payload.jd_id, payload.resume, payload.draft)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/build", response_model=TailoringBuildResponse)
def build_resume_for_jd(payload: TailoringBuildRequest):
    try:
        match_report, draft, fact_check_report = build_tailored_resume(
            payload.jd,
            payload.resume,
        )
        saved_resume = save_tailored_resume(
            payload.jd,
            payload.resume,
            draft,
            match_report=match_report,
            fact_check_report=fact_check_report,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TailoringBuildResponse(
        match_report=match_report,
        draft=draft,
        fact_check_report=fact_check_report,
        saved_resume=saved_resume,
    )


@router.post("/save", response_model=SavedTailoredResume)
def save_rewritten_resume(payload: SaveTailoredResumeRequest):
    try:
        return save_tailored_resume(
            payload.jd,
            payload.resume,
            payload.draft,
            match_report=payload.match_report,
            fact_check_report=payload.fact_check_report,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/saved", response_model=list[SavedTailoredResumeSummary])
def list_saved_tailored_resumes():
    return list_tailored_resumes()


@router.get("/saved/{tailored_resume_id}", response_model=SavedTailoredResume)
def get_saved_tailored_resume(tailored_resume_id: str):
    try:
        return load_tailored_resume(tailored_resume_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tailored resume not found.") from exc
