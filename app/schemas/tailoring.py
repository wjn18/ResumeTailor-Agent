from pydantic import BaseModel, Field
from typing import Optional

from app.schemas.job_description import ParsedJD
from app.schemas.resume import ParsedResume


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


class TailoredResumeDraft(BaseModel):
    jd_id: str
    resume_id: str
    headline: Optional[str] = None
    summary: list[TailoredSentence] = Field(default_factory=list)
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


class TailoringBuildResponse(BaseModel):
    match_report: RequirementMatchReport
    draft: TailoredResumeDraft
    fact_check_report: FactCheckReport
