from app.services.profile_service import ProfileService
from app.services.resume_generator import ResumeGenerator


profile = ProfileService().get_profile()


job = {
    "company": "Deloitte",
    "job_title": "Finance Transformation Consultant"
}

decision = type(
    "Decision",
    (),
    {
        "resume_strategy": {
            "keywords": [

                "Financial Reporting",

                "Finance Transformation",

                "Power BI",

                "Python",

                "SQL",

                "Artificial Intelligence",

                "Business Automation",

                "Australian Accounting",

                "Project Management"

            ]
        }
    }
)()

generator = ResumeGenerator()

file = generator.generate(

    profile,

    job,

    decision

)

print("=" * 70)
print("RESUME GENERATED")
print("=" * 70)
print(file)