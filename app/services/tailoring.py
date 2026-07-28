from abc import ABC, abstractmethod
import json
import re
from typing import Iterable

from app.schemas.jds import JDRequirement, ParsedJD
from app.schemas.resumes import ExperienceFact, ParsedResume
from app.schemas.tailoring import (
    FactCheckReport,
    FormalEducation,
    FormalProject,
    FormalResumeDocument,
    RequirementMatch,
    RequirementMatchReport,
    SentenceFactCheck,
    TailoredResumeDraft,
    TailoredSentence,
)
from app.services.deepseek_client import DeepSeekJSONClient


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

    @abstractmethod
    def revise_after_fact_check(
        self,
        jd: ParsedJD,
        resume: ParsedResume,
        draft: TailoredResumeDraft,
        fact_check_report: FactCheckReport,
    ) -> dict:
        """Return a corrected TailoredResumeDraft after applying the audit."""


class DeepSeekTailoringClient(DeepSeekJSONClient, TailoringLLMClient):
    def match_requirements(self, jd: ParsedJD, resume: ParsedResume) -> dict:
        return self.request_json(
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
        prompt = build_rewrite_prompt(jd, resume, match_report)
        draft = self.request_json(
            system_prompt=(
                "You are a fact-grounded resume writer. "
                "Every sentence must cite source_fact_ids from the provided facts."
            ),
            user_prompt=prompt,
        )
        if _draft_needs_content_retry(draft, resume):
            draft = self.request_json(
                system_prompt=(
                    "You are a fact-grounded resume writer correcting an incomplete "
                    "draft. Every sentence must cite source_fact_ids."
                ),
                user_prompt=(
                    f"{prompt}\n\n"
                    "The previous response did not meet the content requirements. "
                    "Return 3-4 substantive summary sentences and, when supported "
                    "candidate skills exist, 2-4 natural JD-relevant skill sentences. "
                    "Do not return keyword-only skill items."
                ),
            )
        return draft

    def fact_check_resume(
        self,
        jd_id: str,
        resume: ParsedResume,
        draft: TailoredResumeDraft,
    ) -> dict:
        return self.request_json(
            system_prompt=(
                "You are a strict resume fact checker. "
                "Check whether each generated sentence is fully supported by its source_fact_ids."
            ),
            user_prompt=build_fact_check_prompt(jd_id, resume, draft),
        )

    def revise_after_fact_check(
        self,
        jd: ParsedJD,
        resume: ParsedResume,
        draft: TailoredResumeDraft,
        fact_check_report: FactCheckReport,
    ) -> dict:
        return self.request_json(
            system_prompt=(
                "You revise audited resume content using only cited candidate facts. "
                "Remove or safely rewrite every unsupported phrase and return valid JSON."
            ),
            user_prompt=build_revision_prompt(
                jd,
                resume,
                draft,
                fact_check_report,
            ),
        )


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

        selected_fact_ids = unique_fact_ids[:6]
        for fact_id in facts_by_id:
            if fact_id not in selected_fact_ids:
                selected_fact_ids.append(fact_id)
            if len(selected_fact_ids) == 6:
                break
        experience = [
            TailoredSentence(
                section="experience",
                sentence=facts_by_id[fact_id].fact_text,
                source_fact_ids=[fact_id],
            )
            for fact_id in selected_fact_ids
        ]
        summary = [
            TailoredSentence(
                section="summary",
                sentence=facts_by_id[fact_id].fact_text,
                source_fact_ids=[fact_id],
            )
            for fact_id in selected_fact_ids[:4]
        ]
        skills = []
        jd_text = jd.model_dump_json().casefold()
        for skill in candidate_skills(resume):
            if skill["name"].casefold() not in jd_text:
                continue
            evidence_fact_ids = skill["evidence_fact_ids"][:2]
            if not evidence_fact_ids:
                continue
            skills.append(
                TailoredSentence(
                    section="skills",
                    sentence=(
                        f"{skill['proficiency']}{skill['name']}，"
                        "能够在相关项目或工作场景中应用。"
                    ),
                    source_fact_ids=evidence_fact_ids,
                )
            )
            if len(skills) == 4:
                break
        return TailoredResumeDraft(
            jd_id=jd.jd_id,
            resume_id=resume.resume_id,
            headline=jd.job_title,
            summary=summary,
            experience=experience,
            skills=skills,
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

    def revise_after_fact_check(
        self,
        jd: ParsedJD,
        resume: ParsedResume,
        draft: TailoredResumeDraft,
        fact_check_report: FactCheckReport,
    ) -> dict:
        facts_by_id = fact_index(resume)
        checks = {
            (check.section, check.sentence): check
            for check in fact_check_report.checks
        }

        def revise_section(sentences: list[TailoredSentence]) -> list[TailoredSentence]:
            revised = []
            seen = set()
            for sentence in sentences:
                check = checks.get((sentence.section, sentence.sentence))
                if check is None or check.support_status == SUPPORTED:
                    candidates = [sentence]
                else:
                    candidates = [
                        TailoredSentence(
                            section=sentence.section,
                            sentence=facts_by_id[fact_id].fact_text,
                            source_fact_ids=[fact_id],
                        )
                        for fact_id in sentence.source_fact_ids
                        if fact_id in facts_by_id
                    ]

                for candidate in candidates:
                    key = (candidate.section, candidate.sentence)
                    if key not in seen:
                        seen.add(key)
                        revised.append(candidate)
            return revised

        return TailoredResumeDraft(
            jd_id=jd.jd_id,
            resume_id=resume.resume_id,
            headline=draft.headline or jd.job_title,
            summary=revise_section(draft.summary),
            experience=revise_section(draft.experience),
            skills=revise_section(draft.skills),
        ).model_dump()


def match_requirements(
    jd: ParsedJD,
    resume: ParsedResume,
    client: TailoringLLMClient | None = None,
) -> RequirementMatchReport:
    raw_report = (client or DeepSeekTailoringClient()).match_requirements(jd, resume)
    report = RequirementMatchReport.model_validate(raw_report)
    return validate_match_report(report, jd, resume)


def rewrite_resume(
    jd: ParsedJD,
    resume: ParsedResume,
    match_report: RequirementMatchReport | None = None,
    client: TailoringLLMClient | None = None,
) -> TailoredResumeDraft:
    match_report = match_report or match_requirements(jd, resume, client=client)
    raw_draft = (client or DeepSeekTailoringClient()).rewrite_resume(
        jd,
        resume,
        match_report,
    )
    draft = TailoredResumeDraft.model_validate(raw_draft)
    return validate_tailored_resume_draft(draft, jd, resume)


def fact_check_resume(
    jd_id: str,
    resume: ParsedResume,
    draft: TailoredResumeDraft,
    client: TailoringLLMClient | None = None,
) -> FactCheckReport:
    raw_report = (client or DeepSeekTailoringClient()).fact_check_resume(
        jd_id,
        resume,
        draft,
    )
    report = FactCheckReport.model_validate(raw_report)
    return validate_fact_check_report(report, jd_id, resume, draft)


def revise_after_fact_check(
    jd: ParsedJD,
    resume: ParsedResume,
    draft: TailoredResumeDraft,
    fact_check_report: FactCheckReport,
    client: TailoringLLMClient | None = None,
) -> TailoredResumeDraft:
    if all(
        check.support_status == SUPPORTED
        for check in fact_check_report.checks
    ):
        return draft

    raw_draft = (client or DeepSeekTailoringClient()).revise_after_fact_check(
        jd,
        resume,
        draft,
        fact_check_report,
    )
    revised_draft = TailoredResumeDraft.model_validate(raw_draft)
    return validate_tailored_resume_draft(revised_draft, jd, resume)


def build_tailored_resume(
    jd: ParsedJD,
    resume: ParsedResume,
    client: TailoringLLMClient | None = None,
) -> tuple[
    RequirementMatchReport,
    TailoredResumeDraft,
    FactCheckReport,
    TailoredResumeDraft,
    FactCheckReport,
]:
    client = client or DeepSeekTailoringClient()
    match_report = match_requirements(jd, resume, client=client)
    initial_draft = rewrite_resume(
        jd,
        resume,
        match_report=match_report,
        client=client,
    )
    fact_check_report = fact_check_resume(
        jd.jd_id,
        resume,
        initial_draft,
        client=client,
    )
    revised_draft = revise_after_fact_check(
        jd,
        resume,
        initial_draft,
        fact_check_report,
        client=client,
    )
    final_fact_check_report = fact_check_resume(
        jd.jd_id,
        resume,
        revised_draft,
        client=client,
    )
    if any(
        check.support_status != SUPPORTED
        for check in final_fact_check_report.checks
    ):
        revised_draft = revise_after_fact_check(
            jd,
            resume,
            revised_draft,
            final_fact_check_report,
            client=client,
        )
        final_fact_check_report = fact_check_resume(
            jd.jd_id,
            resume,
            revised_draft,
            client=client,
        )
    return (
        match_report,
        initial_draft,
        fact_check_report,
        revised_draft,
        final_fact_check_report,
    )


def assemble_formal_resume(
    jd: ParsedJD,
    resume: ParsedResume,
    revised_draft: TailoredResumeDraft,
) -> FormalResumeDocument:
    generated_skill_text = [
        sentence.sentence.strip()
        for sentence in revised_draft.skills
        if sentence.sentence.strip()
    ]
    return FormalResumeDocument(
        name=resume.name,
        headline=revised_draft.headline or jd.job_title,
        email=resume.email,
        phone=resume.phone,
        summary=[
            sentence.sentence
            for sentence in revised_draft.summary
        ],
        experience=[
            sentence.sentence
            for sentence in revised_draft.experience
        ],
        education=[
            FormalEducation.model_validate(education.model_dump())
            for education in resume.education
        ],
        projects=[
            FormalProject(
                name=project.name,
                role=project.role,
                start_date=project.start_date,
                end_date=project.end_date,
                technologies=project.technologies,
                bullets=[fact.fact_text for fact in project.facts],
            )
            for project in resume.projects
        ],
        skills=_deduplicate_text(generated_skill_text),
    )


def validate_match_report(
    report: RequirementMatchReport,
    jd: ParsedJD,
    resume: ParsedResume,
) -> RequirementMatchReport:
    requirements = normalized_requirements(jd)
    requirements_by_id = {
        requirement.requirement_id: requirement
        for requirement in requirements
    }
    valid_fact_ids = set(fact_index(resume))
    validated_matches = []
    matched_requirement_ids = set()

    for match in report.matches:
        if (
            match.requirement_id not in requirements_by_id
            or match.requirement_id in matched_requirement_ids
        ):
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
        matched_requirement_ids.add(match.requirement_id)

    for requirement in requirements:
        if requirement.requirement_id in matched_requirement_ids:
            continue
        validated_matches.append(
            RequirementMatch(
                requirement_id=requirement.requirement_id,
                requirement_text=requirement.requirement_text,
                match_status=UNKNOWN,
                matched_fact_ids=[],
                reasoning=(
                    "The matching response omitted this requirement; "
                    "information is treated as insufficient."
                ),
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
    draft_sentence_list = list(iter_tailored_sentences(draft))
    draft_sentences = {
        (sentence.section, sentence.sentence)
        for sentence in draft_sentence_list
    }
    checks = []
    checked_sentences = set()

    for check in report.checks:
        sentence_key = (check.section, check.sentence)
        if sentence_key not in draft_sentences or sentence_key in checked_sentences:
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
        checked_sentences.add(sentence_key)

    for sentence in draft_sentence_list:
        sentence_key = (sentence.section, sentence.sentence)
        if sentence_key in checked_sentences:
            continue
        checks.append(
            SentenceFactCheck(
                section=sentence.section,
                sentence=sentence.sentence,
                source_fact_ids=[
                    fact_id
                    for fact_id in sentence.source_fact_ids
                    if fact_id in valid_fact_ids
                ],
                support_status=PARTIALLY_SUPPORTED,
                issue="The fact-check response omitted this generated sentence.",
                suggestion=(
                    "Review the sentence and rewrite it using only its cited facts."
                ),
            )
        )

    return FactCheckReport(jd_id=jd_id, resume_id=resume.resume_id, checks=checks)


def collect_resume_facts(resume: ParsedResume) -> list[ExperienceFact]:
    facts = list(resume.experience_facts)
    for project in resume.projects:
        facts.extend(project.facts)
    return facts


def candidate_skills(resume: ParsedResume) -> list[dict]:
    facts = collect_resume_facts(resume)
    valid_fact_ids = {fact.fact_id for fact in facts}
    candidates = []
    for skill in resume.skills:
        evidence_fact_ids = [
            fact_id
            for fact_id in skill.evidence_fact_ids
            if fact_id in valid_fact_ids
        ]
        if not evidence_fact_ids:
            skill_name = skill.name.strip().casefold()
            evidence_fact_ids = [
                fact.fact_id
                for fact in facts
                if skill_name and skill_name in fact.fact_text.casefold()
            ]
        candidates.append(
            {
                "name": skill.name,
                "proficiency": skill.proficiency,
                "category": skill.category,
                "evidence_fact_ids": list(dict.fromkeys(evidence_fact_ids)),
            }
        )
    return candidates


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
    skills = candidate_skills(resume)
    return f"""
Rewrite resume content for the target job using only the candidate fact library.

Allowed:
- Reorganize facts.
- Adjust wording.
- Use job-relevant keywords when they are supported by cited facts.
- Highlight relevant ability.
- Add natural grammatical connectors that do not introduce a new factual claim.

Forbidden:
- Add new technical stacks.
- Invent numbers.
- Increase proficiency level.
- Change project role.
- Invent business outcomes.

Every generated sentence must include source_fact_ids.

Summary requirements:
- Return 3-4 concise Chinese sentences when at least three relevant facts exist.
- Each sentence should normally be 25-60 Chinese characters.
- Express capability plus an application context or supporting experience.
- Do not output a keyword list and do not merely repeat the skills section.
- Use different evidence across the summary where possible.

Experience requirements:
- Write complete, natural statements with an action and object.
- Include method, technology, scope, or result only when cited facts support it.
- Do not turn a long source paragraph into a comma-separated keyword list.

Skill requirements:
- Select only skills relevant to the JD and supported by evidence_fact_ids.
- Use the stored proficiency exactly; never upgrade it.
- Return 2-4 concise Chinese skill sentences when relevant supported skills exist.
- Write natural phrases such as "熟练使用 Unity，熟悉其操作与开发流程",
  not isolated words such as "Unity / C# / Java".
- Related skills may be combined only when all claims are supported by the cited
  source_fact_ids.

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

Candidate skills with stored proficiency and evidence:
{json.dumps(skills, ensure_ascii=False, indent=2)}
""".strip()


def build_fact_check_prompt(
    jd_id: str,
    resume: ParsedResume,
    draft: TailoredResumeDraft,
) -> str:
    facts = [fact.model_dump() for fact in collect_resume_facts(resume)]
    skills = candidate_skills(resume)
    return f"""
Check each generated resume sentence against only its source_fact_ids.

Output support_status:
- "supported" when the sentence is fully supported by cited facts.
- "partially_supported" when the sentence contains wording that is not fully supported.
- Keep "unsupported" as a possible field value, but prefer "partially_supported" with a concrete issue and suggestion.
- For a skills sentence, its proficiency wording must not exceed the stored
  proficiency in Candidate skills, and at least one cited source_fact_id must
  appear in that skill's evidence_fact_ids.
- Natural grammatical connectors are allowed when they add no new factual claim.

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

Candidate skills:
{json.dumps(skills, ensure_ascii=False, indent=2)}

Generated draft:
{draft.model_dump_json(indent=2)}
""".strip()


def build_revision_prompt(
    jd: ParsedJD,
    resume: ParsedResume,
    draft: TailoredResumeDraft,
    fact_check_report: FactCheckReport,
) -> str:
    facts = [fact.model_dump() for fact in collect_resume_facts(resume)]
    skills = candidate_skills(resume)
    return f"""
Revise the resume draft after fact checking.

Rules:
- Keep supported sentences factual and concise.
- For every partially_supported or unsupported sentence, apply the audit
  suggestion only when the result is fully supported by cited facts.
- Delete unsupported wording when it cannot be safely rewritten.
- Do not add technology, numbers, seniority, proficiency, roles, or outcomes.
- Do not combine unrelated projects into one claim.
- Every returned sentence must cite one or more provided source_fact_ids.
- Preserve 3-4 substantive summary sentences whenever the candidate has enough
  distinct relevant facts; do not replace the summary with a keyword list.
- For skills, use only JD-relevant Candidate skills, keep their stored proficiency,
  and return natural short sentences rather than isolated technology names.
- Return the complete revised draft as valid JSON.

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

Target JD:
{jd.model_dump_json(indent=2)}

Candidate facts:
{json.dumps(facts, ensure_ascii=False, indent=2)}

Candidate skills:
{json.dumps(skills, ensure_ascii=False, indent=2)}

Draft before revision:
{draft.model_dump_json(indent=2)}

Fact-check report:
{fact_check_report.model_dump_json(indent=2)}
""".strip()


def _draft_needs_content_retry(draft: dict, resume: ParsedResume) -> bool:
    summary = draft.get("summary")
    skills = draft.get("skills")
    expected_summary_count = min(3, len(collect_resume_facts(resume)))
    if (
        not isinstance(summary, list)
        or len(summary) < expected_summary_count
        or len(summary) > 4
    ):
        return True
    supported_skill_count = sum(
        bool(skill["evidence_fact_ids"])
        for skill in candidate_skills(resume)
    )
    if not supported_skill_count:
        return False
    expected_skill_count = min(2, supported_skill_count)
    if (
        not isinstance(skills, list)
        or len(skills) < expected_skill_count
        or len(skills) > 4
    ):
        return True
    return any(
        not isinstance(item, dict)
        or len(str(item.get("sentence", "")).strip()) < 8
        for item in skills
    )


def _deduplicate_text(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        normalized = item.strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


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
