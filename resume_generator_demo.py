"""Manual resume-generation demo — NOT a pytest test.

Reads the real candidate profile via ProfileService and writes a REAL file
into the production output/resumes/ directory (resume_generator.py). Must
be run deliberately from the command line only; must never be imported or
collected by pytest.
"""

from app.services.profile_service import ProfileService
from app.services.resume_composer import ResumeComposer
from app.services.resume_generator import ResumeGenerator


def main() -> None:
    profile = ProfileService().get_profile()

    job = {
        "company": "Deloitte",
        "job_title": "Finance Transformation Consultant",
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


if __name__ == "__main__":
    main()
