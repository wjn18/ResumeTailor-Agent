from uuid import uuid4

from app.schemas.resumes import ExperienceFact, ParsedResume, Project, SourceDocument
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

    def normalize_fact(fact: ExperienceFact) -> ExperienceFact:
        nonlocal fact_number
        normalized = fact.model_copy(
            update={
                "fact_id": f"fact_user_{token}_{fact_number:03d}",
                "verified": True,
                "source_location": f"user_input:{source_name}",
            }
        )
        fact_number += 1
        return normalized

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

    return parsed_resume.model_copy(
        update={
            "projects": projects,
            "experience_facts": [
                normalize_fact(fact)
                for fact in parsed_resume.experience_facts
            ],
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
    seen = set()
    for skill in skills:
        key = skill.name.strip().casefold()
        if key and key not in seen:
            seen.add(key)
            unique_skills.append(skill)
    return unique_skills


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
