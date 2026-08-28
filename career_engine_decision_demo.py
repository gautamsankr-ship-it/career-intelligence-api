"""Manual CareerDecisionEngine demo — NOT a pytest test.

Running this script reads the real candidate profile and issues a LIVE
OpenAI API call via analyze_job(). It must be run deliberately from the
command line only; it must never be imported or collected by pytest, since
import alone would trigger real candidate-data access and a billed API call.
"""

from app.services.profile_service import load_candidate_profile
from app.services.ai_service import analyze_job
from app.services.employer_service import EmployerService
from app.services.career_engine import CareerDecisionEngine


def main() -> None:
    candidate = load_candidate_profile()
    print(candidate.keys())
    print(candidate)

    job = analyze_job("""
Financial Data Analyst

Bamboo

Requirements

Power BI
SQL
Excel
Financial Reporting

Minimum 5 years experience.
""")

    employer = EmployerService().analyze(job)

    engine = CareerDecisionEngine()

    decision = engine.evaluate(
        candidate,
        job,
        employer
    )

    print("=" * 60)
    print("Overall Score :", decision.overall_score)
    print("Decision      :", decision.decision)
    print("Confidence    :", decision.confidence)
    print("Priority      :", decision.priority)
    print("=" * 60)

    for card in decision.scorecards:
        print(f"{card.category:<20} {card.score}/{card.weight}")

    print("=" * 60)
    print("Resume Strategy")
    print(decision.resume_strategy)

    print("=" * 60)
    print("Application Strategy")
    print(decision.application_strategy)


if __name__ == "__main__":
    main()
