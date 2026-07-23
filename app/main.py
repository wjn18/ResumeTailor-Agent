from fastapi import FastAPI
from app.schemas.resume import ParsedResume
from app.services.resume_parser import save_parsed_resume, load_parsed_resume

app = FastAPI()


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/resumes")
def create_resume(parsed_resume: ParsedResume):
    file_path = save_parsed_resume(parsed_resume)

    return {
        "status": "saved",
        "resume_id": parsed_resume.resume_id,
        "file_path": str(file_path),
    }


@app.get("/resumes/{resume_id}", response_model=ParsedResume)
def get_resume(resume_id: str):
    return load_parsed_resume(resume_id)