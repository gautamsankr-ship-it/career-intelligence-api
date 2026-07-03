from app.services.profile_service import load_candidate_profile
from app.services.match_service import calculate_match
from app.services.resume_optimizer import optimize_resume

candidate_profile = load_candidate_profile()

job_analysis = {
    "company": "Bamboo",
    "job_title": "Financial Data Analyst",
    "location": "Remote",
    "employment_type": "Full Time",
    "required_skills": [
        "Excel",
        "SQL",
        "Power BI",
        "Financial Analysis"
    ],
    "keywords": [
        "Financial Planning",
        "Reporting",
        "Dashboard"
    ],
    "summary": "Financial Data Analyst position."
}

career_report = calculate_match(
    candidate_profile,
    job_analysis
)

resume = optimize_resume(
    candidate_profile,
    job_analysis,
    career_report
)

print("=" * 80)
print(resume)
print("=" * 80)