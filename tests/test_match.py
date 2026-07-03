from app.services.match_service import calculate_match

candidate_profile = {}

job_analysis = {}

result = calculate_match(candidate_profile, job_analysis)

print(result)