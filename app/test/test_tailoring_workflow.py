import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.jds import JDRequirement, ParsedJD
from app.schemas.resumes import (
    Education,
    ExperienceFact,
    ParsedResume,
    Project,
    Skill,
)
from app.schemas.tailoring import (
    FactCheckReport,
    FormalResumeUpdateRequest,
    RequirementMatchReport,
    TailoredResumeDraft,
    TailoredSentence,
)
from app.services.document_reader import SUPPORTED_EXTENSIONS, read_document_text
from app.services.tailored_resume_storage import save_tailored_resume
from app.services.tailoring import (
    PARTIALLY_SUPPORTED,
    SUPPORTED,
    TailoringLLMClient,
    assemble_formal_resume,
    build_tailored_resume,
    validate_fact_check_report,
    validate_match_report,
)


class AuditedTailoringClient(TailoringLLMClient):
    def __init__(self):
        self.fact_check_calls = 0
        self.revision_calls = 0

    def match_requirements(self, jd, resume):
        return {
            "jd_id": jd.jd_id,
            "resume_id": resume.resume_id,
            "matches": [
                {
                    "requirement_id": "req_001",
                    "requirement_text": "FastAPI",
                    "match_status": "matched",
                    "matched_fact_ids": ["fact_001"],
                    "reasoning": "The cited fact explicitly names FastAPI.",
                }
            ],
        }

    def rewrite_resume(self, jd, resume, match_report):
        return {
            "jd_id": jd.jd_id,
            "resume_id": resume.resume_id,
            "headline": jd.job_title,
            "summary": [
                {
                    "section": "summary",
                    "sentence": "使用 FastAPI 服务百万用户。",
                    "source_fact_ids": ["fact_001"],
                }
            ],
            "experience": [],
            "skills": [],
        }

    def fact_check_resume(self, jd_id, resume, draft):
        self.fact_check_calls += 1
        sentence = draft.summary[0]
        is_revised = sentence.sentence == "使用 FastAPI 构建后端服务。"
        return {
            "jd_id": jd_id,
            "resume_id": resume.resume_id,
            "checks": [
                {
                    "section": sentence.section,
                    "sentence": sentence.sentence,
                    "source_fact_ids": sentence.source_fact_ids,
                    "support_status": (
                        SUPPORTED if is_revised else PARTIALLY_SUPPORTED
                    ),
                    "issue": None if is_revised else "百万用户没有事实依据。",
                    "suggestion": None if is_revised else "删除未提供的用户规模。",
                }
            ],
        }

    def revise_after_fact_check(self, jd, resume, draft, fact_check_report):
        self.revision_calls += 1
        return {
            "jd_id": jd.jd_id,
            "resume_id": resume.resume_id,
            "headline": jd.job_title,
            "summary": [
                {
                    "section": "summary",
                    "sentence": "使用 FastAPI 构建后端服务。",
                    "source_fact_ids": ["fact_001"],
                }
            ],
            "experience": [],
            "skills": [],
        }


class TwoPassRevisionClient(AuditedTailoringClient):
    def fact_check_resume(self, jd_id, resume, draft):
        report = super().fact_check_resume(jd_id, resume, draft)
        if self.revision_calls < 2:
            report["checks"][0].update(
                {
                    "support_status": PARTIALLY_SUPPORTED,
                    "issue": "The first correction still needs another audit pass.",
                    "suggestion": "Rewrite once more using only the cited fact.",
                }
            )
        return report


def sample_jd() -> ParsedJD:
    return ParsedJD(
        jd_id="jd_test",
        company="示例科技",
        job_title="后端工程师",
        raw_text_length=30,
        requirements=[
            JDRequirement(
                requirement_id="req_001",
                category="required_skill",
                requirement_text="FastAPI",
                keywords=["FastAPI"],
            )
        ],
    )


def sample_resume() -> ParsedResume:
    return ParsedResume(
        resume_id="resume_test",
        name="王建",
        email="wang@example.com",
        phone="13800000000",
        education=[
            Education(
                school="示例大学",
                degree="本科",
                major="计算机科学",
                start_date="2019",
                end_date="2023",
            )
        ],
        skills=[Skill(name="Python"), Skill(name="FastAPI")],
        projects=[
            Project(
                project_id="project_001",
                name="简历生成系统",
                role="后端开发",
                technologies=["Python", "FastAPI"],
                facts=[
                    ExperienceFact(
                        fact_id="fact_project_001",
                        category="project",
                        entity_name="简历生成系统",
                        fact_text="实现简历解析与存储流程。",
                    )
                ],
            )
        ],
        experience_facts=[
            ExperienceFact(
                fact_id="fact_001",
                category="experience",
                entity_name="示例科技",
                fact_text="使用 FastAPI 构建后端服务。",
            )
        ],
    )


