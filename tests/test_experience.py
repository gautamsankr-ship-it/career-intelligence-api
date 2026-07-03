from app.services.profile_service import load_candidate_profile
from app.services.scoring.experience import calculate_experience_score

candidate = load_candidate_profile()

job = {
    "summary": "Minimum 5 years experience required.",
    "keywords": []
}

result = calculate_experience_score(
    candidate,
    job
)

print(result)