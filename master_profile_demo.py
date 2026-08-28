"""Manual master-profile inspection demo — NOT a pytest test.

Loads the real production candidate profile via MasterProfileService. Must
be run deliberately from the command line only; must never be imported or
collected by pytest.
"""

from app.services.master_profile_service import MasterProfileService


def main() -> None:
    profile = MasterProfileService().load()

    print()
    print("=" * 60)
    print(profile["candidate"]["full_name"])
    print(profile["candidate"]["title"])
    print(profile["experience"]["years"])
    print(profile["future_education"]["planned_degree"])
    print("=" * 60)


if __name__ == "__main__":
    main()
