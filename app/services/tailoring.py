from abc import ABC, abstractmethod
import json
import os
import re
from typing import Iterable

import httpx

from app.schemas.job_description import JDRequirement, ParsedJD
from app.schemas.resume import ExperienceFact, ParsedResume
from app.schemas.tailoring import (
    FactCheckReport,
    RequirementMatch,
    RequirementMatchReport,
    SentenceFactCheck,
    TailoredResumeDraft,
    TailoredSentence,
)
from app.services.llm_client import parse_model_json_response


MATCHED = "matched"
UNKNOWN = "unknown"
SUPPORTED = "supported"
PARTIALLY_SUPPORTED = "partially_supported"
UNSUPPORTED = "unsupported"


class TailoringLLMClient(ABC):
    @abstractmethod
    def match_requirements(self, jd: ParsedJD, resume: ParsedResume) -> dict:
        """Return a dict that can be validated as RequirementMatchReport."""

    @abstractmethod
    def rewrite_resume(
        self,
        jd: ParsedJD,
        resume: ParsedResume,
        match_report: RequirementMatchReport,
    ) -> dict:
        """Return a dict that can be validated as TailoredResumeDraft."""

    @abstractmethod
    def fact_check_resume(
        self,
        jd_id: str,
        resume: ParsedResume,
        draft: TailoredResumeDraft,
    ) -> dict:
        """Return a dict that can be validated as FactCheckReport."""


class MinimaxTailoringClient(TailoringLLMClient):
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

    def match_requirements(self, jd: ParsedJD, resume: ParsedResume) -> dict:
        return self._request_json(
            system_prompt=(
                "You are a strict evidence matcher for resume tailoring. "
                "Use only provided candidate facts and fact_id values."
            ),
            user_prompt=build_match_prompt(jd, resume),
        )

    def rewrite_resume(
        self,
        jd: ParsedJD,
        resume: ParsedResume,
        match_report: RequirementMatchReport,
    ) -> dict:
        return self._request_json(
            system_prompt=(
                "You are a fact-grounded resume writer. "
                "Every sentence must cite source_fact_ids from the provided facts."
            ),
            user_prompt=build_rewrite_prompt(jd, resume, match_report),
        )

    def fact_check_resume(
        self,
        jd_id: str,
        resume: ParsedResume,
        draft: TailoredResumeDraft,
    ) -> dict:
        return self._request_json(
            system_prompt=(
                "You are a strict resume fact checker. "
                "Check whether each generated sentence is fully supported by its source_fact_ids."
            ),
            user_prompt=build_fact_check_prompt(jd_id, resume, draft),
        )

    def _request_json(self, system_prompt: str, user_prompt: str) -> dict:
        response = httpx.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
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
        return parse_model_json_response(content)


