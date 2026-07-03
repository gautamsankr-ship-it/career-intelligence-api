"""Experience scoring helpers."""
def calculate_experience_score(candidate_profile, job_analysis):

    candidate_years = candidate_profile["experience"]["total_years"]

    text = (
        job_analysis.get("summary", "") + " " +
        " ".join(job_analysis.get("keywords", []))
    ).lower()

    required_years = 0

    for n in range(1, 21):
        if f"{n} year" in text or f"{n}+ year" in text:
            required_years = n

    if required_years == 0:
        score = 15

        reason = "No experience requirement specified."

    elif candidate_years >= required_years:

        score = 15

        reason = (
            f"{candidate_years} years exceeds "
            f"required {required_years} years."
        )

    else:

        score = round(
            candidate_years / required_years * 15
        )

        reason = (
            f"{candidate_years} years compared to "
            f"required {required_years} years."
        )

    return {

        "score": score,

        "reason": reason,

        "evidence": {

            "candidate_years": candidate_years,

            "required_years": required_years

        }

    }