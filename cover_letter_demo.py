"""Manual cover-letter generation demo — NOT a pytest test.

Reads the real candidate profile and issues a LIVE OpenAI API call via
generate_cover_letter(). Must be run deliberately from the command line
only; must never be imported or collected by pytest.
"""

from app.services.profile_service import load_candidate_profile
from app.services.match_service import calculate_match
from app.services.cover_letter_service import generate_cover_letter


def main() -> None:
    candidate = load_candidate_profile()

    job = {
        "company": "Bamboo",
        "job_title": "Financial Data Analyst",
        "required_skills": ["Excel", "SQL", "Power BI"],
        "keywords": ["Financial Analysis", "Reporting"],
        "summary": "Financial Data Analyst position.",
    }

    report = calculate_match(candidate, job)
    cover = generate_cover_letter(candidate, job, report)
    print(cover)


if __name__ == "__main__":
    main()
