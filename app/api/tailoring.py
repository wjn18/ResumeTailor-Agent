from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.schemas.jds import ParsedJD
from app.schemas.resumes import ParsedResume
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
    SUPPORTED,
    assemble_formal_resume,
    fact_check_resume,
    match_requirements,
    rewrite_resume,
)

from app.workflows.tailoring_graph import tailoring_result
from app.workflows.tailoring_runtime import (
    TailoringCapacityExceeded,
    TailoringRunBusy,
    TailoringRunNotFound,
    get_tailoring_runtime,
)


router = APIRouter(prefix="/tailoring", tags=["tailoring"])


def _workflow_error(exc):
    if isinstance(exc, TailoringRunNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, TailoringRunBusy):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, TailoringCapacityExceeded):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=400, detail=f"简历生成失败：{exc}")


def _finish_tailoring(runtime, run):
    # The run lock also covers persistence so repeated review requests return
    # the same result instead of creating another saved resume.
    if run.response is not None:
        return TailoringBuildResponse.model_validate(run.response)
    state = runtime.finish(run)
    jd = ParsedJD.model_validate(state["jd"])
    resume = ParsedResume.model_validate(state["resume"])
    match, initial, first_report, revised, final_report = tailoring_result(state)
    formal = assemble_formal_resume(jd, resume, revised)
    saved = None
    if state["status"] == "awaiting_confirmation":
        saved = save_tailored_resume(
            jd, resume, revised,
            initial_draft=initial,
            formal_resume=formal,
            match_report=match,
            fact_check_report=first_report,
            final_fact_check_report=final_report,
        )
    response = TailoringBuildResponse(
        thread_id=run.thread_id,
        status=state["status"],
        revision_count=state["revision_count"],
        match_report=match,
        initial_draft=initial,
        draft=revised,
        fact_check_report=first_report,
        final_fact_check_report=final_report,
        formal_resume=formal,
        saved_resume=saved,
    )
    run.response = response.model_dump(mode="json")
    return response


@router.post("/build/initial", response_model=TailoringInitialBuildResponse)
def build_initial_resume_for_jd(payload: TailoringBuildRequest):
    runtime = get_tailoring_runtime()
    try:
        with runtime.create() as run:
            state = runtime.start(run, payload.jd, payload.resume, initial_only=True)
            draft = TailoredResumeDraft.model_validate(state["initial_draft"])
            return TailoringInitialBuildResponse(
                thread_id=run.thread_id,
                match_report=RequirementMatchReport.model_validate(state["match_report"]),
                draft=draft,
                formal_resume=assemble_formal_resume(payload.jd, payload.resume, draft),
            )
    except (RuntimeError, ValueError) as exc:
        raise _workflow_error(exc) from exc


@router.post("/build/review", response_model=TailoringReviewResponse)
def review_initial_resume(payload: TailoringReviewRequest):
    runtime = get_tailoring_runtime()
    try:
        with runtime.acquire(payload.thread_id) as run:
            return _finish_tailoring(runtime, run)
    except (RuntimeError, ValueError, TailoringRunNotFound) as exc:
        raise _workflow_error(exc) from exc


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
    runtime = get_tailoring_runtime()
    try:
        with runtime.create() as run:
            runtime.start(run, payload.jd, payload.resume)
            return _finish_tailoring(runtime, run)
    except (RuntimeError, ValueError) as exc:
        raise _workflow_error(exc) from exc


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
        saved = load_tailored_resume(tailored_resume_id)
        if saved.final_fact_check_report and any(
            check.support_status != SUPPORTED
            for check in saved.final_fact_check_report.checks
        ):
            raise HTTPException(status_code=409, detail="简历仍有未通过事实审核的内容，请重新生成。")
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
