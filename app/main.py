from fastapi import FastAPI

from app.api.crud import router as crud_router
from app.api.jds import router as jds_router
from app.api.resumes import router as resumes_router
from app.api.tailoring import router as tailoring_router

app = FastAPI()
app.include_router(crud_router)
app.include_router(jds_router)
app.include_router(resumes_router)
app.include_router(tailoring_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
