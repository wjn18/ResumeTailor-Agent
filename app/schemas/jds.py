from datetime import datetime

from pydantic import BaseModel, Field
from typing import Optional


class JDRequirement(BaseModel):
    requirement_id: str
    category: str
    requirement_text: str
    priority: str = "must_have"
    keywords: list[str] = Field(default_factory=list)


class ParsedJD(BaseModel):
    jd_id: str
    company: Optional[str] = None
    job_title: Optional[str] = None
    raw_text_length: int
    summary: Optional[str] = None
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    tools_and_technologies: list[str] = Field(default_factory=list)
    experience_years: Optional[str] = None
    education_requirements: list[str] = Field(default_factory=list)
    requirements: list[JDRequirement] = Field(default_factory=list)


class JDParseRequest(BaseModel):
    raw_text: str
    company: Optional[str] = None
    job_title: Optional[str] = None


class JDURLExtractRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class ExtractedJDText(BaseModel):
    source_url: str
    final_url: str
    page_title: Optional[str] = None
    raw_text: str
    text_length: int
    extraction_method: str
    fetched_at: datetime
