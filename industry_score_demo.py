"""Manual industry-scoring demo — NOT a pytest test.

Reads the real candidate profile via load_candidate_profile(). Must be run
deliberately from the command line only; must never be imported or
collected by pytest.
"""

from app.services.profile_service import load_candidate_profile
from app.services.scoring.industry import IndustryScorer


def main() -> None:
    candidate = load_candidate_profile()

    job = {
        "summary": "Large accounting and financial services firm providing audit and tax services.",
        "keywords": ["Audit", "Tax", "Financial Reporting"],
    }

    result = IndustryScorer().score(10, candidate, job)
    print(result)


if __name__ == "__main__":
    main()
