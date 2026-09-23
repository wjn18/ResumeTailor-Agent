from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal, Optional
from uuid import UUID

from app.schemas.jds import ParsedJD
from app.schemas.resumes import ParsedResume


class RequirementMatch(BaseModel):
    requirement_id: str
    requirement_text: str
    match_status: str
    matched_fact_ids: list[str] = Field(default_factory=list)
    reasoning: str


class RequirementMatchReport(BaseModel):
    jd_id: str
    resume_id: str
    matches: list[RequirementMatch] = Field(default_factory=list)


class TailoredSentence(BaseModel):
    section: str
    sentence: str
    source_fact_ids: list[str]


class TailoredWorkExperience(BaseModel):
    work_experience_id: str
    bullets: list[TailoredSentence] = Field(default_factory=list)


class TailoredHonorAward(BaseModel):
    honor_award_id: str
    bullets: list[TailoredSentence] = Field(default_factory=list)


class TailoredResumeDraft(BaseModel):
    jd_id: str
    resume_id: str
    headline: Optional[str] = None
    summary: list[TailoredSentence] = Field(default_factory=list)
    work_experiences: list[TailoredWorkExperience] = Field(default_factory=list)
    honor_awards: list[TailoredHonorAward] = Field(default_factory=list)
    # Kept for historical draft JSON compatibility.
    experience: list[TailoredSentence] = Field(default_factory=list)
    skills: list[TailoredSentence] = Field(default_factory=list)


class SentenceFactCheck(BaseModel):
    section: str
    sentence: str
    source_fact_ids: list[str]
    support_status: str
    issue: Optional[str] = None
    suggestion: Optional[str] = None


class FactCheckReport(BaseModel):
    jd_id: str
    resume_id: str
    checks: list[SentenceFactCheck] = Field(default_factory=list)


class FormalEducation(BaseModel):
    school: str
    degree: Optional[str] = None
    major: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class FormalPersonalContact(BaseModel):
    contact_type: str
    contact_value: str
    label: Optional[str] = None


class FormalProject(BaseModel):
    name: str
    role: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    technologies: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class FormalWorkExperience(BaseModel):
    company: str
    job_title: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)


class FormalHonorAward(BaseModel):
    name: str
    issuer: Optional[str] = None
    date: Optional[str] = None
    bullets: list[str] = Field(default_factory=list)


class FormalResumeDocument(BaseModel):
    name: Optional[str] = None
    headline: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    personal_contacts: list[FormalPersonalContact] = Field(default_factory=list)
    education_experiences: list[FormalEducation] = Field(default_factory=list)
    advantages: list[str] = Field(default_factory=list, max_length=6)
    work_experiences: list[FormalWorkExperience] = Field(default_factory=list)
    honor_awards: list[FormalHonorAward] = Field(default_factory=list)
    related_skills: list[str] = Field(default_factory=list)
    # Kept so previously generated JSON files remain readable.
    summary: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    education: list[FormalEducation] = Field(default_factory=list)
    projects: list[FormalProject] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class MatchRequest(BaseModel):
    jd: ParsedJD
    resume: ParsedResume


class RewriteRequest(BaseModel):
    jd: ParsedJD
    resume: ParsedResume
    match_report: Optional[RequirementMatchReport] = None


class FactCheckRequest(BaseModel):
    jd_id: str
    resume: ParsedResume
    draft: TailoredResumeDraft


class TailoringBuildRequest(BaseModel):
    jd: ParsedJD
    resume: ParsedResume


class TailoringTaskCreateRequest(TailoringBuildRequest):
    request_id: Optional[UUID] = None


class TailoringInitialBuildResponse(BaseModel):
    thread_id: str
    status: Literal["initial_ready"] = "initial_ready"
    match_report: RequirementMatchReport
    draft: TailoredResumeDraft
    formal_resume: FormalResumeDocument


class TailoringReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(pattern=r"^tailoring_[0-9a-f]{32}$")


