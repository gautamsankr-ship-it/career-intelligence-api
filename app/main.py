from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.models.job_request import JobRequest

from app.services.ai_service import analyze_job
from app.services.profile_service import load_candidate_profile
from app.services.employer_service import EmployerService
from app.services.career_engine import CareerDecisionEngine
from app.services.application_service import ApplicationService

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

    return JSONResponse(
        {
            "status": "healthy"
        }
    )


@app.post("/analyze-job")
def analyze_job_endpoint(request: JobRequest):

    candidate = load_candidate_profile()

    job = analyze_job(
        request.job_description
    )

    employer = EmployerService().analyze(
        job
    )

    decision = CareerDecisionEngine().evaluate(
        candidate,
        job,
        employer
    )

    return {

        "job_analysis": job,

        "career_report": {

            "overall_score": decision.overall_score,

            "decision": decision.decision,

            "confidence": decision.confidence,

            "strengths": [

                item

                for card in decision.scorecards

                for item in card.matched

            ],

            "missing_skills": [

                item

                for card in decision.scorecards

                for item in card.missing

            ],

            "recommendations": decision.recommendations

        }

    }


@app.post("/generate-resume")
def generate_resume(request: JobRequest):
    result = ApplicationService().generate_documents(request.job_description)

    return {
        "success": True,
        "markdown_file": result.markdown_path,
        "filename": result.docx_path,
        "match_score": result.career_decision.overall_score,
        "decision": result.career_decision.decision,
    }


@app.post("/apply")
def apply(request: JobRequest):
    result = ApplicationService().generate_documents(request.job_description)

    return {
        "success": True,
        "job_analysis": result.job_analysis,
        "markdown_file": result.markdown_path,
        "docx_file": result.docx_path,
        "match_score": result.career_decision.overall_score,
        "decision": result.career_decision.decision,
    }
