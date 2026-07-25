from fastapi import APIRouter, HTTPException

from app.schemas.jds import JDParseRequest, ParsedJD
from app.services.jd_parser import load_parsed_jd, parse_jd_text_to_json


router = APIRouter(prefix="/jds", tags=["job-descriptions"])


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