class TailoringReviewResponse(BaseModel):
    thread_id: str
    status: Literal["awaiting_confirmation", "needs_attention", "completed"]
    revision_count: int
    draft: TailoredResumeDraft
    fact_check_report: FactCheckReport
    final_fact_check_report: FactCheckReport
    formal_resume: FormalResumeDocument
    saved_resume: Optional["SavedTailoredResume"] = None


class TailoringBuildResponse(BaseModel):
    thread_id: str
    status: Literal["awaiting_confirmation", "needs_attention", "completed"]
    revision_count: int
    match_report: RequirementMatchReport
    draft: TailoredResumeDraft
    fact_check_report: FactCheckReport
    initial_draft: Optional[TailoredResumeDraft] = None
    final_fact_check_report: Optional[FactCheckReport] = None
    formal_resume: Optional[FormalResumeDocument] = None
    saved_resume: Optional["SavedTailoredResume"] = None


class SavedTailoredResume(BaseModel):
    tailored_resume_id: str
    display_name: str
    generated_at: str
    jd_id: str
    resume_id: str
    company: Optional[str] = None
    job_title: Optional[str] = None
    draft: TailoredResumeDraft
    initial_draft: Optional[TailoredResumeDraft] = None
    formal_resume: Optional[FormalResumeDocument] = None
    match_report: Optional[RequirementMatchReport] = None
    fact_check_report: Optional[FactCheckReport] = None
    final_fact_check_report: Optional[FactCheckReport] = None
    status: str = "draft"
    updated_at: Optional[str] = None
    confirmed_at: Optional[str] = None
    docx_file_name: Optional[str] = None
    workflow_thread_id: Optional[str] = None
    content_version: int = 0
    reviewed_content_version: int = 0
    content_hash: Optional[str] = None
    reviewed_content_hash: Optional[str] = None


class SavedTailoredResumeSummary(BaseModel):
    tailored_resume_id: str
    display_name: str
    generated_at: str
    jd_id: str
    resume_id: str
    company: Optional[str] = None
    job_title: Optional[str] = None
    status: str = "draft"
    docx_file_name: Optional[str] = None


class SaveTailoredResumeRequest(BaseModel):
    jd: ParsedJD
    resume: ParsedResume
    draft: TailoredResumeDraft
    initial_draft: Optional[TailoredResumeDraft] = None
    formal_resume: Optional[FormalResumeDocument] = None
    match_report: Optional[RequirementMatchReport] = None
    fact_check_report: Optional[FactCheckReport] = None
    final_fact_check_report: Optional[FactCheckReport] = None


class FormalResumeUpdateRequest(BaseModel):
    formal_resume: FormalResumeDocument


class ConfirmTailoredResumeRequest(BaseModel):
    formal_resume: FormalResumeDocument


class TailoringDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    action: Literal["edit", "confirm"]
    expected_version: int = Field(ge=1)
    formal_resume: Optional[FormalResumeDocument] = None

    @model_validator(mode="after")
    def validate_action(self):
        if (self.action == "edit") != (self.formal_resume is not None):
            raise ValueError("编辑必须提供正文；确认只能引用已审核的版本。")
        return self


class TailoringTaskResponse(BaseModel):
    thread_id: str
    status: Literal[
        "queued", "running", "saving", "initial_ready", "awaiting_confirmation",
        "needs_attention", "failed", "cancelling", "cancelled", "completed",
    ]
    target: Literal["initial", "full"]
    created_at: str
    updated_at: str
    graph_version: int
    input_hash: str
    current_node: Optional[str] = None
    failed_node: Optional[str] = None
    error: Optional[str] = None
    revision_count: int = 0
    draft_version: int = 0
    audit_version: int = 0
    content_version: int = 0
    reviewed_content_version: int = 0
    content_hash: Optional[str] = None
    reviewed_content_hash: Optional[str] = None
    pending_decision: Optional[dict] = None
    current_document: Optional[FormalResumeDocument] = None
    preview: Optional[TailoringInitialBuildResponse] = None
    result: Optional[TailoringBuildResponse] = None
