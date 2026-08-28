"""Manual CareerOpportunity timeline demo — NOT a pytest test.

Pure local model manipulation (no production/network access), but has no
assertions and is not a real test, so it must never be collected by pytest.
Run it deliberately from the command line only.
"""

from app.models.career_opportunity import CareerOpportunity


def main() -> None:
    job = CareerOpportunity(
        company="EY",
        job_title="Senior Accountant",
        location="Remote",
        source="LinkedIn",
    )

    job.add_event("DISCOVERED")
    job.update_scores(raw=81.5, optimized=92.4, confidence=95)
    job.add_event("PACKAGE_GENERATED")

    print()
    print(job)
    print()
    print("Timeline")
    print("-" * 50)

    for event in job.timeline:
        print(event.stage, event.timestamp)


if __name__ == "__main__":
    main()
