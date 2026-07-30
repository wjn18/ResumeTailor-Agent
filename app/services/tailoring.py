from abc import ABC, abstractmethod
import json
import re
from typing import Iterable

from app.schemas.jds import JDRequirement, ParsedJD
from app.schemas.resumes import ExperienceFact, ParsedResume
from app.schemas.tailoring import (
    FactCheckReport,
    FormalEducation,
    FormalHonorAward,
    FormalProject,
    FormalResumeDocument,
    FormalWorkExperience,
    RequirementMatch,
    RequirementMatchReport,
    SentenceFactCheck,
    TailoredHonorAward,
    TailoredResumeDraft,
    TailoredSentence,
    TailoredWorkExperience,
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
                    "Return up to 6 ranked personal advantages, include every structured "
                    "work experience and honor award, and include every evidence-supported "
                    "candidate skill in related skills even when it also appears in a "
                    "personal advantage."
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
        summary = [
            TailoredSentence(
                section="advantages",
                sentence=facts_by_id[fact_id].fact_text,
                source_fact_ids=[fact_id],
            )
            for fact_id in selected_fact_ids[:6]
        ]
        work_experiences = [
            TailoredWorkExperience(
                work_experience_id=item.work_experience_id,
                bullets=[
                    TailoredSentence(
                        section="work_experience",
                        sentence=fact.fact_text,
                        source_fact_ids=[fact.fact_id],
                    )
                    for fact in item.facts
                ],
            )
            for item in resume.work_experiences
        ]
        honor_awards = [
            TailoredHonorAward(
                honor_award_id=item.honor_award_id,
                bullets=[
                    TailoredSentence(
                        section="honor_award",
                        sentence=fact.fact_text,
                        source_fact_ids=[fact.fact_id],
                    )
                    for fact in item.facts
                ],
            )
            for item in resume.honor_awards
        ]
        skills = ranked_candidate_skill_sentences(jd, resume)
        return TailoredResumeDraft(
            jd_id=jd.jd_id,
            resume_id=resume.resume_id,
            headline=jd.job_title,
            summary=summary,
            work_experiences=work_experiences,
            honor_awards=honor_awards,
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
            work_experiences=[
                TailoredWorkExperience(
                    work_experience_id=item.work_experience_id,
                    bullets=revise_section(item.bullets),
                )
                for item in draft.work_experiences
            ],
            honor_awards=[
                TailoredHonorAward(
                    honor_award_id=item.honor_award_id,
                    bullets=revise_section(item.bullets),
                )
                for item in draft.honor_awards
            ],
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
    validated_draft = validate_tailored_resume_draft(draft, jd, resume)
    return rank_and_filter_draft(
        validated_draft,
        match_report,
        jd,
        resume,
    )


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
    match_report, initial_draft = build_initial_tailored_resume(
        jd,
        resume,
        client=client,
    )
    (
        fact_check_report,
        revised_draft,
        final_fact_check_report,
    ) = review_tailored_resume(
        jd,
        resume,
        match_report,
        initial_draft,
        client=client,
    )
    return (
        match_report,
        initial_draft,
        fact_check_report,
        revised_draft,
        final_fact_check_report,
    )


def build_initial_tailored_resume(
    jd: ParsedJD,
    resume: ParsedResume,
    client: TailoringLLMClient | None = None,
) -> tuple[RequirementMatchReport, TailoredResumeDraft]:
    client = client or DeepSeekTailoringClient()
    match_report = match_requirements(jd, resume, client=client)
    initial_draft = rewrite_resume(
        jd,
        resume,
        match_report=match_report,
        client=client,
    )
    return match_report, initial_draft


def review_tailored_resume(
    jd: ParsedJD,
    resume: ParsedResume,
    match_report: RequirementMatchReport,
    initial_draft: TailoredResumeDraft,
    client: TailoringLLMClient | None = None,
) -> tuple[FactCheckReport, TailoredResumeDraft, FactCheckReport]:
    client = client or DeepSeekTailoringClient()
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
    revised_draft = rank_and_filter_draft(
        revised_draft,
        match_report,
        jd,
        resume,
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
        revised_draft = rank_and_filter_draft(
            revised_draft,
            match_report,
            jd,
            resume,
        )
        final_fact_check_report = fact_check_resume(
            jd.jd_id,
            resume,
            revised_draft,
            client=client,
        )
    revised_draft = rank_and_filter_draft(
        revised_draft,
        match_report,
        jd,
        resume,
    )
    return fact_check_report, revised_draft, final_fact_check_report


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
    work_experiences_by_id = {
        item.work_experience_id: item
        for item in resume.work_experiences
    }
    tailored_work_experiences = revised_draft.work_experiences
    if not tailored_work_experiences:
        tailored_work_experiences = [
            TailoredWorkExperience(
                work_experience_id=item.work_experience_id,
                bullets=[
                    TailoredSentence(
                        section="work_experience",
                        sentence=fact.fact_text,
                        source_fact_ids=[fact.fact_id],
                    )
                    for fact in item.facts[:6]
                ],
            )
            for item in resume.work_experiences
        ]
    formal_work_experiences = []
    for tailored_work in tailored_work_experiences:
        source_work = work_experiences_by_id.get(
            tailored_work.work_experience_id
        )
        if source_work is None:
            continue
        formal_work_experiences.append(
            FormalWorkExperience(
                company=source_work.company,
                job_title=source_work.job_title,
                start_date=source_work.start_date,
                end_date=source_work.end_date,
                bullets=[
                    sentence.sentence
                    for sentence in tailored_work.bullets
                ],
            )
        )

    honor_awards_by_id = {
        item.honor_award_id: item
        for item in resume.honor_awards
    }
    tailored_honor_awards = revised_draft.honor_awards
    if not tailored_honor_awards:
        tailored_honor_awards = [
            TailoredHonorAward(
                honor_award_id=item.honor_award_id,
                bullets=[
                    TailoredSentence(
                        section="honor_award",
                        sentence=fact.fact_text,
                        source_fact_ids=[fact.fact_id],
                    )
                    for fact in item.facts[:4]
                ],
            )
            for item in resume.honor_awards
        ]
    formal_honor_awards = []
    for tailored_honor in tailored_honor_awards:
        source_honor = honor_awards_by_id.get(
            tailored_honor.honor_award_id
        )
        if source_honor is None:
            continue
        formal_honor_awards.append(
            FormalHonorAward(
                name=source_honor.name,
                issuer=source_honor.issuer,
                date=source_honor.date,
                bullets=[
                    sentence.sentence
                    for sentence in tailored_honor.bullets
                ],
            )
        )

    return FormalResumeDocument(
        name=resume.name,
        headline=revised_draft.headline or jd.job_title,
        email=resume.email,
        phone=resume.phone,
        advantages=[
            sentence.sentence
            for sentence in revised_draft.summary
        ],
        work_experiences=formal_work_experiences,
        honor_awards=formal_honor_awards,
        related_skills=_deduplicate_text(generated_skill_text),
        summary=[],
        experience=[],
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
        skills=[],
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

    def validate_sentence(
        sentence: TailoredSentence,
        section: str,
        allowed_fact_ids: set[str] | None = None,
    ) -> TailoredSentence:
        source_fact_ids = [
            fact_id
            for fact_id in sentence.source_fact_ids
            if (
                fact_id in valid_fact_ids
                and (
                    allowed_fact_ids is None
                    or fact_id in allowed_fact_ids
                )
            )
        ]
        if not source_fact_ids:
            raise ValueError(
                "Every generated sentence must include at least one valid source_fact_id."
            )
        return sentence.model_copy(
            update={
                "section": section,
                "source_fact_ids": source_fact_ids,
            }
        )

    valid_work_experiences = {
        item.work_experience_id: item
        for item in resume.work_experiences
    }
    tailored_work_experiences = []
    seen_work_ids = set()
    for item in draft.work_experiences:
        if (
            item.work_experience_id not in valid_work_experiences
            or item.work_experience_id in seen_work_ids
        ):
            continue
        seen_work_ids.add(item.work_experience_id)
        work_fact_ids = {
            fact.fact_id
            for fact in valid_work_experiences[
                item.work_experience_id
            ].facts
        }
        tailored_work_experiences.append(
            TailoredWorkExperience(
                work_experience_id=item.work_experience_id,
                bullets=[
                    validate_sentence(
                        sentence,
                        "work_experience",
                        work_fact_ids,
                    )
                    for sentence in item.bullets[:6]
                ],
            )
        )

    for work_id, work_experience in valid_work_experiences.items():
        if work_id in seen_work_ids:
            continue
        tailored_work_experiences.append(
            TailoredWorkExperience(
                work_experience_id=work_id,
                bullets=[
                    TailoredSentence(
                        section="work_experience",
                        sentence=fact.fact_text,
                        source_fact_ids=[fact.fact_id],
                    )
                    for fact in work_experience.facts[:6]
                ],
            )
        )

    valid_honor_awards = {
        item.honor_award_id: item
        for item in resume.honor_awards
    }
    tailored_honor_awards = []
    seen_honor_ids = set()
    for item in draft.honor_awards:
        if (
            item.honor_award_id not in valid_honor_awards
            or item.honor_award_id in seen_honor_ids
        ):
            continue
        seen_honor_ids.add(item.honor_award_id)
        honor_fact_ids = {
            fact.fact_id
            for fact in valid_honor_awards[
                item.honor_award_id
            ].facts
        }
        tailored_honor_awards.append(
            TailoredHonorAward(
                honor_award_id=item.honor_award_id,
                bullets=[
                    validate_sentence(
                        sentence,
                        "honor_award",
                        honor_fact_ids,
                    )
                    for sentence in item.bullets[:4]
                ],
            )
        )

    for honor_id, honor_award in valid_honor_awards.items():
        if honor_id in seen_honor_ids:
            continue
        tailored_honor_awards.append(
            TailoredHonorAward(
                honor_award_id=honor_id,
                bullets=[
                    TailoredSentence(
                        section="honor_award",
                        sentence=fact.fact_text,
                        source_fact_ids=[fact.fact_id],
                    )
                    for fact in honor_award.facts[:4]
                ],
            )
        )

    return TailoredResumeDraft(
        jd_id=jd.jd_id,
        resume_id=resume.resume_id,
        headline=draft.headline,
        summary=[
            validate_sentence(sentence, "advantages")
            for sentence in draft.summary[:6]
        ],
        work_experiences=tailored_work_experiences,
        honor_awards=tailored_honor_awards,
        experience=[
            validate_sentence(sentence, "work_experience")
            for sentence in draft.experience
        ],
        skills=[
            validate_sentence(sentence, "related_skills")
            for sentence in draft.skills
        ],
    )


def rank_and_filter_draft(
    draft: TailoredResumeDraft,
    match_report: RequirementMatchReport,
    jd: ParsedJD | None = None,
    resume: ParsedResume | None = None,
) -> TailoredResumeDraft:
    fact_scores: dict[str, int] = {}
    match_count = len(match_report.matches)
    requirement_priorities = {
        requirement.requirement_id: requirement.priority
        for requirement in normalized_requirements(jd)
    } if jd is not None else {}
    for index, match in enumerate(match_report.matches):
        if match.match_status != MATCHED:
            continue
        priority_bonus = (
            1000
            if requirement_priorities.get(match.requirement_id) == "must_have"
            else 100
        )
        requirement_score = priority_bonus + max(1, match_count - index)
        for fact_id in match.matched_fact_ids:
            fact_scores[fact_id] = (
                fact_scores.get(fact_id, 0) + requirement_score
            )

    ranked_advantages = sorted(
        enumerate(draft.summary),
        key=lambda item: (
            -sum(
                fact_scores.get(fact_id, 0)
                for fact_id in item[1].source_fact_ids
            ),
            item[0],
        ),
    )
    advantages = [
        sentence
        for _, sentence in ranked_advantages[:6]
    ]
    related_skills = (
        ranked_candidate_skill_sentences(jd, resume, fact_scores)
        if jd is not None and resume is not None
        else list(draft.skills)
    )

    return draft.model_copy(
        update={
            "summary": advantages,
            "skills": related_skills,
        }
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
    for work_experience in resume.work_experiences:
        facts.extend(work_experience.facts)
    for honor_award in resume.honor_awards:
        facts.extend(honor_award.facts)
    for project in resume.projects:
        facts.extend(project.facts)
    unique_facts = []
    seen_fact_ids = set()
    for fact in facts:
        if fact.fact_id in seen_fact_ids:
            continue
        seen_fact_ids.add(fact.fact_id)
        unique_facts.append(fact)
    return unique_facts


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


def ranked_candidate_skill_sentences(
    jd: ParsedJD,
    resume: ParsedResume,
    fact_scores: dict[str, int] | None = None,
) -> list[TailoredSentence]:
    fact_scores = fact_scores or {}
    ranked_skills = []
    seen_names = set()
    for index, skill in enumerate(candidate_skills(resume)):
        name = skill["name"].strip()
        name_key = name.casefold()
        evidence_fact_ids = skill["evidence_fact_ids"]
        if not name or not evidence_fact_ids or name_key in seen_names:
            continue
        seen_names.add(name_key)
        evidence_score = sum(
            fact_scores.get(fact_id, 0)
            for fact_id in evidence_fact_ids
        )
        ranked_skills.append(
            (
                -_skill_jd_relevance(name, jd),
                -evidence_score,
                index,
                TailoredSentence(
                    section="related_skills",
                    sentence=_format_skill_phrase(
                        skill["proficiency"],
                        name,
                    ),
                    source_fact_ids=evidence_fact_ids,
                ),
            )
        )

    ranked_skills.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked_skills]


def _skill_jd_relevance(skill_name: str, jd: ParsedJD) -> int:
    score = 0
    weighted_sections = (
        (5000, jd.required_skills),
        (4500, jd.tools_and_technologies),
        (4000, jd.preferred_skills),
        (1500, jd.responsibilities),
        (1000, jd.soft_skills),
    )
    for weight, texts in weighted_sections:
        if any(_skill_name_matches_text(skill_name, text) for text in texts):
            score = max(score, weight)

    for requirement in normalized_requirements(jd):
        requirement_texts = [
            requirement.requirement_text,
            *requirement.keywords,
        ]
        if not any(
            _skill_name_matches_text(skill_name, text)
            for text in requirement_texts
        ):
            continue
        if requirement.category == "required_skill":
            score = max(score, 5000)
        elif requirement.category == "tool":
            score = max(score, 4500)
        elif requirement.category == "preferred_skill":
            score = max(score, 4000)
        elif requirement.priority == "must_have":
            score = max(score, 3500)
        else:
            score = max(score, 2000)
    return score


def _skill_name_matches_text(skill_name: str, text: str) -> bool:
    normalized_name = skill_name.strip().casefold()
    normalized_text = text.strip().casefold()
    if not normalized_name or not normalized_text:
        return False
    if not normalized_name.isascii():
        return normalized_name in normalized_text

    left_boundary = (
        r"(?<![a-z0-9])"
        if normalized_name[0].isalnum()
        else ""
    )
    right_boundary = (
        r"(?![a-z0-9])"
        if normalized_name[-1].isalnum()
        else ""
    )
    return bool(
        re.search(
            rf"{left_boundary}{re.escape(normalized_name)}{right_boundary}",
            normalized_text,
        )
    )


def _format_skill_phrase(proficiency: str, skill_name: str) -> str:
    prefix = {
        "了解": "了解",
        "熟悉": "熟悉使用",
        "熟练": "熟练使用",
        "精通": "精通",
    }.get(proficiency, "了解")
    return f"{prefix} {skill_name}"


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
    for work_experience in draft.work_experiences:
        yield from work_experience.bullets
    for honor_award in draft.honor_awards:
        yield from honor_award.bullets
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
    work_experiences = [
        {
            "work_experience_id": item.work_experience_id,
            "company": item.company,
            "job_title": item.job_title,
            "start_date": item.start_date,
            "end_date": item.end_date,
            "fact_ids": [fact.fact_id for fact in item.facts],
        }
        for item in resume.work_experiences
    ]
    honor_awards = [
        {
            "honor_award_id": item.honor_award_id,
            "name": item.name,
            "issuer": item.issuer,
            "date": item.date,
            "fact_ids": [fact.fact_id for fact in item.facts],
        }
        for item in resume.honor_awards
    ]
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

Personal advantage requirements:
- Return 3-6 concise Chinese bullet sentences, never more than 6.
- Order them from highest to lowest relevance to the JD. Evidence matched to an
  earlier or must-have requirement should appear first.
- State the capability first, then include a concrete work or project experience
  in the same sentence as evidence when supported.
- Prefer distinct capabilities and evidence.
- When fewer than 6 strongly JD-related capabilities exist, broadly useful and
  supported capabilities may appear later in the list.
- Each sentence should normally be 25-70 Chinese characters.

Work experience requirements:
- Include every item from Structured work experiences, preserving its
  work_experience_id. Never invent or rewrite company, job title, or dates.
- Return only tailored work-content bullets under each work_experience_id.
- Every bullet must cite only fact_ids belonging to that work experience.
- Write complete statements with an action and object. Add method, technology,
  scope, or result only when cited facts support it.

Honor and award requirements:
- Include every item from Structured honor awards, preserving its
  honor_award_id. Never invent or rewrite the award name, issuer, or date.
- Return only concise factual description bullets under each honor_award_id.
- Every bullet must cite only fact_ids belonging to that honor award.
- Do not inflate the award level, ranking, scope, selection rate, or result.

Related skill requirements:
- Include every Candidate skill supported by one or more evidence_fact_ids.
- A skill must still appear here when it is already mentioned in a personal
  advantage.
- Order required JD skills first, then JD tools and preferred skills, then other
  JD-related skills, and finally broadly useful skills such as Excel.
- Use the stored proficiency exactly; never upgrade it.
- Write one short phrase per skill using only proficiency plus skill name:
  "了解 Excel", "熟悉使用 Unity", "熟练使用 Unity", or "精通 AI".
- Do not append an unsupported ability, workflow, result, or explanatory clause.

Return valid JSON:
{{
  "jd_id": "{jd.jd_id}",
  "resume_id": "{resume.resume_id}",
  "headline": "string or null",
  "summary": [
    {{"section": "advantages", "sentence": "string", "source_fact_ids": ["fact_id"]}}
  ],
  "work_experiences": [
    {{
      "work_experience_id": "existing work_experience_id",
      "bullets": [
        {{"section": "work_experience", "sentence": "string", "source_fact_ids": ["fact_id"]}}
      ]
    }}
  ],
  "honor_awards": [
    {{
      "honor_award_id": "existing honor_award_id",
      "bullets": [
        {{"section": "honor_award", "sentence": "string", "source_fact_ids": ["fact_id"]}}
      ]
    }}
  ],
  "skills": [
    {{"section": "related_skills", "sentence": "string", "source_fact_ids": ["fact_id"]}}
  ]
}}

Parsed JD:
{jd.model_dump_json(indent=2)}

Match report:
{match_report.model_dump_json(indent=2)}

Candidate facts:
{json.dumps(facts, ensure_ascii=False, indent=2)}

Structured work experiences:
{json.dumps(work_experiences, ensure_ascii=False, indent=2)}

Structured honor awards:
{json.dumps(honor_awards, ensure_ascii=False, indent=2)}

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
- Preserve 3-6 ranked personal advantages when enough distinct relevant facts
  exist. Each advantage should state a capability and its supporting experience.
- Preserve every valid work_experience_id and revise only its content bullets.
- Preserve every valid honor_award_id and revise only its description bullets.
  Never change the award name, issuer, date, level, or ranking.
- For related skills, preserve every evidence-supported Candidate skill even when
  it is covered by personal advantages. Keep its stored proficiency, use one
  proficiency-plus-name phrase per skill, and order JD-related skills before
  broadly useful skills.
- Return the complete revised draft as valid JSON.

Return valid JSON:
{{
  "jd_id": "{jd.jd_id}",
  "resume_id": "{resume.resume_id}",
  "headline": "string or null",
  "summary": [
    {{"section": "advantages", "sentence": "string", "source_fact_ids": ["fact_id"]}}
  ],
  "work_experiences": [
    {{
      "work_experience_id": "existing work_experience_id",
      "bullets": [
        {{"section": "work_experience", "sentence": "string", "source_fact_ids": ["fact_id"]}}
      ]
    }}
  ],
  "honor_awards": [
    {{
      "honor_award_id": "existing honor_award_id",
      "bullets": [
        {{"section": "honor_award", "sentence": "string", "source_fact_ids": ["fact_id"]}}
      ]
    }}
  ],
  "skills": [
    {{"section": "related_skills", "sentence": "string", "source_fact_ids": ["fact_id"]}}
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
    work_experiences = draft.get("work_experiences")
    honor_awards = draft.get("honor_awards")
    skills = draft.get("skills")
    expected_summary_count = min(3, len(collect_resume_facts(resume)))
    if (
        not isinstance(summary, list)
        or len(summary) < expected_summary_count
        or len(summary) > 6
    ):
        return True
    if resume.work_experiences and (
        not isinstance(work_experiences, list)
        or len(work_experiences) != len(resume.work_experiences)
    ):
        return True
    if resume.honor_awards and (
        not isinstance(honor_awards, list)
        or len(honor_awards) != len(resume.honor_awards)
    ):
        return True
    expected_skill_count = len(
        {
            skill["name"].strip().casefold()
            for skill in candidate_skills(resume)
            if skill["name"].strip() and skill["evidence_fact_ids"]
        }
    )
    if (
        not isinstance(skills, list)
        or len(skills) < expected_skill_count
    ):
        return True
    return any(
        not isinstance(item, dict)
        or len(str(item.get("sentence", "")).strip()) < 3
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
