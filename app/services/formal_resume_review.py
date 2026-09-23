"""Audit the exact edited document, including identity and structured fields."""

import hashlib
import json

from app.schemas.resumes import ExperienceFact
from app.schemas.tailoring import TailoredResumeDraft, TailoredSentence
from app.services.tailoring import collect_resume_facts, fact_check_resume, SUPPORTED, PARTIALLY_SUPPORTED


def content_hash(document):
    payload = document.model_dump(mode="json") if hasattr(document, "model_dump") else document
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def document_claims(document):
    # Keep each structured item intact: a bullet transferred to another employer
    # must be checked together with that employer, title and dates.
    for field, value in document.model_dump(mode="json").items():
        for index, item in enumerate(value if isinstance(value, list) else [value]):
            if item is not None and item != "":
                yield f"{field}[{index}]", json.dumps(item, ensure_ascii=False, sort_keys=True)


def review_formal_resume(jd, resume, document, client=None):
    evidence = list(collect_resume_facts(resume))
    # These facts come only from the immutable original input, never from edits.
    original = resume.model_dump(mode="json", exclude={"source_document", "resume_id"})
    original["target_job_title"] = jd.job_title
    for field, value in original.items():
        if value:
            evidence.append(ExperienceFact(
                fact_id=f"original_profile_{field}", category="original_profile",
                entity_name=field, fact_text=json.dumps({field: value}, ensure_ascii=False),
            ))
    audit_resume = resume.model_copy(update={"experience_facts": evidence})
    fact_ids = [fact.fact_id for fact in evidence]
    draft = TailoredResumeDraft(
        jd_id=jd.jd_id, resume_id=resume.resume_id,
        summary=[TailoredSentence(section=field, sentence=text, source_fact_ids=fact_ids)
                 for field, text in document_claims(document)],
    )
    if not draft.summary:
        from app.schemas.tailoring import FactCheckReport
        return FactCheckReport(jd_id=jd.jd_id, resume_id=resume.resume_id)
    report = fact_check_resume(jd.jd_id, audit_resume, draft, client=client)
    for check in report.checks:
        if check.support_status == SUPPORTED and not check.source_fact_ids:
            check.support_status = PARTIALLY_SUPPORTED
            check.issue = "审核未提供原始资料中的事实依据。"
    return report
