from app.services.profile_service import load_candidate_profile
from app.services.match_service import calculate_match
from app.services.cover_letter_service import generate_cover_letter

candidate = load_candidate_profile()

job = {

    "company":"Bamboo",

    "job_title":"Financial Data Analyst",

    "required_skills":[
        "Excel",
        "SQL",
        "Power BI"
    ],

    "keywords":[
        "Financial Analysis",
        "Reporting"
    ],

    "summary":"Financial Data Analyst position."
}

report = calculate_match(
    candidate,
    job
)

cover = generate_cover_letter(
    candidate,
    job,
    report
)

print(cover)