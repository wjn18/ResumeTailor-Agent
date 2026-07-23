from pydantic import BaseModel, Field
from typing import Optional


class UserCreate(BaseModel):
    name: str
    email: str


class UserPatch(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


class UserRead(UserCreate):
    id: int
    created_time: str


class OriginalResumeCreate(BaseModel):
    user_id: int
    file_path: str
    parsed_content_json: Optional[str] = None
    version: int = 1


class OriginalResumePatch(BaseModel):
    user_id: Optional[int] = None
    file_path: Optional[str] = None
    parsed_content_json: Optional[str] = None
    version: Optional[int] = None


class OriginalResumeRead(OriginalResumeCreate):
    id: int
    created_time: str


class ExperienceFactCreate(BaseModel):
    resume_id: int
    category: str
    entity_name: str
    fact_text: str
    verified: bool = False
    source_location: Optional[str] = None


class ExperienceFactPatch(BaseModel):
    resume_id: Optional[int] = None
    category: Optional[str] = None
    entity_name: Optional[str] = None
    fact_text: Optional[str] = None
    verified: Optional[bool] = None
    source_location: Optional[str] = None


class ExperienceFactRead(ExperienceFactCreate):
    id: int
    created_time: str


class JobDescriptionCreate(BaseModel):
    user_id: int
    company: str
    job_title: str
    raw_text: str
    parsed_json: Optional[str] = None


class JobDescriptionPatch(BaseModel):
    user_id: Optional[int] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    raw_text: Optional[str] = None
    parsed_json: Optional[str] = None


class JobDescriptionRead(JobDescriptionCreate):
    id: int
    created_at: str


class TailoredResumeCreate(BaseModel):
    master_resume_id: int
    job_description_id: int
    content_json: str
    match_score: float = Field(default=0, ge=0, le=100)
    status: str = "draft"


class TailoredResumePatch(BaseModel):
    master_resume_id: Optional[int] = None
    job_description_id: Optional[int] = None
    content_json: Optional[str] = None
    match_score: Optional[float] = Field(default=None, ge=0, le=100)
    status: Optional[str] = None


class TailoredResumeRead(TailoredResumeCreate):
    id: int
    created_at: str


class ResumeChangeCreate(BaseModel):
    tailored_resume_id: int
    section: str
    original_text: Optional[str] = None
    new_text: str
    source_fact_ids: Optional[str] = None
    status: str = "pending"
    user_feedback: Optional[str] = None


class ResumeChangePatch(BaseModel):
    tailored_resume_id: Optional[int] = None
    section: Optional[str] = None
    original_text: Optional[str] = None
    new_text: Optional[str] = None
    source_fact_ids: Optional[str] = None
    status: Optional[str] = None
    user_feedback: Optional[str] = None


class ResumeChangeRead(ResumeChangeCreate):
    id: int
