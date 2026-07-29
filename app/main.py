import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.crud import router as crud_router
from app.api.jds import router as jds_router
from app.api.resumes import router as resumes_router
from app.api.tailoring import router as tailoring_router
from app.api.user_facts import router as user_facts_router

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(crud_router)
app.include_router(jds_router)
app.include_router(resumes_router)
app.include_router(tailoring_router)
app.include_router(user_facts_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
