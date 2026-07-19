from app.services.resume_optimizer import ResumeOptimizer


job_analysis = {
    "company": "Bamboo",
    "job_title": "Financial Data Analyst",
    "required_skills": ["Excel", "SQL", "Power BI", "Financial Analysis"],
    "finance_domains": ["Financial Planning"],
}

ats_result = {
    "keyword_summary": {
        "matched": [{"keyword": "Excel"}, {"keyword": "SQL"}],
        "partial": [{"keyword": "Power BI"}],
        "missing": ["Financial Analysis"],
    }
}

strategy = ResumeOptimizer().optimize(
    career_result=None,
    ats_result=ats_result,
    job_analysis=job_analysis,
)

print("=" * 80)
print(strategy)
print("=" * 80)
