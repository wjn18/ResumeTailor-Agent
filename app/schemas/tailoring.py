from pydantic import BaseModel, Field
from typing import Optional

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


class TailoringInitialBuildResponse(BaseModel):
    match_report: RequirementMatchReport
    draft: TailoredResumeDraft
    formal_resume: FormalResumeDocument


class TailoringReviewRequest(BaseModel):
    jd: ParsedJD
    resume: ParsedResume
    match_report: RequirementMatchReport
    draft: TailoredResumeDraft


class TailoringReviewResponse(BaseModel):
    draft: TailoredResumeDraft
    fact_check_report: FactCheckReport
    final_fact_check_report: FactCheckReport
    formal_resume: FormalResumeDocument
    saved_resume: "SavedTailoredResume"


class TailoringBuildResponse(BaseModel):
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
