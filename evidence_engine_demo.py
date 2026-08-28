"""Manual EvidenceEngine keyword-scoring demo — NOT a pytest test.

Reads the real candidate profile/evidence data via EvidenceEngine. Must be
run deliberately from the command line only; must never be imported or
collected by pytest.
"""

from app.services.evidence_engine import EvidenceEngine


def main() -> None:
    engine = EvidenceEngine()

    keywords = [
        "python", "forecast", "financial", "power bi",
        "audit", "leadership", "openai", "django",
    ]

    print("=" * 70)
    print("EVIDENCE ENGINE")
    print("=" * 70)

    for keyword in keywords:
        result = engine.evidence_score(keyword)
        print()
        print("-" * 60)
        print(keyword.upper())
        print("Evidence Score :", result["score"])
        print("Skills         :", len(result["evidence"]["skills"]))
        print("Projects       :", len(result["evidence"]["projects"]))
        print("Responsibilities :", len(result["evidence"]["responsibilities"]))
        print("Achievements   :", len(result["evidence"]["achievements"]))


if __name__ == "__main__":
    main()