class LocalFallbackTailoringClient(TailoringLLMClient):
    def match_requirements(self, jd: ParsedJD, resume: ParsedResume) -> dict:
        facts = collect_resume_facts(resume)
        matches = []
        for requirement in normalized_requirements(jd):
            matched_fact_ids = [
                fact.fact_id
                for fact in facts
                if _has_text_overlap(requirement, fact.fact_text)
            ]
            matches.append(
                RequirementMatch(
                    requirement_id=requirement.requirement_id,
                    requirement_text=requirement.requirement_text,
                    match_status=MATCHED if matched_fact_ids else UNKNOWN,
                    matched_fact_ids=matched_fact_ids[:5],
                    reasoning=(
                        "Matched by explicit text overlap with cited facts."
                        if matched_fact_ids
                        else "No provided fact explicitly supports this requirement."
                    ),
                ).model_dump()
            )

        return RequirementMatchReport(
            jd_id=jd.jd_id,
            resume_id=resume.resume_id,
            matches=matches,
        ).model_dump()

    def rewrite_resume(
        self,
        jd: ParsedJD,
        resume: ParsedResume,
        match_report: RequirementMatchReport,
    ) -> dict:
        facts_by_id = fact_index(resume)
        used_fact_ids = []
        for match in match_report.matches:
            if match.match_status == MATCHED:
                used_fact_ids.extend(match.matched_fact_ids)

        unique_fact_ids = []
        for fact_id in used_fact_ids:
            if fact_id in facts_by_id and fact_id not in unique_fact_ids:
                unique_fact_ids.append(fact_id)

        selected_fact_ids = unique_fact_ids[:6] or list(facts_by_id)[:3]
        sentences = [
            TailoredSentence(
                section="experience",
                sentence=facts_by_id[fact_id].fact_text,
                source_fact_ids=[fact_id],
            )
            for fact_id in selected_fact_ids
        ]

        summary = sentences[:2]
        experience = sentences[2:] if len(sentences) > 2 else sentences
        return TailoredResumeDraft(
            jd_id=jd.jd_id,
            resume_id=resume.resume_id,
            headline=jd.job_title,
            summary=summary,
            experience=experience,
            skills=[],
        ).model_dump()

    def fact_check_resume(
        self,
        jd_id: str,
        resume: ParsedResume,
        draft: TailoredResumeDraft,
    ) -> dict:
        facts_by_id = fact_index(resume)
        checks = []
        for sentence in iter_tailored_sentences(draft):
            invalid_fact_ids = [
                fact_id
                for fact_id in sentence.source_fact_ids
                if fact_id not in facts_by_id
            ]
            if invalid_fact_ids:
                checks.append(
                    SentenceFactCheck(
                        section=sentence.section,
                        sentence=sentence.sentence,
                        source_fact_ids=sentence.source_fact_ids,
                        support_status=PARTIALLY_SUPPORTED,
                        issue=f"Unknown source_fact_ids: {', '.join(invalid_fact_ids)}.",
                        suggestion="Remove the sentence or replace source_fact_ids with valid supporting facts.",
                    ).model_dump()
                )
                continue

            source_text = " ".join(
                facts_by_id[fact_id].fact_text
                for fact_id in sentence.source_fact_ids
            )
            is_supported = _has_text_overlap(sentence.sentence, source_text)
            checks.append(
                SentenceFactCheck(
                    section=sentence.section,
                    sentence=sentence.sentence,
                    source_fact_ids=sentence.source_fact_ids,
                    support_status=SUPPORTED if is_supported else PARTIALLY_SUPPORTED,
                    issue=None if is_supported else "The sentence is not fully grounded in the cited facts.",
                    suggestion=None
                    if is_supported
                    else "Delete unsupported wording or rewrite using only the cited fact text.",
                ).model_dump()
            )

        return FactCheckReport(
            jd_id=jd_id,
            resume_id=resume.resume_id,
            checks=checks,
        ).model_dump()


def match_requirements(
    jd: ParsedJD,
    resume: ParsedResume,
    client: TailoringLLMClient | None = None,
) -> RequirementMatchReport:
    raw_report = (client or MinimaxTailoringClient()).match_requirements(jd, resume)
    report = RequirementMatchReport.model_validate(raw_report)
    return validate_match_report(report, jd, resume)


def rewrite_resume(
    jd: ParsedJD,
    resume: ParsedResume,
    match_report: RequirementMatchReport | None = None,
    client: TailoringLLMClient | None = None,
) -> TailoredResumeDraft:
    match_report = match_report or match_requirements(jd, resume, client=client)
    raw_draft = (client or MinimaxTailoringClient()).rewrite_resume(jd, resume, match_report)
    draft = TailoredResumeDraft.model_validate(raw_draft)
    return validate_tailored_resume_draft(draft, jd, resume)


def fact_check_resume(
    jd_id: str,
    resume: ParsedResume,
    draft: TailoredResumeDraft,
    client: TailoringLLMClient | None = None,
) -> FactCheckReport:
    raw_report = (client or MinimaxTailoringClient()).fact_check_resume(jd_id, resume, draft)
    report = FactCheckReport.model_validate(raw_report)
    return validate_fact_check_report(report, jd_id, resume, draft)


def build_tailored_resume(
    jd: ParsedJD,
    resume: ParsedResume,
    client: TailoringLLMClient | None = None,
) -> tuple[RequirementMatchReport, TailoredResumeDraft, FactCheckReport]:
    client = client or MinimaxTailoringClient()
    match_report = match_requirements(jd, resume, client=client)
    draft = rewrite_resume(jd, resume, match_report=match_report, client=client)
    fact_check_report = fact_check_resume(jd.jd_id, resume, draft, client=client)
    return match_report, draft, fact_check_report


