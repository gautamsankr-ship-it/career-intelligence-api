from app.services.profile_service import ProfileService
from app.services.resume_composer import ResumeComposer
from app.services.resume_generator import ResumeGenerator


profile = ProfileService().get_profile()


job = {
    "company": "Deloitte",
    "job_title": "Finance Transformation Consultant"
}

ats_result = {
    "ats_score": {},
    "keyword_summary": {},
}

resume_strategy = {
    "resume_title": job["job_title"],
    "summary_focus": [],
    "skills_priority": [],
    "projects_priority": [],
    "keywords_missing": [],
    "keywords_to_strengthen": [],
}

composition = ResumeComposer().compose(
    profile,
    job,
    career_result=None,
    ats_result=ats_result,
    resume_strategy=resume_strategy,
)

generator = ResumeGenerator()

file = generator.generate(composition, job)

print("=" * 70)
print("RESUME GENERATED")
print("=" * 70)
print(file)
