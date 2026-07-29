from abc import ABC, abstractmethod
import re
from uuid import uuid4

from app.schemas.resumes import ExperienceFact, ParsedResume, Skill, SourceDocument
from app.services.deepseek_client import DeepSeekJSONClient
from app.services.llm_client import _honor_award_parse_incomplete


class UserFactLLMClient(ABC):
    @abstractmethod
    def parse_user_facts(
        self,
        user_text: str,
        source_document: SourceDocument,
    ) -> dict:
        """Return a dict that can be validated as ParsedResume."""


class DeepSeekUserFactParser(DeepSeekJSONClient, UserFactLLMClient):
    def parse_user_facts(
        self,
        user_text: str,
        source_document: SourceDocument,
    ) -> dict:
        prompt = build_user_fact_parse_prompt(user_text)
        parsed_data = self.request_json(
            system_prompt=(
                "You are a strict candidate fact parser. Return only valid JSON. "
                "Extract only claims explicitly stated by the user."
            ),
            user_prompt=prompt,
        )
        if _honor_award_parse_incomplete(parsed_data, user_text):
            parsed_data = self.request_json(
                system_prompt=(
                    "You are correcting an incomplete candidate fact parse. "
                    "Return only valid JSON and do not invent award metadata."
                ),
                user_prompt=(
                    f"{prompt}\n\n"
                    "The input explicitly states an honor or award. The previous "
                    "response did not populate honor_awards with award facts. "
                    "Extract its exact name, issuer, date, and atomic facts. "
                    "Use null for missing metadata."
                ),
            )
        parsed_data["source_document"] = source_document.model_dump()
        return ParsedResume.model_validate(parsed_data).model_dump()


class LocalFallbackUserFactParser(UserFactLLMClient):
    COMMON_SKILLS = [
        "Python",
        "FastAPI",
        "Pydantic",
        "SQLite",
        "SQL",
        "Java",
        "MySQL",
        "Unity",
        "C#",
        "Git",
        "Docker",
        "Linux",
        "React",
        "Streamlit",
    ]

    def parse_user_facts(
        self,
        user_text: str,
        source_document: SourceDocument,
    ) -> dict:
        lines = [line.strip() for line in user_text.splitlines() if line.strip()]
        facts = [
            ExperienceFact(
                fact_id=f"fact_{index:03d}",
                category="user_input",
                entity_name="self_reported",
                fact_text=line,
                verified=True,
                source_location=f"user_input:line:{index}",
            )
            for index, line in enumerate(lines, start=1)
        ]
        skills = []
        for skill_name in self.COMMON_SKILLS:
            if not re.search(
                rf"(?<!\w){re.escape(skill_name)}(?!\w)",
                user_text,
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
                    proficiency=_infer_user_skill_proficiency(user_text, skill_name),
                    category="user_stated",
                    evidence_fact_ids=evidence_fact_ids,
                )
            )

        return ParsedResume(
            resume_id=f"resume_{uuid4().hex[:12]}",
            source_document=source_document,
            skills=skills,
            experience_facts=facts,
        ).model_dump()


def build_user_fact_parse_prompt(user_text: str) -> str:
    return f"""
Parse the user's self-reported skills, experience, education, projects, and
honors or awards into the same JSON structure used by a parsed resume.

Rules:
- Extract only claims explicitly stated in the input.
- Do not overstate proficiency or infer years of experience, roles, metrics,
  technologies, or outcomes that are not supported by the input.
- Preserve uncertainty in the fact text instead of strengthening a claim.
- Split long descriptions into atomic facts. Each fact_text must be a complete,
  natural sentence with one main action or capability, not a keyword list.
- Every extracted claim must appear exactly once in the appropriate work,
  honor_award, project, or top-level fact collection with a unique fact_id.
- Every skill must contain proficiency using exactly one of 了解, 熟悉, 熟练,
  or 精通, and must cite its supporting evidence_fact_ids.
- Use an explicitly stated proficiency when present. Otherwise use 了解 for an
  isolated mention, 熟悉 for substantive use in one context, 熟练 only for repeated
  use or independently completed work, and 精通 only when explicitly stated.
- Set verified to true because the fact is directly supported by the user input.
- Set source_location to "user_input".
- If the user explicitly states employment information, put it in work_experiences
  with the exact company, job title, start date, and end date. Use null for
  employment metadata that the user did not state.
- If the user explicitly states an honor, award, scholarship, or competition
  prize, put it in honor_awards with the exact name, issuer, and date. Use null
  for award metadata that the user did not state.
- Award facts must stay under their honor_award and must not be duplicated in
  experience_facts.
- Return empty arrays for sections that are not stated.

Return valid JSON matching this shape:
{{
  "resume_id": "string",
  "name": "string or null",
  "email": "string or null",
  "phone": "string or null",
  "education": [
    {{
      "school": "string",
      "degree": "string or null",
      "major": "string or null",
      "start_date": "string or null",
      "end_date": "string or null"
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
          "source_location": "user_input"
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
          "source_location": "user_input"
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
          "source_location": "user_input"
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
      "source_location": "user_input"
    }}
  ]
}}

User input:
{user_text}
""".strip()


def _infer_user_skill_proficiency(text: str, skill_name: str) -> str:
    nearby_pattern = rf".{{0,12}}{re.escape(skill_name)}.{{0,12}}"
    nearby_text = " ".join(re.findall(nearby_pattern, text, re.IGNORECASE))
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
