from uuid import uuid4

from app.schemas.resumes import (
    ExperienceFact,
    HonorAward,
    ParsedResume,
    Project,
    SourceDocument,
    WorkExperience,
)
from app.services.resume_parser import load_parsed_resume, save_parsed_resume
from app.services.user_fact_llm_client import (
    DeepSeekUserFactParser,
    UserFactLLMClient,
)


def parse_user_fact_text_to_json(
    user_text: str,
    resume_id: str | None = None,
    source_name: str = "user_input",
    parser_client: UserFactLLMClient | None = None,
) -> ParsedResume:
    normalized_text = user_text.strip()
    if not normalized_text:
        raise ValueError("User fact text must not be empty.")

    source_document = SourceDocument(
        file_name=f"{source_name}.txt",
        file_type="text",
        text_length=len(normalized_text),
    )
    client = parser_client or DeepSeekUserFactParser()
    raw_parsed_data = client.parse_user_facts(normalized_text, source_document)
    parsed_user_facts = ParsedResume.model_validate(raw_parsed_data)
    normalized_user_facts = _normalize_user_fact_source(
        parsed_user_facts,
        source_name=source_name,
    )

    if resume_id is None:
        result = normalized_user_facts.model_copy(
            update={"resume_id": f"resume_{uuid4().hex[:12]}"}
        )
    else:
        existing_resume = load_parsed_resume(resume_id)
        result = _merge_user_facts(existing_resume, normalized_user_facts)

    save_parsed_resume(result)
    return result


def _normalize_user_fact_source(
    parsed_resume: ParsedResume,
    source_name: str,
) -> ParsedResume:
    token = uuid4().hex[:8]
    fact_number = 1
    fact_id_mapping: dict[str, str] = {}

    def normalize_fact(fact: ExperienceFact) -> ExperienceFact:
        nonlocal fact_number
        normalized_fact_id = f"fact_user_{token}_{fact_number:03d}"
        fact_id_mapping[fact.fact_id] = normalized_fact_id
        normalized = fact.model_copy(
            update={
                "fact_id": normalized_fact_id,
                "verified": True,
                "source_location": f"user_input:{source_name}",
            }
        )
        fact_number += 1
        return normalized

    work_experiences = []
    for work_number, work_experience in enumerate(
        parsed_resume.work_experiences,
        start=1,
    ):
        work_experiences.append(
            work_experience.model_copy(
                update={
                    "work_experience_id": (
                        f"work_user_{token}_{work_number:03d}"
                    ),
                    "facts": [
                        normalize_fact(fact)
                        for fact in work_experience.facts
                    ],
                }
            )
        )

    honor_awards = []
    for honor_number, honor_award in enumerate(
        parsed_resume.honor_awards,
        start=1,
    ):
        honor_awards.append(
            honor_award.model_copy(
                update={
                    "honor_award_id": (
                        f"honor_user_{token}_{honor_number:03d}"
                    ),
                    "facts": [
                        normalize_fact(fact)
                        for fact in honor_award.facts
                    ],
                }
            )
        )

    projects = []
    for project_number, project in enumerate(parsed_resume.projects, start=1):
        projects.append(
            project.model_copy(
                update={
                    "project_id": f"project_user_{token}_{project_number:03d}",
                    "facts": [normalize_fact(fact) for fact in project.facts],
                }
            )
        )

    experience_facts = [
        normalize_fact(fact)
        for fact in parsed_resume.experience_facts
    ]
    skills = [
        skill.model_copy(
            update={
                "evidence_fact_ids": [
                    fact_id_mapping[fact_id]
                    for fact_id in skill.evidence_fact_ids
                    if fact_id in fact_id_mapping
                ]
            }
        )
        for skill in parsed_resume.skills
    ]

    return parsed_resume.model_copy(
        update={
            "work_experiences": work_experiences,
            "honor_awards": honor_awards,
            "projects": projects,
            "experience_facts": experience_facts,
            "skills": skills,
        }
    )


def _merge_user_facts(
    existing_resume: ParsedResume,
    user_facts: ParsedResume,
) -> ParsedResume:
    return existing_resume.model_copy(
        update={
            "name": existing_resume.name or user_facts.name,
            "email": existing_resume.email or user_facts.email,
            "phone": existing_resume.phone or user_facts.phone,
            "education": _deduplicate_models(
                existing_resume.education + user_facts.education
            ),
            "skills": _deduplicate_skills(
                existing_resume.skills + user_facts.skills
            ),
            "work_experiences": _merge_work_experiences(
                existing_resume.work_experiences,
                user_facts.work_experiences,
            ),
            "honor_awards": _merge_honor_awards(
                existing_resume.honor_awards,
                user_facts.honor_awards,
            ),
            "projects": _merge_projects(
                existing_resume.projects,
                user_facts.projects,
            ),
            "experience_facts": _deduplicate_facts(
                existing_resume.experience_facts + user_facts.experience_facts
            ),
        }
    )


