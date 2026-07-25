from abc import ABC, abstractmethod
from uuid import uuid4

from app.schemas.jds import ParsedJD
from app.services.deepseek_client import DeepSeekJSONClient


class JDLLMClient(ABC):
    @abstractmethod
    def parse_jd(
        self,
        jd_text: str,
        company: str | None = None,
        job_title: str | None = None,
    ) -> dict:
        """Return a dict that can be validated as ParsedJD."""


class DeepSeekJDParser(DeepSeekJSONClient, JDLLMClient):
    def parse_jd(
        self,
        jd_text: str,
        company: str | None = None,
        job_title: str | None = None,
    ) -> dict:
        prompt = build_jd_parse_prompt(jd_text, company=company, job_title=job_title)
        parsed_data = self.request_json(
            system_prompt=(
                "You are a strict job description parser. Return only valid JSON. "
                "Do not include Markdown, explanations, or invented requirements."
            ),
            user_prompt=prompt,
        )
        parsed_data["raw_text_length"] = len(jd_text)
        if company is not None:
            parsed_data["company"] = company
        if job_title is not None:
            parsed_data["job_title"] = job_title
        return ParsedJD.model_validate(parsed_data).model_dump()


class LocalFallbackJDParser(JDLLMClient):
    KEYWORDS = [
        "Python",
        "FastAPI",
        "Pydantic",
        "SQL",
        "SQLite",
        "React",
        "Streamlit",
        "Docker",
        "AWS",
        "LLM",
        "API",
        "Git",
    ]

    def parse_jd(
        self,
        jd_text: str,
        company: str | None = None,
        job_title: str | None = None,
    ) -> dict:
        lines = [line.strip("-• \t") for line in jd_text.splitlines() if line.strip()]
        detected_skills = [
            keyword
            for keyword in self.KEYWORDS
            if keyword.lower() in jd_text.lower()
        ]
        responsibilities = lines[:8]
        requirements = [
            {
                "requirement_id": f"req_{index:03d}",
                "category": "jd_line",
                "requirement_text": line,
                "priority": "must_have",
                "keywords": [
                    keyword
                    for keyword in detected_skills
                    if keyword.lower() in line.lower()
                ],
            }
            for index, line in enumerate(lines[:20], start=1)
        ]

        return ParsedJD(
            jd_id=f"jd_{uuid4().hex[:12]}",
            company=company,
            job_title=job_title,
            raw_text_length=len(jd_text),
            summary=lines[0] if lines else None,
            responsibilities=responsibilities,
            required_skills=detected_skills,
            tools_and_technologies=detected_skills,
            requirements=requirements,
        ).model_dump()


def build_jd_parse_prompt(
    jd_text: str,
    company: str | None = None,
    job_title: str | None = None,
) -> str:
    return f"""
You are a job description parsing engine for a fact-grounded resume tailoring system.
Extract only requirements supported by the job description text. Do not invent content.

Known company: {company or "unknown"}
Known job title: {job_title or "unknown"}

Return valid JSON matching this shape:
{{
  "jd_id": "string",
  "company": "string or null",
  "job_title": "string or null",
  "raw_text_length": 0,
  "summary": "string or null",
  "responsibilities": ["string"],
  "required_skills": ["string"],
  "preferred_skills": ["string"],
  "soft_skills": ["string"],
  "tools_and_technologies": ["string"],
  "experience_years": "string or null",
  "education_requirements": ["string"],
  "requirements": [
    {{
      "requirement_id": "string",
      "category": "responsibility | required_skill | preferred_skill | soft_skill | tool | education | experience | other",
      "requirement_text": "string",
      "priority": "must_have | nice_to_have | unknown",
      "keywords": ["string"]
    }}
  ]
}}

Job description text:
{jd_text}
""".strip()
