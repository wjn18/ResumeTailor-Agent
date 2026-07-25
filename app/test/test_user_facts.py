import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.resumes import (
    ExperienceFact,
    ParsedResume,
    Project,
    SourceDocument,
)
from app.services.user_fact_llm_client import (
    LocalFallbackUserFactParser,
    UserFactLLMClient,
)
from app.services.user_fact_parser import parse_user_fact_text_to_json


class ProjectFactParser(UserFactLLMClient):
    def parse_user_facts(self, user_text, source_document):
        return ParsedResume(
            resume_id="resume_from_model",
            source_document=source_document,
            projects=[
                Project(
                    project_id="project_from_model",
                    name="ResumeTailor",
                    technologies=["FastAPI"],
                    facts=[
                        ExperienceFact(
                            fact_id="fact_from_model",
                            category="project",
                            entity_name="ResumeTailor",
                            fact_text=user_text,
                            source_location="user_input",
                        )
                    ],
                )
            ],
        ).model_dump()


class UserFactParserTests(unittest.TestCase):
    def test_parse_user_facts_http_endpoint(self):
        parsed_resume = ParsedResume(resume_id="resume_http_test")

        with patch(
            "app.api.user_facts.parse_user_fact_text_to_json",
            return_value=parsed_resume,
        ) as parse_user_facts:
            response = TestClient(app).post(
                "/user-facts/parse",
                json={
                    "text": "我会使用 Python。",
                    "resume_id": "resume_existing",
                    "source_name": "profile",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resume_id"], "resume_http_test")
        parse_user_facts.assert_called_once_with(
            user_text="我会使用 Python。",
            resume_id="resume_existing",
            source_name="profile",
        )

    def test_creates_compatible_parsed_resume_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "app.services.resume_parser.RESUME_DATA_DIR",
                Path(temp_dir),
            ):
                result = parse_user_fact_text_to_json(
                    "我使用 Python 和 FastAPI 开发过接口。",
                    source_name="profile",
                    parser_client=LocalFallbackUserFactParser(),
                )

                self.assertIsInstance(result, ParsedResume)
                self.assertEqual(result.source_document.file_type, "text")
                self.assertEqual(result.experience_facts[0].category, "user_input")
                self.assertTrue(
                    result.experience_facts[0].fact_id.startswith("fact_user_")
                )
                self.assertEqual(
                    result.experience_facts[0].source_location,
                    "user_input:profile",
                )
                self.assertTrue((Path(temp_dir) / f"{result.resume_id}.json").exists())

    def test_merges_user_facts_into_existing_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            existing = ParsedResume(
                resume_id="resume_existing",
                source_document=SourceDocument(
                    file_name="resume.pdf",
                    file_type="pdf",
                    text_length=100,
                ),
            )

            with patch("app.services.resume_parser.RESUME_DATA_DIR", data_dir):
                from app.services.resume_parser import save_parsed_resume

                save_parsed_resume(existing)
                result = parse_user_fact_text_to_json(
                    "我会使用 Docker，并用 Python 开发过工具。",
                    resume_id=existing.resume_id,
                    source_name="self_profile",
                    parser_client=LocalFallbackUserFactParser(),
                )

                self.assertEqual(result.resume_id, existing.resume_id)
                self.assertEqual(result.source_document.file_type, "pdf")
                self.assertEqual(
                    {skill.name for skill in result.skills},
                    {"Docker", "Python"},
                )
                self.assertEqual(len(result.experience_facts), 1)
                self.assertEqual(
                    result.experience_facts[0].source_location,
                    "user_input:self_profile",
                )

    def test_merges_new_facts_into_an_existing_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            existing = ParsedResume(
                resume_id="resume_existing",
                projects=[
                    Project(
                        project_id="project_existing",
                        name="ResumeTailor",
                        technologies=["Python"],
                        facts=[
                            ExperienceFact(
                                fact_id="fact_existing",
                                category="project",
                                entity_name="ResumeTailor",
                                fact_text="使用 Python 构建简历工具。",
                            )
                        ],
                    )
                ],
            )

            with patch("app.services.resume_parser.RESUME_DATA_DIR", data_dir):
                from app.services.resume_parser import save_parsed_resume

                save_parsed_resume(existing)
                result = parse_user_fact_text_to_json(
                    "使用 FastAPI 实现接口。",
                    resume_id=existing.resume_id,
                    parser_client=ProjectFactParser(),
                )

                self.assertEqual(len(result.projects), 1)
                self.assertEqual(result.projects[0].project_id, "project_existing")
                self.assertEqual(
                    result.projects[0].technologies,
                    ["Python", "FastAPI"],
                )
                self.assertEqual(len(result.projects[0].facts), 2)
                self.assertTrue(
                    result.projects[0].facts[1].fact_id.startswith("fact_user_")
                )


if __name__ == "__main__":
    unittest.main()