def _deduplicate_models(items: list) -> list:
    unique_items = []
    seen = set()
    for item in items:
        key = repr(item.model_dump())
        if key not in seen:
            seen.add(key)
            unique_items.append(item)
    return unique_items


def _deduplicate_skills(skills: list) -> list:
    unique_skills = []
    indexes = {}
    proficiency_rank = {"了解": 0, "熟悉": 1, "熟练": 2, "精通": 3}
    for skill in skills:
        key = skill.name.strip().casefold()
        if not key:
            continue
        if key not in indexes:
            indexes[key] = len(unique_skills)
            unique_skills.append(skill)
            continue

        index = indexes[key]
        existing = unique_skills[index]
        proficiency = (
            skill.proficiency
            if proficiency_rank.get(skill.proficiency, 0)
            > proficiency_rank.get(existing.proficiency, 0)
            else existing.proficiency
        )
        evidence_fact_ids = list(
            dict.fromkeys(
                existing.evidence_fact_ids + skill.evidence_fact_ids
            )
        )
        unique_skills[index] = existing.model_copy(
            update={
                "proficiency": proficiency,
                "category": existing.category or skill.category,
                "evidence_fact_ids": evidence_fact_ids,
            }
        )
    return unique_skills


def _merge_work_experiences(
    existing_items: list[WorkExperience],
    new_items: list[WorkExperience],
) -> list[WorkExperience]:
    merged_items = list(existing_items)
    indexes = {
        _work_identity(item): index
        for index, item in enumerate(merged_items)
    }

    for item in new_items:
        identity = _work_identity(item)
        existing_index = indexes.get(identity)
        if existing_index is None:
            indexes[identity] = len(merged_items)
            merged_items.append(item)
            continue

        existing = merged_items[existing_index]
        merged_items[existing_index] = existing.model_copy(
            update={
                "job_title": existing.job_title or item.job_title,
                "start_date": existing.start_date or item.start_date,
                "end_date": existing.end_date or item.end_date,
                "facts": _deduplicate_facts(existing.facts + item.facts),
            }
        )

    return merged_items


def _work_identity(item: WorkExperience) -> tuple[str, str]:
    return (
        item.company.strip().casefold(),
        (item.job_title or "").strip().casefold(),
    )


def _merge_honor_awards(
    existing_items: list[HonorAward],
    new_items: list[HonorAward],
) -> list[HonorAward]:
    merged_items = list(existing_items)
    indexes = {
        _honor_identity(item): index
        for index, item in enumerate(merged_items)
    }

    for item in new_items:
        identity = _honor_identity(item)
        existing_index = indexes.get(identity)
        if existing_index is None:
            indexes[identity] = len(merged_items)
            merged_items.append(item)
            continue

        existing = merged_items[existing_index]
        merged_items[existing_index] = existing.model_copy(
            update={
                "issuer": existing.issuer or item.issuer,
                "date": existing.date or item.date,
                "facts": _deduplicate_facts(existing.facts + item.facts),
            }
        )

    return merged_items


def _honor_identity(item: HonorAward) -> tuple[str, str]:
    return (
        item.name.strip().casefold(),
        (item.issuer or "").strip().casefold(),
    )


def _merge_projects(
    existing_projects: list[Project],
    new_projects: list[Project],
) -> list[Project]:
    merged_projects = list(existing_projects)
    project_indexes = {
        _project_identity(project): index
        for index, project in enumerate(merged_projects)
    }

    for project in new_projects:
        identity = _project_identity(project)
        existing_index = project_indexes.get(identity)
        if existing_index is None:
            project_indexes[identity] = len(merged_projects)
            merged_projects.append(project)
            continue

        existing = merged_projects[existing_index]
        merged_projects[existing_index] = existing.model_copy(
            update={
                "role": existing.role or project.role,
                "start_date": existing.start_date or project.start_date,
                "end_date": existing.end_date or project.end_date,
                "technologies": _deduplicate_strings(
                    existing.technologies + project.technologies
                ),
                "facts": _deduplicate_facts(existing.facts + project.facts),
            }
        )

    return merged_projects


def _project_identity(project: Project) -> str:
    return project.name.strip().casefold()


def _deduplicate_strings(items: list[str]) -> list[str]:
    unique_items = []
    seen = set()
    for item in items:
        key = item.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            unique_items.append(item)
    return unique_items


def _deduplicate_facts(facts: list[ExperienceFact]) -> list[ExperienceFact]:
    unique_facts = []
    seen = set()
    for fact in facts:
        key = (
            fact.category.strip().casefold(),
            fact.entity_name.strip().casefold(),
            fact.fact_text.strip().casefold(),
        )
        if key not in seen:
            seen.add(key)
            unique_facts.append(fact)
    return unique_facts
