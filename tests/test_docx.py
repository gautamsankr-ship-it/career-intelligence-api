from app.services.profile_service import load_candidate_profile
from app.services.match_service import calculate_match
from app.services.resume_optimizer import optimize_resume
from app.services.docx_service import generate_resume_docx

candidate = load_candidate_profile()

job_analysis = {
    "company": "Bamboo",
    "job_title": "Financial Data Analyst",
    "required_skills": [
        "Excel",
        "SQL",
        "Power BI"
    ],
    "keywords": [
        "Financial Analysis",
        "Reporting"
    ],
    "summary": "Financial Data Analyst"
}

career_report = calculate_match(candidate, job_analysis)

resume = optimize_resume(
    candidate,
    job_analysis,
    career_report
)

filename = generate_resume_docx(resume)

print(filename)