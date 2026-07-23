from fastapi import FastAPI

from app.api.resumes import router as resumes_router

app = FastAPI()
app.include_router(resumes_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
