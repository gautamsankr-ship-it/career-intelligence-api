from app.services.profile_service import load_candidate_profile
from app.services.scoring.skills import calculate_skills_score

candidate = load_candidate_profile()

job = {

    "required_skills":[

        "Xero",

        "Power BI",

        "SQL",

        "Financial Reporting"

    ]
}

result = calculate_skills_score(
    candidate,
    job
)

print(result)