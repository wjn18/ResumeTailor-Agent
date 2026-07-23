from fastapi import APIRouter, HTTPException, Query, Response, status
import sqlite3
from typing import TypeVar

from pydantic import BaseModel

from app.schemas.database import (
    ExperienceFactCreate,
    ExperienceFactPatch,
    ExperienceFactRead,
    JobDescriptionCreate,
    JobDescriptionPatch,
    JobDescriptionRead,
    OriginalResumeCreate,
    OriginalResumePatch,
    OriginalResumeRead,
    ResumeChangeCreate,
    ResumeChangePatch,
    ResumeChangeRead,
    TailoredResumeCreate,
    TailoredResumePatch,
    TailoredResumeRead,
    UserCreate,
    UserPatch,
    UserRead,
)
from app.services.database import create_row, delete_row, get_row, list_rows, patch_row


router = APIRouter(prefix="/crud", tags=["crud"])
CreateModel = TypeVar("CreateModel", bound=BaseModel)
PatchModel = TypeVar("PatchModel", bound=BaseModel)


def _list(table_name: str, limit: int, offset: int):
    return list_rows(table_name, limit=limit, offset=offset)


def _get(table_name: str, item_id: int):
    row = get_row(table_name, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    return row


def _create(table_name: str, payload: CreateModel):
    try:
        return create_row(table_name, payload.model_dump(exclude_unset=True))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _patch(table_name: str, item_id: int, payload: PatchModel):
    try:
        row = patch_row(table_name, item_id, payload.model_dump(exclude_unset=True))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if row is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    return row


def _delete(table_name: str, item_id: int, response: Response):
    if not delete_row(table_name, item_id):
        raise HTTPException(status_code=404, detail="Item not found.")
    response.status_code = status.HTTP_204_NO_CONTENT
    return None


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate):
    return _create("users", payload)


@router.get("/users", response_model=list[UserRead])
def list_users(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return _list("users", limit, offset)


@router.get("/users/{item_id}", response_model=UserRead)
def get_user(item_id: int):
    return _get("users", item_id)


@router.patch("/users/{item_id}", response_model=UserRead)
def patch_user(item_id: int, payload: UserPatch):
    return _patch("users", item_id, payload)


@router.delete("/users/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(item_id: int, response: Response):
    return _delete("users", item_id, response)


@router.post(
    "/original-resumes",
    response_model=OriginalResumeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_original_resume(payload: OriginalResumeCreate):
    return _create("original_resume", payload)


@router.get("/original-resumes", response_model=list[OriginalResumeRead])
def list_original_resumes(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return _list("original_resume", limit, offset)


@router.get("/original-resumes/{item_id}", response_model=OriginalResumeRead)
def get_original_resume(item_id: int):
    return _get("original_resume", item_id)


@router.patch("/original-resumes/{item_id}", response_model=OriginalResumeRead)
def patch_original_resume(item_id: int, payload: OriginalResumePatch):
    return _patch("original_resume", item_id, payload)


@router.delete("/original-resumes/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_original_resume(item_id: int, response: Response):
    return _delete("original_resume", item_id, response)


@router.post(
    "/experience-facts",
    response_model=ExperienceFactRead,
    status_code=status.HTTP_201_CREATED,
)
def create_experience_fact(payload: ExperienceFactCreate):
    return _create("experience_facts", payload)


@router.get("/experience-facts", response_model=list[ExperienceFactRead])
def list_experience_facts(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return _list("experience_facts", limit, offset)


@router.get("/experience-facts/{item_id}", response_model=ExperienceFactRead)
def get_experience_fact(item_id: int):
    return _get("experience_facts", item_id)


@router.patch("/experience-facts/{item_id}", response_model=ExperienceFactRead)
def patch_experience_fact(item_id: int, payload: ExperienceFactPatch):
    return _patch("experience_facts", item_id, payload)


@router.delete("/experience-facts/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experience_fact(item_id: int, response: Response):
    return _delete("experience_facts", item_id, response)


@router.post(
    "/job-descriptions",
    response_model=JobDescriptionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_job_description(payload: JobDescriptionCreate):
    return _create("job_descriptions", payload)


@router.get("/job-descriptions", response_model=list[JobDescriptionRead])
def list_job_descriptions(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return _list("job_descriptions", limit, offset)


@router.get("/job-descriptions/{item_id}", response_model=JobDescriptionRead)
def get_job_description(item_id: int):
    return _get("job_descriptions", item_id)


@router.patch("/job-descriptions/{item_id}", response_model=JobDescriptionRead)
def patch_job_description(item_id: int, payload: JobDescriptionPatch):
    return _patch("job_descriptions", item_id, payload)


@router.delete("/job-descriptions/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job_description(item_id: int, response: Response):
    return _delete("job_descriptions", item_id, response)


@router.post(
    "/tailored-resumes",
    response_model=TailoredResumeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_tailored_resume(payload: TailoredResumeCreate):
    return _create("tailored_resumes", payload)


@router.get("/tailored-resumes", response_model=list[TailoredResumeRead])
def list_tailored_resumes(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return _list("tailored_resumes", limit, offset)


@router.get("/tailored-resumes/{item_id}", response_model=TailoredResumeRead)
def get_tailored_resume(item_id: int):
    return _get("tailored_resumes", item_id)


@router.patch("/tailored-resumes/{item_id}", response_model=TailoredResumeRead)
def patch_tailored_resume(item_id: int, payload: TailoredResumePatch):
    return _patch("tailored_resumes", item_id, payload)


@router.delete("/tailored-resumes/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tailored_resume(item_id: int, response: Response):
    return _delete("tailored_resumes", item_id, response)


@router.post(
    "/resume-changes",
    response_model=ResumeChangeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_resume_change(payload: ResumeChangeCreate):
    return _create("resume_changes", payload)


@router.get("/resume-changes", response_model=list[ResumeChangeRead])
def list_resume_changes(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return _list("resume_changes", limit, offset)


@router.get("/resume-changes/{item_id}", response_model=ResumeChangeRead)
def get_resume_change(item_id: int):
    return _get("resume_changes", item_id)


@router.patch("/resume-changes/{item_id}", response_model=ResumeChangeRead)
def patch_resume_change(item_id: int, payload: ResumeChangePatch):
    return _patch("resume_changes", item_id, payload)


@router.delete("/resume-changes/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resume_change(item_id: int, response: Response):
    return _delete("resume_changes", item_id, response)
