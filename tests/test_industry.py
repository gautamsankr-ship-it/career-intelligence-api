from app.services.profile_service import load_candidate_profile
from app.services.scoring.industry import IndustryScorer

candidate = load_candidate_profile()

job = {

    "summary":
    "Large accounting and financial services firm providing audit and tax services.",

    "keywords":[
        "Audit",
        "Tax",
        "Financial Reporting"
    ]

}

result = IndustryScorer().score(
    10,
    candidate,
    job
)

print(result)