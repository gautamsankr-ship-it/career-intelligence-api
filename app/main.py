from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.models.resume_request import ResumeRequest
from app.services.resume_service import generate_resume

app = FastAPI(
    title="Career Intelligence Platform Document Service",
    version="1.0.0"
)

templates = Jinja2Templates(directory="app/templates")


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request
        }
    )


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