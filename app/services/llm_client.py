from abc import ABC, abstractmethod
import json
import re
from uuid import uuid4

from app.schemas.resume import ExperienceFact, ParsedResume, Skill, SourceDocument


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
        # Expected response: a JSON string matching app.schemas.resume.ParsedResume.
        raise NotImplementedError(
            "Model API is not configured yet. Use prompt to request structured JSON: "
            f"{prompt[:120]}..."
        )


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
        skills = [
            Skill(name=skill, category="detected_keyword")
            for skill in self.COMMON_SKILLS
            if re.search(rf"(?<!\w){re.escape(skill)}(?!\w)", resume_text, re.IGNORECASE)
        ]

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

        return ParsedResume(
            resume_id=f"resume_{uuid4().hex[:12]}",
            source_document=source_document,
            name=_guess_name(lines),
            email=email,
            phone=phone,
            skills=skills,
            experience_facts=facts,
        ).model_dump()


def parse_model_json_response(response_text: str) -> dict:
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Model response is not valid JSON.") from exc


def build_resume_parse_prompt(resume_text: str) -> str:
    return f"""
You are a resume parsing engine for a fact-grounded resume tailoring system.
Extract only facts that are supported by the resume text. Do not invent content.

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
    {{"name": "string", "category": "string or null"}}
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
