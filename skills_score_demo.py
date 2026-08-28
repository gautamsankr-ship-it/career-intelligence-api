"""Manual skills-scoring demo — NOT a pytest test.

Reads the real candidate profile via load_candidate_profile(). Must be run
deliberately from the command line only; must never be imported or
collected by pytest.
"""

from app.services.profile_service import load_candidate_profile
from app.services.scoring.skills import calculate_skills_score


def main() -> None:
    candidate = load_candidate_profile()

    job = {
        "required_skills": ["Xero", "Power BI", "SQL", "Financial Reporting"],
    }

    result = calculate_skills_score(candidate, job)
    print(result)


if __name__ == "__main__":
    main()
