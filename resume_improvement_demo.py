"""Manual resume-improvement scoring demo — NOT a pytest test.

Pure local computation (no network/production access), but has no
assertions and is not a real test, so it must never be collected by pytest.
Run it deliberately from the command line only.
"""

from app.services.resume_improvement_service import ResumeImprovementService


def main() -> None:
    service = ResumeImprovementService()

    result = service.evaluate(
        raw_score=78.2,
        optimized_score=89.5
    )

    print()
    print("=" * 60)
    print("AI IMPROVEMENT")
    print("=" * 60)
    print()

    print("Raw Score:", result.raw_score)
    print("Optimized Score:", result.optimized_score)
    print("Improvement:", result.improvement)
    print("Confidence:", result.confidence)
    print()

    for item in result.improvements:
        print(f"+{item.points:>4}  {item.category}")
        print(f"      {item.explanation}")

    print()
    print(result.summary)


if __name__ == "__main__":
    main()
