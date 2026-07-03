"""Skills scoring helpers."""
def calculate_skills_score(candidate_profile, job_analysis):

    candidate_skills = []

    skills = candidate_profile.get("skills", {})

    for category in skills.values():

        candidate_skills.extend(category)

    candidate_skills = {
        skill.lower().strip()
        for skill in candidate_skills
    }

    job_skills = {
        skill.lower().strip()
        for skill in job_analysis.get("required_skills", [])
    }

    matched = sorted(candidate_skills & job_skills)
    missing = sorted(job_skills - candidate_skills)

    if len(job_skills) == 0:

        score = 0

    else:

        score = round(
            len(matched) / len(job_skills) * 25
        )

    return {
        "score": score,
        "matched": matched,
        "missing": missing
    }
