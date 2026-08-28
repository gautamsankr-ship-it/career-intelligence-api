"""Manual match-scoring demo — NOT a pytest test.

Pure local computation on empty synthetic inputs (no production/network
access), but has no assertions and is not a real test, so it must never be
collected by pytest. Run it deliberately from the command line only.
"""

from app.services.match_service import calculate_match


def main() -> None:
    candidate_profile = {}
    job_analysis = {}

    result = calculate_match(candidate_profile, job_analysis)
    print(result)


if __name__ == "__main__":
    main()