class TailoringWorkflowTests(unittest.TestCase):
    def test_build_revises_after_audit_and_checks_again(self):
        client = AuditedTailoringClient()

        (
            _match_report,
            initial_draft,
            first_report,
            revised_draft,
            final_report,
        ) = build_tailored_resume(sample_jd(), sample_resume(), client=client)

        self.assertIn("百万用户", initial_draft.summary[0].sentence)
        self.assertEqual(
            first_report.checks[0].support_status,
            PARTIALLY_SUPPORTED,
        )
        self.assertEqual(
            revised_draft.summary[0].sentence,
            "使用 FastAPI 构建后端服务。",
        )
        self.assertEqual(final_report.checks[0].support_status, SUPPORTED)
        self.assertEqual(client.revision_calls, 1)
        self.assertEqual(client.fact_check_calls, 2)

    def test_build_runs_one_extra_revision_when_first_fix_still_fails(self):
        client = TwoPassRevisionClient()

        result = build_tailored_resume(
            sample_jd(),
            sample_resume(),
            client=client,
        )

        self.assertEqual(client.revision_calls, 2)
        self.assertEqual(client.fact_check_calls, 3)
        self.assertEqual(result[-1].checks[0].support_status, SUPPORTED)

    def test_missing_audit_sentence_is_not_treated_as_supported(self):
        draft = TailoredResumeDraft(
            jd_id="jd_test",
            resume_id="resume_test",
            summary=[
                TailoredSentence(
                    section="summary",
                    sentence="使用 FastAPI 构建后端服务。",
                    source_fact_ids=["fact_001"],
                )
            ],
        )

        report = validate_fact_check_report(
            report=FactCheckReport(
                jd_id="jd_test",
                resume_id="resume_test",
            ),
            jd_id="jd_test",
            resume=sample_resume(),
            draft=draft,
        )

        self.assertEqual(len(report.checks), 1)
        self.assertEqual(
            report.checks[0].support_status,
            PARTIALLY_SUPPORTED,
        )
        self.assertIn("omitted", report.checks[0].issue)

    def test_missing_requirement_is_added_as_unknown(self):
        jd = sample_jd().model_copy(
            update={
                "requirements": [
                    *sample_jd().requirements,
                    JDRequirement(
                        requirement_id="req_002",
                        category="required_skill",
                        requirement_text="Kubernetes",
                        keywords=["Kubernetes"],
                    ),
                ]
            }
        )

        report = validate_match_report(
            RequirementMatchReport(
                jd_id=jd.jd_id,
                resume_id="resume_test",
                matches=[],
            ),
            jd,
            sample_resume(),
        )

        self.assertEqual(
            [match.requirement_id for match in report.matches],
            ["req_001", "req_002"],
        )
        self.assertTrue(
            all(match.match_status == "unknown" for match in report.matches)
        )

    def test_formal_resume_can_be_edited_confirmed_and_downloaded(self):
        jd = sample_jd()
        resume = sample_resume()
        draft = TailoredResumeDraft(
            jd_id=jd.jd_id,
            resume_id=resume.resume_id,
            headline=jd.job_title,
            summary=[
                TailoredSentence(
                    section="summary",
                    sentence="使用 FastAPI 构建后端服务。",
                    source_fact_ids=["fact_001"],
                )
            ],
        )
        formal_resume = assemble_formal_resume(jd, resume, draft)

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            with (
                patch(
                    "app.services.tailored_resume_storage.TAILORED_RESUME_DATA_DIR",
                    data_dir,
                ),
                patch(
                    "app.services.docx_export.TAILORED_RESUME_DATA_DIR",
                    data_dir,
                ),
            ):
                saved = save_tailored_resume(
                    jd,
                    resume,
                    draft,
                    formal_resume=formal_resume,
                )
                edited = formal_resume.model_copy(
                    update={"summary": ["用户确认后的个人总结。"]}
                )
                client = TestClient(app)

                edit_response = client.patch(
                    f"/tailoring/saved/{saved.tailored_resume_id}/content",
                    json=FormalResumeUpdateRequest(
                        formal_resume=edited
                    ).model_dump(),
                )
                self.assertEqual(edit_response.status_code, 200)
                self.assertEqual(
                    edit_response.json()["formal_resume"]["summary"],
                    ["用户确认后的个人总结。"],
                )

                confirm_response = client.post(
                    f"/tailoring/saved/{saved.tailored_resume_id}/confirm",
                    json={"formal_resume": edited.model_dump()},
                )
                self.assertEqual(confirm_response.status_code, 200)
                self.assertEqual(confirm_response.json()["status"], "confirmed")

                download_response = client.get(
                    f"/tailoring/saved/{saved.tailored_resume_id}/docx"
                )
                self.assertEqual(download_response.status_code, 200)
                self.assertTrue(download_response.content.startswith(b"PK"))

                docx_path = data_dir / f"{saved.tailored_resume_id}.docx"
                document = Document(docx_path)
                document_text = "\n".join(
                    paragraph.text for paragraph in document.paragraphs
                )
                self.assertIn("用户确认后的个人总结。", document_text)
                self.assertIn("简历生成系统", document_text)

    def test_docx_input_is_supported_and_readable(self):
        self.assertIn(".pdf", SUPPORTED_EXTENSIONS)
        self.assertIn(".docx", SUPPORTED_EXTENSIONS)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "resume.docx"
            document = Document()
            document.add_paragraph("Python 与 FastAPI 项目经历")
            document.save(path)

            self.assertEqual(
                read_document_text(path),
                "Python 与 FastAPI 项目经历",
            )


if __name__ == "__main__":
    unittest.main()