def validate_match_report(
    report: RequirementMatchReport,
    jd: ParsedJD,
    resume: ParsedResume,
) -> RequirementMatchReport:
    valid_requirement_ids = {
        requirement.requirement_id
        for requirement in normalized_requirements(jd)
    }
    valid_fact_ids = set(fact_index(resume))
    validated_matches = []

    for match in report.matches:
        if match.requirement_id not in valid_requirement_ids:
            continue

        matched_fact_ids = [
            fact_id
            for fact_id in match.matched_fact_ids
            if fact_id in valid_fact_ids
        ]
        match_status = MATCHED if matched_fact_ids and match.match_status == MATCHED else UNKNOWN
        validated_matches.append(
            match.model_copy(
                update={
                    "match_status": match_status,
                    "matched_fact_ids": matched_fact_ids,
                    "reasoning": match.reasoning
                    if matched_fact_ids
                    else "Information is insufficient in the provided fact library.",
                }
            )
        )

    return RequirementMatchReport(
        jd_id=jd.jd_id,
        resume_id=resume.resume_id,
        matches=validated_matches,
    )


def validate_tailored_resume_draft(
    draft: TailoredResumeDraft,
    jd: ParsedJD,
    resume: ParsedResume,
) -> TailoredResumeDraft:
    valid_fact_ids = set(fact_index(resume))

    def validate_sentence(sentence: TailoredSentence) -> TailoredSentence:
        source_fact_ids = [
            fact_id
            for fact_id in sentence.source_fact_ids
            if fact_id in valid_fact_ids
        ]
        if not source_fact_ids:
            raise ValueError(
                "Every generated sentence must include at least one valid source_fact_id."
            )
        return sentence.model_copy(update={"source_fact_ids": source_fact_ids})

    return TailoredResumeDraft(
        jd_id=jd.jd_id,
        resume_id=resume.resume_id,
        headline=draft.headline,
        summary=[validate_sentence(sentence) for sentence in draft.summary],
        experience=[validate_sentence(sentence) for sentence in draft.experience],
        skills=[validate_sentence(sentence) for sentence in draft.skills],
    )


def validate_fact_check_report(
    report: FactCheckReport,
    jd_id: str,
    resume: ParsedResume,
    draft: TailoredResumeDraft,
) -> FactCheckReport:
    valid_fact_ids = set(fact_index(resume))
    draft_sentences = {
        (sentence.section, sentence.sentence)
        for sentence in iter_tailored_sentences(draft)
    }
    checks = []

    for check in report.checks:
        if (check.section, check.sentence) not in draft_sentences:
            continue

        source_fact_ids = [
            fact_id
            for fact_id in check.source_fact_ids
            if fact_id in valid_fact_ids
        ]
        support_status = check.support_status
        if support_status not in {SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED}:
            support_status = PARTIALLY_SUPPORTED
        if support_status == UNSUPPORTED:
            support_status = PARTIALLY_SUPPORTED

        checks.append(
            check.model_copy(
                update={
                    "source_fact_ids": source_fact_ids,
                    "support_status": support_status,
                }
            )
        )

    return FactCheckReport(jd_id=jd_id, resume_id=resume.resume_id, checks=checks)


def collect_resume_facts(resume: ParsedResume) -> list[ExperienceFact]:
    facts = list(resume.experience_facts)
    for project in resume.projects:
        facts.extend(project.facts)
    return facts


def fact_index(resume: ParsedResume) -> dict[str, ExperienceFact]:
    return {
        fact.fact_id: fact
        for fact in collect_resume_facts(resume)
    }


def normalized_requirements(jd: ParsedJD) -> list[JDRequirement]:
    if jd.requirements:
        return jd.requirements

    requirements = []
    index = 1
    for category, texts in (
        ("responsibility", jd.responsibilities),
        ("required_skill", jd.required_skills),
        ("preferred_skill", jd.preferred_skills),
        ("soft_skill", jd.soft_skills),
        ("tool", jd.tools_and_technologies),
        ("education", jd.education_requirements),
    ):
        for text in texts:
            requirements.append(
                JDRequirement(
                    requirement_id=f"req_{index:03d}",
                    category=category,
                    requirement_text=text,
                    priority="must_have" if category == "required_skill" else "unknown",
                    keywords=[text] if category in {"required_skill", "tool"} else [],
                )
            )
            index += 1

    if jd.experience_years:
        requirements.append(
            JDRequirement(
                requirement_id=f"req_{index:03d}",
                category="experience",
                requirement_text=jd.experience_years,
                priority="must_have",
                keywords=[],
            )
        )

    return requirements


def iter_tailored_sentences(draft: TailoredResumeDraft) -> Iterable[TailoredSentence]:
    yield from draft.summary
    yield from draft.experience
    yield from draft.skills


