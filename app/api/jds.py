from fastapi import APIRouter, HTTPException

from app.schemas.jds import (
    ExtractedJDText,
    JDParseRequest,
    JDURLExtractRequest,
    ParsedJD,
)
from app.services.jd_parser import load_parsed_jd, parse_jd_text_to_json
from app.services.jd_web_extractor import (
    JDPageFetchError,
    JDURLSecurityError,
    extract_jd_text_from_url,
)


router = APIRouter(prefix="/jds", tags=["job-descriptions"])


@router.post("/extract-url", response_model=ExtractedJDText)
def extract_jd_url(payload: JDURLExtractRequest):
    try:
        return extract_jd_text_from_url(payload.url)
    except JDURLSecurityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JDPageFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/parse", response_model=ParsedJD)
def parse_jd(payload: JDParseRequest):
    try:
        return parse_jd_text_to_json(
            payload.raw_text,
            company=payload.company,
            job_title=payload.job_title,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{jd_id}", response_model=ParsedJD)
def get_parsed_jd(jd_id: str):
    try:
        return load_parsed_jd(jd_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="JD JSON not found.") from exc
