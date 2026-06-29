from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.models.resume_request import ResumeRequest
from app.services.resume_service import generate_resume

app = FastAPI(
    title="Career Intelligence Platform Document Service",
    version="1.0.0"
)


@app.get("/")
def home():
    return {"status": "running"}


@app.get("/health")
def health():
    return JSONResponse({"status": "healthy"})


@app.post("/generate/resume")
def create_resume(request: ResumeRequest):

    result = generate_resume(request)

    return {
        "success": True,
        "docx_path": result["path"],
        "filename": result["filename"]
    }