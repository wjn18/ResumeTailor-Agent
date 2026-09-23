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
    TailoringTaskResponse,
    TailoringTaskCreateRequest,
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

from app.storage.workflow_base import RunBusy, RunConflict, RunNotFound
from app.workflows.tailoring_runtime import get_tailoring_runtime


router = APIRouter(prefix="/tailoring", tags=["tailoring"])


def _workflow_error(exc):
    if isinstance(exc, RunNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (RunBusy, RunConflict)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=f"简历生成失败：{exc}")


def _checked_result(job):
    if job["status"] == "failed":
        raise HTTPException(status_code=400, detail={
            "message": job["error"], "thread_id": job["thread_id"],
        })
    if job["status"] in {"cancelled", "cancelling"}:
        raise HTTPException(status_code=409, detail="任务已取消。")
    return job


def _task_response(job):
    # Restore the latest saved edits rather than an old generation snapshot.
    result = job.get("result")
    if result and result.get("saved_resume"):
        saved = load_tailored_resume(result["saved_resume"]["tailored_resume_id"])
        result = {**result, "saved_resume": saved.model_dump(mode="json"), "formal_resume": saved.formal_resume}
        job = {**job, "result": result}
        if saved.status == "confirmed":
            job["status"] = "completed"
    return TailoringTaskResponse.model_validate(job)


@router.post("/tasks", response_model=TailoringTaskResponse, status_code=202)
def create_tailoring_task(payload: TailoringTaskCreateRequest):
    try:
        return _task_response(get_tailoring_runtime().submit(
            payload.jd, payload.resume, request_id=payload.request_id,
        ))
    except RunConflict as exc:
        raise _workflow_error(exc) from exc


@router.get("/tasks/{thread_id}", response_model=TailoringTaskResponse)
def get_tailoring_task(thread_id: str):
    try:
        return _task_response(get_tailoring_runtime().get(thread_id))
    except RunNotFound as exc:
        raise _workflow_error(exc) from exc


@router.post("/tasks/{thread_id}/resume", response_model=TailoringTaskResponse, status_code=202)
def resume_tailoring_task(thread_id: str):
    try:
        return _task_response(get_tailoring_runtime().resume(thread_id))
    except (RunNotFound, RunConflict) as exc:
        raise _workflow_error(exc) from exc


@router.post("/tasks/{thread_id}/cancel", response_model=TailoringTaskResponse, status_code=202)
def cancel_tailoring_task(thread_id: str):
    try:
        return _task_response(get_tailoring_runtime().cancel(thread_id))
    except (RunNotFound, RunConflict) as exc:
        raise _workflow_error(exc) from exc


@router.post("/build/initial", response_model=TailoringInitialBuildResponse)
def build_initial_resume_for_jd(payload: TailoringBuildRequest):
    runtime = get_tailoring_runtime()
    try:
        job = runtime.submit(payload.jd, payload.resume, initial_only=True)
        return _checked_result(runtime.execute(job["thread_id"], wait=True))["preview"]
    except (RuntimeError, ValueError) as exc:
        raise _workflow_error(exc) from exc


@router.post("/build/review", response_model=TailoringReviewResponse)
def review_initial_resume(payload: TailoringReviewRequest):
    runtime = get_tailoring_runtime()
    try:
        runtime.resume(payload.thread_id)
        return _checked_result(runtime.execute(payload.thread_id, wait=True))["result"]
    except (RuntimeError, ValueError, RunNotFound) as exc:
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
        job = runtime.submit(payload.jd, payload.resume)
        return _checked_result(runtime.execute(job["thread_id"], wait=True))["result"]
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