def build_match_prompt(jd: ParsedJD, resume: ParsedResume) -> str:
    facts = [fact.model_dump() for fact in collect_resume_facts(resume)]
    requirements = [
        requirement.model_dump()
        for requirement in normalized_requirements(jd)
    ]
    return f"""
Match every job requirement against the candidate fact library.

Rules:
- Only cite provided fact_id values.
- Do not infer skills, tools, seniority, achievements, or experience from common sense.
- If evidence is insufficient, output match_status "unknown".
- match_status must be either "matched" or "unknown".

Return valid JSON:
{{
  "jd_id": "{jd.jd_id}",
  "resume_id": "{resume.resume_id}",
  "matches": [
    {{
      "requirement_id": "string",
      "requirement_text": "string",
      "match_status": "matched | unknown",
      "matched_fact_ids": ["fact_id"],
      "reasoning": "string"
    }}
  ]
}}

Job requirements:
{json.dumps(requirements, ensure_ascii=False, indent=2)}

Candidate facts:
{json.dumps(facts, ensure_ascii=False, indent=2)}
""".strip()


def build_rewrite_prompt(
    jd: ParsedJD,
    resume: ParsedResume,
    match_report: RequirementMatchReport,
) -> str:
    facts = [fact.model_dump() for fact in collect_resume_facts(resume)]
    return f"""
Rewrite resume content for the target job using only the candidate fact library.

Allowed:
- Reorganize facts.
- Adjust wording.
- Use job-relevant keywords when they are supported by cited facts.
- Highlight relevant ability.

Forbidden:
- Add new technical stacks.
- Invent numbers.
- Increase proficiency level.
- Change project role.
- Invent business outcomes.

Every generated sentence must include source_fact_ids.

Return valid JSON:
{{
  "jd_id": "{jd.jd_id}",
  "resume_id": "{resume.resume_id}",
  "headline": "string or null",
  "summary": [
    {{"section": "summary", "sentence": "string", "source_fact_ids": ["fact_id"]}}
  ],
  "experience": [
    {{"section": "experience", "sentence": "string", "source_fact_ids": ["fact_id"]}}
  ],
  "skills": [
    {{"section": "skills", "sentence": "string", "source_fact_ids": ["fact_id"]}}
  ]
}}

Parsed JD:
{jd.model_dump_json(indent=2)}

Match report:
{match_report.model_dump_json(indent=2)}

Candidate facts:
{json.dumps(facts, ensure_ascii=False, indent=2)}
""".strip()


def build_fact_check_prompt(
    jd_id: str,
    resume: ParsedResume,
    draft: TailoredResumeDraft,
) -> str:
    facts = [fact.model_dump() for fact in collect_resume_facts(resume)]
    return f"""
Check each generated resume sentence against only its source_fact_ids.

Output support_status:
- "supported" when the sentence is fully supported by cited facts.
- "partially_supported" when the sentence contains wording that is not fully supported.
- Keep "unsupported" as a possible field value, but prefer "partially_supported" with a concrete issue and suggestion.

For partially_supported, explain the exact problem and suggest deletion or a safer rewrite.

Return valid JSON:
{{
  "jd_id": "{jd_id}",
  "resume_id": "{resume.resume_id}",
  "checks": [
    {{
      "section": "string",
      "sentence": "string",
      "source_fact_ids": ["fact_id"],
      "support_status": "supported | partially_supported | unsupported",
      "issue": "string or null",
      "suggestion": "string or null"
    }}
  ]
}}

Candidate facts:
{json.dumps(facts, ensure_ascii=False, indent=2)}

Generated draft:
{draft.model_dump_json(indent=2)}
""".strip()


def _extract_message_content(response_data: dict) -> str:
    try:
        return response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected MiniMax response shape: {response_data}") from exc


def _has_text_overlap(left: JDRequirement | str, right: str) -> bool:
    if isinstance(left, JDRequirement):
        words = _content_words(left.requirement_text)
        for keyword in left.keywords:
            words.update(_content_words(keyword))
    else:
        words = _content_words(left)

    right_words = _content_words(right)
    return bool(words and right_words and words.intersection(right_words))


def _content_words(text: str) -> set[str]:
    stop_words = {
        "and",
        "or",
        "the",
        "a",
        "an",
        "to",
        "of",
        "in",
        "on",
        "with",
        "for",
        "by",
        "is",
        "are",
        "be",
        "we",
        "need",
        "build",
        "using",
        "include",
        "includes",
        "responsibilities",
    }
    return {
        word
        for word in re.findall(r"[A-Za-z0-9+#.]+", text.lower())
        if len(word) > 1 and word not in stop_words
    }
