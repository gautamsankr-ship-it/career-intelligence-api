"""Manual JobScraper normalization demo — NOT a pytest test.

Reads the static sample fixture app/data/sample_jobs.json (not production
cache/discovery data). No network/production access, but has no assertions
and is not a real test, so it must never be collected by pytest. Run it
deliberately from the command line only.
"""

import json

from app.services.scraper_service import JobScraper


def main() -> None:
    with open("app/data/sample_jobs.json", encoding="utf-8") as f:
        jobs = json.load(f)

    scraper = JobScraper()
    normalized = scraper.normalize(jobs)

    print()
    print("=" * 60)
    print("Jobs Loaded:", len(normalized))
    print("=" * 60)

    for job in normalized:
        print(job["company"], "-", job["job_title"])


if __name__ == "__main__":
    main()
