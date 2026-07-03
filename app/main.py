from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.models.job_request import JobRequest

from app.services.ai_service import analyze_job
from app.services.profile_service import load_candidate_profile
from app.services.employer_service import EmployerService
from app.services.career_engine import CareerDecisionEngine
from app.services.resume_optimizer import optimize_resume
from app.services.docx_service import generate_resume_docx
from app.services.application_service import build_application

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

    optimized_resume = optimize_resume(
        candidate,
        job,
        decision
    )

    filename = generate_resume_docx(
        optimized_resume,
        "generated_resume.docx"
    )

    return {

        "success": True,

        "filename": filename,

        "match_score": decision.overall_score,

        "decision": decision.decision

    }


@app.post("/apply")
def apply(request: JobRequest):

    return build_application(
        request.job_description
    )