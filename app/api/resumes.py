from fastapi import APIRouter, File, HTTPException, UploadFile

from app.schemas.resumes import ParsedResume
from app.services.resume_parser import (
    load_parsed_resume,
    parse_resume_file_to_json,
    save_upload_file,
)


router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.post("/parse", response_model=ParsedResume)
def parse_resume_upload(file: UploadFile = File(...)):
    try:
        saved_file_path = save_upload_file(file.filename or "resume", file.file)
        return parse_resume_file_to_json(saved_file_path)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{resume_id}", response_model=ParsedResume)
def get_resume(resume_id: str):
    try:
        return load_parsed_resume(resume_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Resume JSON not found.") from exc
