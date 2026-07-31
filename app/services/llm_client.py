from abc import ABC, abstractmethod
import re
from uuid import uuid4

from app.schemas.resumes import ExperienceFact, ParsedResume, Skill, SourceDocument
from app.services.deepseek_client import DeepSeekJSONClient


class ResumeLLMClient(ABC):
    @abstractmethod
    def parse_resume(self, resume_text: str, source_document: SourceDocument) -> dict:
        """Return a dict that can be validated as ParsedResume."""


class PlaceholderResumeLLMClient(ResumeLLMClient):
    def parse_resume(self, resume_text: str, source_document: SourceDocument) -> dict:
        raise NotImplementedError("Configure a real model API client before using LLM parsing.")


class ModelAPIResumeParser(ResumeLLMClient):
    def parse_resume(self, resume_text: str, source_document: SourceDocument) -> dict:
        prompt = build_resume_parse_prompt(resume_text)

        # Plug the real model SDK/API request here.
        # Expected response: a JSON string matching app.schemas.resumes.ParsedResume.
        raise NotImplementedError(
            "Model API is not configured yet. Use prompt to request structured JSON: "
            f"{prompt[:120]}..."
        )


class DeepSeekResumeParser(DeepSeekJSONClient, ResumeLLMClient):
    def parse_resume(self, resume_text: str, source_document: SourceDocument) -> dict:
        prompt = build_resume_parse_prompt(resume_text)
        parsed_data = self.request_json(
            system_prompt=(
                "You are a strict resume parser. Return only valid JSON. "
                "Do not include Markdown, explanations, or unsupported facts."
            ),
            user_prompt=prompt,
        )
        missing_work = _work_experience_parse_incomplete(
            parsed_data,
            resume_text,
        )
        missing_honors = _honor_award_parse_incomplete(
            parsed_data,
            resume_text,
        )
        missing_education = _education_experience_parse_incomplete(
            parsed_data,
            resume_text,
        )
        missing_contacts = _personal_contact_parse_incomplete(
            parsed_data,
            resume_text,
        )
        if missing_work or missing_honors or missing_education or missing_contacts:
            correction_requirements = []
            if missing_work:
                correction_requirements.append(
                    "The resume contains an explicit work or internship section. "
                    "Extract each employment item into work_experiences with its "
                    "exact company, job title, dates, and atomic facts."
                )
            if missing_honors:
                correction_requirements.append(
                    "The resume contains an explicit honors or awards section. "
                    "Extract each award into honor_awards with its exact name, "
                    "issuer, date, and atomic facts."
                )
            if missing_education:
                correction_requirements.append(
                    "The resume contains explicit education information. Extract "
                    "each school into education_experiences with exact degree, "
                    "major, dates, and an atomic education fact."
                )
            if missing_contacts:
                correction_requirements.append(
                    "The resume contains explicit contact information. Extract "
                    "each item into personal_contacts with its exact type, value, "
                    "optional label, and an atomic contact fact."
                )
            parsed_data = self.request_json(
                system_prompt=(
                    "You are correcting an incomplete structured resume parse. "
                    "Return only valid JSON and do not invent metadata."
                ),
                user_prompt=(
                    f"{prompt}\n\n"
                    "The previous response omitted a required structured section. "
                    + " ".join(correction_requirements)
                    + " Use null for missing metadata."
                ),
            )
        parsed_data["source_document"] = source_document.model_dump()
        return ParsedResume.model_validate(parsed_data).model_dump()


