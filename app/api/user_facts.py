from fastapi import APIRouter, HTTPException

from app.schemas.resume import ParsedResume, UserFactTextParseRequest
from app.services.user_fact_parser import parse_user_fact_text_to_json


router = APIRouter(prefix="/user-facts", tags=["user-facts"])


@router.post("/parse", response_model=ParsedResume)
def parse_user_fact_text(payload: UserFactTextParseRequest):
    try:
        return parse_user_fact_text_to_json(
            user_text=payload.text,
            resume_id=payload.resume_id,
            source_name=payload.source_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Resume JSON not found.") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
