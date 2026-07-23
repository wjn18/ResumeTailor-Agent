import json
from pathlib import Path
from app.schemas.resume import ParsedResume


RESUME_DATA_DIR = Path("app/data/resumes")


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