from app.schemas.jds import ParsedJD
from app.services.database import load_json_document, upsert_json_document
from app.services.jd_llm_client import DeepSeekJDParser, JDLLMClient


def save_parsed_jd(parsed_jd: ParsedJD) -> str:
    upsert_json_document(
        "parsed_jd_documents",
        "jd_id",
        parsed_jd.jd_id,
        parsed_jd.model_dump(mode="json"),
    )
    return parsed_jd.jd_id


def load_parsed_jd(jd_id: str) -> ParsedJD:
    data = load_json_document("parsed_jd_documents", "jd_id", jd_id)
    return ParsedJD.model_validate(data)


def parse_jd_text_to_json(
    jd_text: str,
    company: str | None = None,
    job_title: str | None = None,
    parser_client: JDLLMClient | None = None,
) -> ParsedJD:
    if not jd_text.strip():
        raise ValueError("JD text cannot be empty.")

    client = parser_client or DeepSeekJDParser()
    raw_parsed_data = client.parse_jd(jd_text, company=company, job_title=job_title)
    parsed_jd = ParsedJD.model_validate(raw_parsed_data)
    save_parsed_jd(parsed_jd)
    return parsed_jd
