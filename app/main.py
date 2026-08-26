from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app.models.job_request import JobRequest

def validate_job_description(description: str):
    if not description or not description.strip() or description.strip().lower() == "string" or len(description.strip()) < 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please provide a valid job description."
        )

from app.services.application_service import ApplicationService
from app.config import SCREENING_AUTO_APPLY

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
    evaluation = ApplicationService().evaluate_job(request.job_description)
    decision = evaluation.career_decision

    return {

        "job_analysis": evaluation.job_analysis,

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

            "recommendations": decision.recommendations,
            "ats_result": evaluation.ats_result,
            "screening_decision": evaluation.screening_decision,
        }

    }


@app.post("/generate-resume")
def generate_resume(request: JobRequest):
    validate_job_description(request.job_description)
    service = ApplicationService()
    evaluation = service.evaluate_job(request.job_description)
    if evaluation.screening_decision != SCREENING_AUTO_APPLY:
        return {
            "success": True,
            "documents_generated": False,
            "job_analysis": evaluation.job_analysis,
            "match_score": evaluation.career_decision.overall_score,
            "decision": evaluation.screening_decision,
        }
    result = service.generate_application_documents(evaluation)

    response = {
        "success": True,
        "documents_generated": True,
        "markdown_path": result.markdown_path,
        "docx_path": result.docx_path,
        "cover_letter_markdown_path": result.cover_letter_markdown_path,
        "cover_letter_docx_path": result.cover_letter_docx_path,
        "match_score": result.career_decision.overall_score,
        "decision": result.career_decision.decision,
    }
    if not result.job_analysis.get("company"):
        response["warnings"] = ["Company name could not be extracted from the job description."]
    
    return response


@app.post("/apply")
def apply(request: JobRequest):
    validate_job_description(request.job_description)
    service = ApplicationService()
    evaluation = service.evaluate_job(request.job_description)
    if evaluation.screening_decision != SCREENING_AUTO_APPLY:
        return {
            "success": True,
            "documents_generated": False,
            "job_analysis": evaluation.job_analysis,
            "match_score": evaluation.career_decision.overall_score,
            "decision": evaluation.screening_decision,
        }
    result = service.generate_application_documents(evaluation)

    response = {
        "success": True,
        "documents_generated": True,
        "job_analysis": result.job_analysis,
        "markdown_path": result.markdown_path,
        "docx_path": result.docx_path,
        "cover_letter_markdown_path": result.cover_letter_markdown_path,
        "cover_letter_docx_path": result.cover_letter_docx_path,
        "match_score": result.career_decision.overall_score,
        "decision": result.career_decision.decision,
    }
    if not result.job_analysis.get("company"):
        response["warnings"] = ["Company name could not be extracted from the job description."]
        
    return response
