import json
from pathlib import Path
import shutil
from uuid import uuid4

from app.schemas.resume import ParsedResume
from app.schemas.resume import SourceDocument
from app.services.document_reader import read_document_text
from app.services.llm_client import MinimaxResumeParser, ResumeLLMClient


RESUME_DATA_DIR = Path("app/data/resumes")
UPLOAD_DATA_DIR = Path("app/data/uploads")


def save_parsed_resume(parsed_resume: ParsedResume) -> Path:
    RESUME_DATA_DIR.mkdir(parents=True, exist_ok=True)

    file_path = RESUME_DATA_DIR / f"{parsed_resume.resume_id}.json"

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            parsed_resume.model_dump(),
            file,
            ensure_ascii=False,
            indent=2,
        )

    return file_path


def load_parsed_resume(resume_id: str) -> ParsedResume:
    file_path = RESUME_DATA_DIR / f"{resume_id}.json"

    with file_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    return ParsedResume.model_validate(data)


def parse_resume_file_to_json(
    source_file_path: Path,
    parser_client: ResumeLLMClient | None = None,
) -> ParsedResume:
    resume_text = read_document_text(source_file_path)
    source_document = SourceDocument(
        file_name=source_file_path.name,
        file_type=source_file_path.suffix.lower().lstrip("."),
        text_length=len(resume_text),
    )
    client = parser_client or MinimaxResumeParser()
    raw_parsed_data = client.parse_resume(resume_text, source_document)
    parsed_resume = ParsedResume.model_validate(raw_parsed_data)
    save_parsed_resume(parsed_resume)
    return parsed_resume


def save_upload_file(file_name: str, file_object) -> Path:
    UPLOAD_DATA_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file_name).suffix.lower()
    safe_name = f"upload_{uuid4().hex}{suffix}"
    destination = UPLOAD_DATA_DIR / safe_name

    with destination.open("wb") as output_file:
        shutil.copyfileobj(file_object, output_file)

    return destination
