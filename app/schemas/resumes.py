from pydantic import BaseModel, Field
from typing import Optional


class SourceDocument(BaseModel):
    file_name: str
    file_type: str
    text_length: int


class Education(BaseModel):
    school: str
    degree: Optional[str] = None
    major: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class Skill(BaseModel):
    name: str
    category: Optional[str] = None


class ExperienceFact(BaseModel):
    fact_id: str
    category: str
    entity_name: str
    fact_text: str
    verified: bool = True
    source_location: Optional[str] = None


class Project(BaseModel):
    project_id: str
    name: str
    role: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    technologies: list[str] = Field(default_factory=list)
    facts: list[ExperienceFact] = Field(default_factory=list)


class ParsedResume(BaseModel):
    resume_id: str
    source_document: Optional[SourceDocument] = None
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    education: list[Education] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    experience_facts: list[ExperienceFact] = Field(default_factory=list)


class UserFactTextParseRequest(BaseModel):
    text: str = Field(min_length=1)
    resume_id: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    source_name: str = Field(default="user_input", min_length=1, max_length=100)
