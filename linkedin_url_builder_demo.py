"""Manual LinkedIn URL builder demo — NOT a pytest test.

Pure local URL construction (no network access), but has no assertions and
is not a real test, so it must never be collected by pytest. Run it
deliberately from the command line only.
"""

from app.services.linkedin_url_builder import LinkedInURLBuilder


def main() -> None:
    builder = LinkedInURLBuilder()
    urls = builder.build_urls()

    print()
    print("=" * 80)

    for item in urls:
        print(item["name"])
        print(item["url"])
        print("-" * 80)


if __name__ == "__main__":
    main()
