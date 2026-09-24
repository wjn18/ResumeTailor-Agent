import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT

from app.schemas.jds import ParsedJD
from app.schemas.resumes import ParsedResume, Project
from app.schemas.tailoring import FormalResumeDocument, TailoredResumeDraft
from app.services.docx_export import export_formal_resume_docx
from app.services.tailoring import assemble_formal_resume
from app.services.user_fact_parser import _merge_projects
from app.services.llm_client import _personal_contact_parse_incomplete


class ProjectLinkTests(unittest.TestCase):
    def test_project_repository_does_not_require_personal_contact(self):
        data = {"projects": [{"links": [{"url": "https://github.com/example/demo"}]}]}
        self.assertFalse(_personal_contact_parse_incomplete(data, "项目链接：https://github.com/example/demo"))
        self.assertTrue(_personal_contact_parse_incomplete(data, "电话：13800138000，项目：https://github.com/example/demo"))

    def legacy_document(self):
        return {
            "name": "测试姓名",
            "headline": "AI技术美术方向应届生｜AI工作流与Agent开发",
            "email": "test@example.com",
            "personal_contacts": [
                {"contact_type": "phone", "contact_value": "123456789"},
                {"contact_type": "github", "label": "简历修改 Agent", "contact_value": "https://github.com/example/resume"},
                {"contact_type": "website", "label": "Unity3D ARPG 战斗 Demo", "contact_value": "https://github.com/example/game"},
                {"contact_type": "other", "label": "Unity3D ARPG 战斗 Demo 游玩视频", "contact_value": "https://www.bilibili.com/video/demo"},
                {"contact_type": "website", "label": "数字性健康 App 项目链接", "contact_value": "https://example.com/health"},
            ],
            "projects": [
                {"name": "简历修改 Agent", "bullets": ["开发简历修改功能。"]},
                {"name": "Unity3D ARPG 战斗 Demo", "bullets": ["开发战斗系统。"]},
                {"name": "数字性健康 App", "bullets": ["开发健康应用。"]},
            ],
        }

    def test_legacy_links_move_to_their_projects_and_reload_without_duplicates(self):
        document = FormalResumeDocument.model_validate(self.legacy_document())
        self.assertEqual([len(project.links) for project in document.projects], [1, 2, 1])
        self.assertEqual([c.contact_type for c in document.personal_contacts], ["phone"])
        self.assertEqual(FormalResumeDocument.model_validate(document.model_dump()), document)

    def test_ambiguous_or_unidentified_links_are_not_assigned(self):
        document = FormalResumeDocument.model_validate({
            "projects": [{"name": "Demo"}, {"name": "Demo"}],
            "personal_contacts": [
                {"contact_type": "website", "label": "Demo", "contact_value": "https://example.com/demo"},
                {"contact_type": "github", "label": "个人主页", "contact_value": "https://github.com/example"},
            ],
        })
        self.assertEqual(len(document.personal_contacts), 2)
        self.assertTrue(all(not project.links for project in document.projects))

    def test_parsed_links_survive_assembly_and_keep_original_evidence(self):
        data = self.legacy_document()
        for index, contact in enumerate(data["personal_contacts"]):
            contact["personal_contact_id"] = f"contact_{index}"
        for index, project in enumerate(data["projects"]):
            project["project_id"] = f"project_{index}"
        resume = ParsedResume.model_validate({**data, "resume_id": "resume_1"})
        original = resume.model_dump()
        document = assemble_formal_resume(
            ParsedJD(jd_id="jd_1", job_title="岗位名称", raw_text_length=0),
            resume,
            TailoredResumeDraft(jd_id="jd_1", resume_id="resume_1", headline=data["headline"]),
        )
        self.assertIsNone(document.headline)
        self.assertEqual([len(p.links) for p in document.projects], [1, 2, 1])
        self.assertEqual(resume.model_dump(), original)
        self.assertEqual(len(resume.personal_contacts), 5)

    def test_supplemental_project_links_are_merged(self):
        first = Project(project_id="p1", name="Demo", links=[{"url": "https://example.com/code"}])
        second = Project(project_id="p2", name="Demo", links=[
            {"url": "https://example.com/code"}, {"url": "https://example.com/video"},
        ])
        result = _merge_projects([first], [second])
        self.assertEqual([link.url for link in result[0].links], ["https://example.com/code", "https://example.com/video"])

    def test_word_header_has_no_headline_or_urls_and_links_follow_project_bullets(self):
        data = self.legacy_document()
        data["personal_contacts"].append({"contact_type": "website", "contact_value": "https://example.com/profile"})
        resume = FormalResumeDocument.model_validate(data)
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.services.docx_export.TAILORED_RESUME_EXPORT_DIR", Path(directory)):
                path = export_formal_resume_docx("test", resume)
            document = Document(path)
            text = "\n".join(p.text for p in document.paragraphs)
            header = text.split("项目经历")[0]
            self.assertNotIn(data["headline"], text)
            self.assertNotIn("https://", header)
            self.assertIn("123456789", header)
            self.assertNotIn("https://example.com/profile", text)
            for index, project in enumerate(resume.projects):
                end = text.index(resume.projects[index + 1].name) if index + 1 < len(resume.projects) else len(text)
                for link in project.links:
                    self.assertGreater(text.index(link.url), text.index(project.bullets[-1]))
                    self.assertLess(text.index(link.url), end)
            targets = [rel.target_ref for rel in document.part.rels.values() if rel.reltype == RT.HYPERLINK]
            self.assertEqual(len(targets), 4)


if __name__ == "__main__":
    unittest.main()