class LocalFallbackResumeParser(ResumeLLMClient):
    COMMON_SKILLS = [
        "Python",
        "FastAPI",
        "Pydantic",
        "SQLite",
        "Java",
        "MySQL",
        "Unity",
        "C#",
        "Git",
        "Codex",
        "Docker",
        "Linux",
    ]

    def parse_resume(self, resume_text: str, source_document: SourceDocument) -> dict:
        lines = [line.strip() for line in resume_text.splitlines() if line.strip()]
        email = _find_first(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", resume_text)
        phone = _find_first(r"(?:\+?\d[\d\s-]{7,}\d)", resume_text)

        facts = [
            ExperienceFact(
                fact_id=f"fact_{index:03d}",
                category="resume_line",
                entity_name="raw_resume",
                fact_text=line,
                verified=True,
                source_location=f"line:{index}",
            )
            for index, line in enumerate(lines[:20], start=1)
        ]
        skills = []
        for skill_name in self.COMMON_SKILLS:
            if not re.search(
                rf"(?<!\w){re.escape(skill_name)}(?!\w)",
                resume_text,
                re.IGNORECASE,
            ):
                continue
            evidence_fact_ids = [
                fact.fact_id
                for fact in facts
                if re.search(
                    rf"(?<!\w){re.escape(skill_name)}(?!\w)",
                    fact.fact_text,
                    re.IGNORECASE,
                )
            ]
            skills.append(
                Skill(
                    name=skill_name,
                    proficiency=_infer_proficiency(resume_text, skill_name),
                    category="detected_keyword",
                    evidence_fact_ids=evidence_fact_ids,
                )
            )

        return ParsedResume(
            resume_id=f"resume_{uuid4().hex[:12]}",
            source_document=source_document,
            name=_guess_name(lines),
            email=email,
            phone=phone,
            skills=skills,
            experience_facts=facts,
        ).model_dump()


def build_resume_parse_prompt(resume_text: str) -> str:
    return f"""
You are a resume parsing engine for a fact-grounded resume tailoring system.
Extract only facts that are supported by the resume text. Do not invent content.

Fact extraction rules:
- Split each work or project paragraph into atomic facts. One fact should express
  one main action, responsibility, capability, or result.
- fact_text must be a complete, natural sentence rather than a keyword, label,
  or comma-separated technology list.
- Preserve the action, object, context, method/technology, and result when they
  are explicitly present. Never invent a result or metric.
- Prefer 3-6 atomic facts for a detailed project instead of one oversized fact.
- Keep every fact_id unique across experience_facts, work facts, award facts,
  education facts, personal-contact facts, and project facts.
- Put education content in education_experiences. Preserve the exact school,
  degree, major, start date, and end date; use null when a field is not stated.
- Education facts must stay under their education_experience and must not be
  duplicated in the top-level experience_facts array.
- Put email, phone, website, location, GitHub, LinkedIn, WeChat, and other
  explicit contact details in personal_contacts. Preserve values exactly.
- Personal-contact facts must stay under their personal_contact and must not be
  duplicated in the top-level experience_facts array.
- Put employment content in work_experiences. Extract company, job title, start
  date, and end date exactly as written; use null when a field is not stated.
- Work facts must stay under their work_experience and must not be duplicated in
  the top-level experience_facts array.
- Put explicitly stated honors, awards, scholarships, and competition prizes in
  honor_awards. Preserve the exact award name, issuer, and date; use null when
  issuer or date is not stated.
- Award facts must stay under their honor_award and must not be duplicated in
  the top-level experience_facts array.

Skill rules:
- Every skill must include one proficiency modifier: 了解, 熟悉, 熟练, or 精通.
- Use an explicitly stated proficiency when present.
- Otherwise infer conservatively from evidence: an isolated mention means 了解;
  substantive use in one context means 熟悉; repeated use or independent delivery
  may mean 熟练. Use 精通 only when the source explicitly says 精通.
- Every skill must cite evidence_fact_ids for facts that demonstrate its use.
- Do not create a skill from an unsupported keyword.

Return valid JSON matching this shape:
{{
  "resume_id": "string",
  "name": "string or null",
  "personal_contacts": [
    {{
      "personal_contact_id": "string",
      "contact_type": "email | phone | location | website | github | linkedin | wechat | other",
      "contact_value": "exact contact value",
      "label": "string or null",
      "facts": [
        {{
          "fact_id": "string",
          "category": "personal_contact",
          "entity_name": "contact type",
          "fact_text": "complete factual sentence",
          "verified": true,
          "source_location": "personal contact"
        }}
      ]
    }}
  ],
  "education_experiences": [
    {{
      "education_experience_id": "string",
      "school": "string",
      "degree": "string or null",
      "major": "string or null",
      "start_date": "string or null",
      "end_date": "string or null",
      "facts": [
        {{
          "fact_id": "string",
          "category": "education_experience",
          "entity_name": "school name",
          "fact_text": "complete factual sentence",
          "verified": true,
          "source_location": "education experience"
        }}
      ]
    }}
  ],
  "skills": [
    {{
      "name": "string",
      "proficiency": "了解 | 熟悉 | 熟练 | 精通",
      "category": "string or null",
      "evidence_fact_ids": ["fact_id"]
    }}
  ],
  "work_experiences": [
    {{
      "work_experience_id": "string",
      "company": "string",
      "job_title": "string or null",
      "start_date": "string or null",
      "end_date": "string or null",
      "facts": [
        {{
          "fact_id": "string",
          "category": "work",
          "entity_name": "company name",
          "fact_text": "string",
          "verified": true,
          "source_location": "work experience"
        }}
      ]
    }}
  ],
  "honor_awards": [
    {{
      "honor_award_id": "string",
      "name": "exact award or honor name",
      "issuer": "string or null",
      "date": "string or null",
      "facts": [
        {{
          "fact_id": "string",
          "category": "honor_award",
          "entity_name": "award name",
          "fact_text": "complete factual sentence",
          "verified": true,
          "source_location": "honors and awards"
        }}
      ]
    }}
  ],
  "projects": [
    {{
      "project_id": "string",
      "name": "string",
      "role": "string or null",
      "start_date": "string or null",
      "end_date": "string or null",
      "technologies": ["string"],
      "facts": [
        {{
          "fact_id": "string",
          "category": "string",
          "entity_name": "string",
          "fact_text": "string",
          "verified": true,
          "source_location": "string or null"
        }}
      ]
    }}
  ],
  "experience_facts": [
    {{
      "fact_id": "string",
      "category": "string",
      "entity_name": "string",
      "fact_text": "string",
      "verified": true,
      "source_location": "string or null"
    }}
  ]
}}

Resume text:
{resume_text}
""".strip()


def _work_experience_parse_incomplete(
    parsed_data: dict,
    resume_text: str,
) -> bool:
    has_work_section = bool(
        re.search(
            r"工作经历|工作经验|实习经历|职业经历|work\s+experience|employment",
            resume_text,
            re.IGNORECASE,
        )
    )
    if not has_work_section:
        return False
    work_experiences = parsed_data.get("work_experiences")
    return (
        not isinstance(work_experiences, list)
        or not work_experiences
        or any(
            not isinstance(item, dict)
            or not str(item.get("company", "")).strip()
            for item in work_experiences
        )
    )


def _honor_award_parse_incomplete(
    parsed_data: dict,
    resume_text: str,
) -> bool:
    has_honor_section = bool(
        re.search(
            (
                r"荣誉奖项|荣誉奖励|获奖经历|奖项荣誉|获奖情况|"
                r"(?:获得|荣获|获评|入选).{0,30}(?:奖|荣誉|称号)|"
                r"(?:一|二|三|特等)等奖|奖学金|"
                r"honou?rs?(?:\s*(?:&|and)\s*awards?)?|awards?"
            ),
            resume_text,
            re.IGNORECASE,
        )
    )
    if not has_honor_section:
        return False
    honor_awards = parsed_data.get("honor_awards")
    return (
        not isinstance(honor_awards, list)
        or not honor_awards
        or any(
            not isinstance(item, dict)
            or not str(item.get("name", "")).strip()
            or not isinstance(item.get("facts"), list)
            or not item.get("facts")
            for item in honor_awards
        )
    )


def _education_experience_parse_incomplete(
    parsed_data: dict,
    resume_text: str,
) -> bool:
    has_education = bool(
        re.search(
            (
                r"教育经历|教育背景|学历|毕业院校|学校|大学|学院|"
                r"education|university|college|bachelor|master|phd"
            ),
            resume_text,
            re.IGNORECASE,
        )
    )
    if not has_education:
        return False
    education_experiences = parsed_data.get("education_experiences")
    return (
        not isinstance(education_experiences, list)
        or not education_experiences
        or any(
            not isinstance(item, dict)
            or not str(item.get("school", "")).strip()
            or not isinstance(item.get("facts"), list)
            or not item.get("facts")
            for item in education_experiences
        )
    )


def _personal_contact_parse_incomplete(
    parsed_data: dict,
    resume_text: str,
) -> bool:
    has_contact = bool(
        re.search(
            (
                r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|"
                r"(?:\+?\d[\d\s-]{7,}\d)|"
                r"github(?:\.com|：|:)|linkedin(?:\.com|：|:)|"
                r"邮箱|电话|手机|联系方式"
            ),
            resume_text,
            re.IGNORECASE,
        )
    )
    if not has_contact:
        return False
    personal_contacts = parsed_data.get("personal_contacts")
    return (
        not isinstance(personal_contacts, list)
        or not personal_contacts
        or any(
            not isinstance(item, dict)
            or not str(item.get("contact_type", "")).strip()
            or not str(item.get("contact_value", "")).strip()
            or not isinstance(item.get("facts"), list)
            or not item.get("facts")
            for item in personal_contacts
        )
    )


def _infer_proficiency(text: str, skill_name: str) -> str:
    nearby_pattern = rf".{{0,12}}{re.escape(skill_name)}.{{0,12}}"
    nearby_matches = re.findall(nearby_pattern, text, re.IGNORECASE)
    nearby_text = " ".join(nearby_matches)
    for marker, proficiency in (
        ("精通", "精通"),
        ("熟练", "熟练"),
        ("熟悉", "熟悉"),
        ("掌握", "熟悉"),
        ("了解", "了解"),
    ):
        if marker in nearby_text:
            return proficiency
    return "熟悉"


def _find_first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(0).strip() if match else None


def _guess_name(lines: list[str]) -> str | None:
    if not lines:
        return None

    first_line = lines[0]
    if len(first_line) <= 30 and not any(char.isdigit() for char in first_line):
        return first_line

    return None
