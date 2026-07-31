from pydantic import BaseModel, Field, field_validator, model_validator
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


class EducationExperience(BaseModel):
    education_experience_id: str
    school: str
    degree: Optional[str] = None
    major: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    facts: list["ExperienceFact"] = Field(default_factory=list)


class PersonalContact(BaseModel):
    personal_contact_id: str
    contact_type: str
    contact_value: str
    label: Optional[str] = None
    facts: list["ExperienceFact"] = Field(default_factory=list)


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
    personal_contacts: list[PersonalContact] = Field(default_factory=list)
    education_experiences: list[EducationExperience] = Field(
        default_factory=list
    )
    # Historical fields retained for existing parsed-resume JSON files.
    education: list[Education] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    work_experiences: list[WorkExperience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    honor_awards: list[HonorAward] = Field(default_factory=list)
    experience_facts: list[ExperienceFact] = Field(default_factory=list)

    @model_validator(mode="after")
    def populate_structured_profile_facts(self):
        existing_fact_ids = {
            fact.fact_id
            for fact in self.experience_facts
        }
        for collection in (
            self.work_experiences,
            self.projects,
            self.honor_awards,
            self.education_experiences,
            self.personal_contacts,
        ):
            for item in collection:
                existing_fact_ids.update(fact.fact_id for fact in item.facts)

        if not self.education_experiences and self.education:
            self.education_experiences = [
                _legacy_education_experience(
                    education,
                    index,
                    existing_fact_ids,
                )
                for index, education in enumerate(self.education, start=1)
            ]

        if not self.personal_contacts:
            legacy_contacts = [
                ("email", "邮箱", self.email),
                ("phone", "电话", self.phone),
            ]
            self.personal_contacts = [
                _legacy_personal_contact(
                    contact_type,
                    label,
                    value,
                    index,
                    existing_fact_ids,
                )
                for index, (contact_type, label, value) in enumerate(
                    (
                        item
                        for item in legacy_contacts
                        if item[2] and item[2].strip()
                    ),
                    start=1,
                )
            ]

        if not self.education and self.education_experiences:
            self.education = [
                Education(
                    school=item.school,
                    degree=item.degree,
                    major=item.major,
                    start_date=item.start_date,
                    end_date=item.end_date,
                )
                for item in self.education_experiences
            ]
        if not self.email:
            self.email = _first_contact_value(self.personal_contacts, "email")
        if not self.phone:
            self.phone = _first_contact_value(self.personal_contacts, "phone")
        return self


class UserFactTextParseRequest(BaseModel):
    text: str = Field(min_length=1)
    resume_id: Optional[str] = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    source_name: str = Field(default="user_input", min_length=1, max_length=100)


def _legacy_education_experience(
    education: Education,
    index: int,
    existing_fact_ids: set[str],
) -> EducationExperience:
    fact_id = _unique_fact_id(f"fact_education_{index:03d}", existing_fact_ids)
    details = [
        value
        for value in (education.degree, education.major)
        if value
    ]
    dates = " - ".join(
        value
        for value in (education.start_date, education.end_date)
        if value
    )
    fact_text = "，".join(
        value
        for value in (
            education.school,
            " / ".join(details) if details else None,
            dates or None,
        )
        if value
    )
    return EducationExperience(
        education_experience_id=f"education_{index:03d}",
        school=education.school,
        degree=education.degree,
        major=education.major,
        start_date=education.start_date,
        end_date=education.end_date,
        facts=[
            ExperienceFact(
                fact_id=fact_id,
                category="education_experience",
                entity_name=education.school,
                fact_text=fact_text,
                source_location="legacy:education",
            )
        ],
    )


def _legacy_personal_contact(
    contact_type: str,
    label: str,
    value: str,
    index: int,
    existing_fact_ids: set[str],
) -> PersonalContact:
    fact_id = _unique_fact_id(f"fact_contact_{index:03d}", existing_fact_ids)
    return PersonalContact(
        personal_contact_id=f"contact_{index:03d}",
        contact_type=contact_type,
        contact_value=value,
        label=label,
        facts=[
            ExperienceFact(
                fact_id=fact_id,
                category="personal_contact",
                entity_name=contact_type,
                fact_text=f"{label}：{value}",
                source_location="legacy:personal_contact",
            )
        ],
    )


def _unique_fact_id(candidate: str, existing_fact_ids: set[str]) -> str:
    suffix = 1
    fact_id = candidate
    while fact_id in existing_fact_ids:
        suffix += 1
        fact_id = f"{candidate}_{suffix}"
    existing_fact_ids.add(fact_id)
    return fact_id


def _first_contact_value(
    contacts: list[PersonalContact],
    contact_type: str,
) -> str | None:
    normalized_type = contact_type.casefold()
    for contact in contacts:
        if contact.contact_type.strip().casefold() == normalized_type:
            return contact.contact_value
    return None
