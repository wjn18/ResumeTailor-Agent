from pydantic import BaseModel, Field, field_validator
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
    proficiency: str = "了解"
    category: Optional[str] = None
    evidence_fact_ids: list[str] = Field(default_factory=list)

    @field_validator("proficiency", mode="before")
    @classmethod
    def normalize_proficiency(cls, value) -> str:
        if not isinstance(value, str):
            return "了解"

        normalized = value.strip()
        aliases = {
            "入门": "了解",
            "基础": "了解",
            "掌握": "熟悉",
            "较熟悉": "熟悉",
            "较为熟悉": "熟悉",
            "熟练掌握": "熟练",
            "高级": "熟练",
            "专家": "精通",
        }
        normalized = aliases.get(normalized, normalized)
        return normalized if normalized in {"了解", "熟悉", "熟练", "精通"} else "了解"


class ExperienceFact(BaseModel):
    fact_id: str
    category: str
    entity_name: str
    fact_text: str
    verified: bool = True
    source_location: Optional[str] = None


class WorkExperience(BaseModel):
    work_experience_id: str
    company: str
    job_title: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    facts: list[ExperienceFact] = Field(default_factory=list)


class HonorAward(BaseModel):
    honor_award_id: str
    name: str
    issuer: Optional[str] = None
    date: Optional[str] = None
    facts: list[ExperienceFact] = Field(default_factory=list)


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
    work_experiences: list[WorkExperience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    honor_awards: list[HonorAward] = Field(default_factory=list)
    experience_facts: list[ExperienceFact] = Field(default_factory=list)


class UserFactTextParseRequest(BaseModel):
    text: str = Field(min_length=1)
    resume_id: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    source_name: str = Field(default="user_input", min_length=1, max_length=100)
