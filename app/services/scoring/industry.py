"""Industry scoring helpers."""
def calculate_industry_score(candidate_profile, job_analysis):

    candidate_industries = {
        industry.lower().strip()
        for industry in candidate_profile.get("industries", [])
    }

    # We will later replace this with AI extraction.
    job_text = (
        job_analysis.get("summary", "") + " " +
        " ".join(job_analysis.get("keywords", []))
    ).lower()

    industry_similarity = {

        "accounting": [
            "audit",
            "tax",
            "financial services",
            "consulting",
            "professional services"
        ],

        "renewable energy": [
            "hydropower",
            "solar",
            "wind",
            "utilities",
            "infrastructure"
        ],

        "financial services": [
            "banking",
            "insurance",
            "fintech",
            "wealth",
            "investment"
        ]
    }

    score = 0
    matched = []

    for candidate in candidate_industries:

        if candidate in job_text:

            score = 10
            matched.append(candidate)
            break

        if candidate in industry_similarity:

            for related in industry_similarity[candidate]:

                if related in job_text:

                    score = 8
                    matched.append(related)
                    break

    if score == 0:

        reason = "No industry match found."

    elif score == 10:

        reason = "Direct industry match."

    else:

        reason = "Related industry match."

    return {

        "score": score,

        "reason": reason,

        "evidence": {

            "matched": matched

        }

    }