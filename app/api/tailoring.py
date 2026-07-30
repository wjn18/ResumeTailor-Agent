from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.schemas.tailoring import (
    ConfirmTailoredResumeRequest,
    FactCheckRequest,
    FactCheckReport,
    FormalResumeUpdateRequest,
    MatchRequest,
    RequirementMatchReport,
    RewriteRequest,
    SaveTailoredResumeRequest,
    SavedTailoredResume,
    SavedTailoredResumeSummary,
    TailoredResumeDraft,
    TailoringBuildRequest,
    TailoringBuildResponse,
    TailoringInitialBuildResponse,
    TailoringReviewRequest,
    TailoringReviewResponse,
)
from app.services.tailored_resume_storage import (
    list_tailored_resumes,
    load_tailored_resume,
    mark_tailored_resume_confirmed,
    save_tailored_resume,
    update_formal_resume,
)
from app.services.docx_export import export_formal_resume_docx
from app.services.tailoring import (
    assemble_formal_resume,
    build_initial_tailored_resume,
    build_tailored_resume,
    fact_check_resume,
    match_requirements,
    review_tailored_resume,
    rewrite_resume,
)


router = APIRouter(prefix="/tailoring", tags=["tailoring"])


@router.post(
    "/build/initial",
    response_model=TailoringInitialBuildResponse,
)
def build_initial_resume_for_jd(payload: TailoringBuildRequest):
    try:
        match_report, initial_draft = build_initial_tailored_resume(
            payload.jd,
            payload.resume,
        )
        formal_resume = assemble_formal_resume(
            payload.jd,
            payload.resume,
            initial_draft,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"初稿生成失败：{exc}",
        ) from exc

    return TailoringInitialBuildResponse(
        match_report=match_report,
        draft=initial_draft,
        formal_resume=formal_resume,
    )


@router.post(
    "/build/review",
    response_model=TailoringReviewResponse,
)
def review_initial_resume(payload: TailoringReviewRequest):
    try:
        (
            fact_check_report,
            revised_draft,
            final_fact_check_report,
        ) = review_tailored_resume(
            payload.jd,
            payload.resume,
            payload.match_report,
            payload.draft,
        )
        formal_resume = assemble_formal_resume(
            payload.jd,
            payload.resume,
            revised_draft,
        )
        saved_resume = save_tailored_resume(
            payload.jd,
            payload.resume,
            revised_draft,
            initial_draft=payload.draft,
            formal_resume=formal_resume,
            match_report=payload.match_report,
            fact_check_report=fact_check_report,
            final_fact_check_report=final_fact_check_report,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"简历审核失败：{exc}",
        ) from exc

    return TailoringReviewResponse(
        draft=revised_draft,
        fact_check_report=fact_check_report,
        final_fact_check_report=final_fact_check_report,
        formal_resume=formal_resume,
        saved_resume=saved_resume,
    )


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
        (
            match_report,
            initial_draft,
            fact_check_report,
            revised_draft,
            final_fact_check_report,
        ) = build_tailored_resume(
            payload.jd,
            payload.resume,
        )
        formal_resume = assemble_formal_resume(
            payload.jd,
            payload.resume,
            revised_draft,
        )
        saved_resume = save_tailored_resume(
            payload.jd,
            payload.resume,
            revised_draft,
            initial_draft=initial_draft,
            formal_resume=formal_resume,
            match_report=match_report,
            fact_check_report=fact_check_report,
            final_fact_check_report=final_fact_check_report,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return TailoringBuildResponse(
        match_report=match_report,
        draft=revised_draft,
        fact_check_report=fact_check_report,
        initial_draft=initial_draft,
        final_fact_check_report=final_fact_check_report,
        formal_resume=formal_resume,
        saved_resume=saved_resume,
    )


@router.post("/save", response_model=SavedTailoredResume)
def save_rewritten_resume(payload: SaveTailoredResumeRequest):
    try:
        formal_resume = payload.formal_resume or assemble_formal_resume(
            payload.jd,
            payload.resume,
            payload.draft,
        )
        return save_tailored_resume(
            payload.jd,
            payload.resume,
            payload.draft,
            initial_draft=payload.initial_draft,
            formal_resume=formal_resume,
            match_report=payload.match_report,
            fact_check_report=payload.fact_check_report,
            final_fact_check_report=payload.final_fact_check_report,
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


@router.patch(
    "/saved/{tailored_resume_id}/content",
    response_model=SavedTailoredResume,
)
def update_saved_resume_content(
    tailored_resume_id: str,
    payload: FormalResumeUpdateRequest,
):
    try:
        return update_formal_resume(
            tailored_resume_id,
            payload.formal_resume,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tailored resume not found.") from exc


@router.post(
    "/saved/{tailored_resume_id}/confirm",
    response_model=SavedTailoredResume,
)
def confirm_saved_resume(
    tailored_resume_id: str,
    payload: ConfirmTailoredResumeRequest,
):
    try:
        load_tailored_resume(tailored_resume_id)
        docx_path = export_formal_resume_docx(
            tailored_resume_id,
            payload.formal_resume,
        )
        return mark_tailored_resume_confirmed(
            tailored_resume_id,
            payload.formal_resume,
            docx_path.name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tailored resume not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/saved/{tailored_resume_id}/docx")
def download_saved_resume_docx(tailored_resume_id: str):
    try:
        saved_resume = load_tailored_resume(tailored_resume_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tailored resume not found.") from exc

    if saved_resume.status != "confirmed" or not saved_resume.docx_file_name:
        raise HTTPException(
            status_code=409,
            detail="Confirm the tailored resume before downloading DOCX.",
        )
    if saved_resume.formal_resume is None:
        raise HTTPException(
            status_code=409,
            detail="The saved record does not contain formal resume content.",
        )

    docx_path = export_formal_resume_docx(
        tailored_resume_id,
        saved_resume.formal_resume,
    )
    return FileResponse(
        path=docx_path,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        filename=f"{saved_resume.display_name}.docx",
    )
