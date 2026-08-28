"""Manual ProfileIntelligenceService demo — NOT a pytest test.

Loads the real production candidate profile via ProfileIntelligenceService.
Must be run deliberately from the command line only; must never be imported
or collected by pytest.
"""

from app.services.profile_intelligence_service import ProfileIntelligenceService


def main() -> None:
    profile = ProfileIntelligenceService()

    print("=" * 70)
    print("PROFILE INTELLIGENCE")
    print("=" * 70)
    print("Total Capabilities :", len(profile.get_capabilities()))
    print()

    keywords = ["python", "power bi", "forecast", "audit", "openai", "financial"]

    for keyword in keywords:
        print("-" * 60)
        print("Searching:", keyword)
        print("Skill Exists :", profile.has_skill(keyword))
        print("Projects     :", len(profile.search_projects(keyword)))
        print("Responsibilities :", len(profile.search_responsibilities(keyword)))
        print("Achievements :", len(profile.search_achievements(keyword)))

    print("=" * 70)


if __name__ == "__main__":
    main()
