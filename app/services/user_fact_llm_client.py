from abc import ABC, abstractmethod
import os
import re
from uuid import uuid4

import httpx

from app.schemas.resume import ExperienceFact, ParsedResume, Skill, SourceDocument
from app.services.llm_client import parse_model_json_response


class UserFactLLMClient(ABC):
    @abstractmethod
    def parse_user_facts(
        self,
        user_text: str,
        source_document: SourceDocument,
    ) -> dict:
        """Return a dict that can be validated as ParsedResume."""


class MinimaxUserFactParser(UserFactLLMClient):
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        api_url: str | None = None,
        timeout_seconds: float = 120,
    ):
        self.api_key = api_key or os.getenv("MINIMAX_API_KEY")
        self.model = model or os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
        self.api_url = api_url or os.getenv(
            "MINIMAX_API_URL",
            "https://api.minimax.io/v1/chat/completions",
        )
        self.timeout_seconds = timeout_seconds

        if not self.api_key:
            raise RuntimeError("MINIMAX_API_KEY is not set.")

    def parse_user_facts(
        self,
        user_text: str,
        source_document: SourceDocument,
    ) -> dict:
        response = httpx.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a strict candidate fact parser. Return only valid JSON. "
                            "Extract only claims explicitly stated by the user."
                        ),
                    },
                    {
                        "role": "user",
                        "content": build_user_fact_parse_prompt(user_text),
                    },
                ],
                "temperature": 0.1,
            },
            timeout=self.timeout_seconds,
        )

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"MiniMax API request failed: {response.text}") from exc

        content = _extract_message_content(response.json())
        parsed_data = parse_model_json_response(content)
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
        skills = [
            Skill(name=skill, category="user_stated")
            for skill in self.COMMON_SKILLS
            if re.search(rf"(?<!\w){re.escape(skill)}(?!\w)", user_text, re.IGNORECASE)
        ]
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

        return ParsedResume(
            resume_id=f"resume_{uuid4().hex[:12]}",
            source_document=source_document,
            skills=skills,
            experience_facts=facts,
        ).model_dump()


def build_user_fact_parse_prompt(user_text: str) -> str:
    return f"""
Parse the user's self-reported skills, experience, education, and projects into the
same JSON structure used by a parsed resume.

Rules:
- Extract only claims explicitly stated in the input.
- Do not infer proficiency, years of experience, roles, metrics, or technologies.
- Preserve uncertainty in the fact text instead of strengthening a claim.
- Every extracted skill, project claim, and experience claim must also appear in
  experience_facts or project facts with a unique fact_id.
- Set verified to true because the fact is directly supported by the user input.
- Set source_location to "user_input".
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


def _extract_message_content(response_data: dict) -> str:
    try:
        return response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected MiniMax response shape: {response_data}") from exc
