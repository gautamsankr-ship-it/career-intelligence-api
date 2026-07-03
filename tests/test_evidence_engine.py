from app.services.evidence_engine import EvidenceEngine

engine = EvidenceEngine()

keywords = [

    "python",

    "forecast",

    "financial",

    "power bi",

    "audit",

    "leadership",

    "openai",

    "django"

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