import json
from pathlib import Path

from app.schemas.job_description import ParsedJD
from app.services.jd_llm_client import JDLLMClient, MinimaxJDParser


JD_DATA_DIR = Path("app/data/job_descriptions")


def save_parsed_jd(parsed_jd: ParsedJD) -> Path:
    JD_DATA_DIR.mkdir(parents=True, exist_ok=True)
    file_path = JD_DATA_DIR / f"{parsed_jd.jd_id}.json"

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(parsed_jd.model_dump(), file, ensure_ascii=False, indent=2)

    return file_path


def load_parsed_jd(jd_id: str) -> ParsedJD:
    file_path = JD_DATA_DIR / f"{jd_id}.json"

    with file_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    return ParsedJD.model_validate(data)


def parse_jd_text_to_json(
    jd_text: str,
    company: str | None = None,
    job_title: str | None = None,
    parser_client: JDLLMClient | None = None,
) -> ParsedJD:
    if not jd_text.strip():
        raise ValueError("JD text cannot be empty.")

    client = parser_client or MinimaxJDParser()
    raw_parsed_data = client.parse_jd(jd_text, company=company, job_title=job_title)
    parsed_jd = ParsedJD.model_validate(raw_parsed_data)
    save_parsed_jd(parsed_jd)
    return parsed_jd
