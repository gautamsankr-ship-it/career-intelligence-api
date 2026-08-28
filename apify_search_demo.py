"""Manual Apify LinkedIn search demo — NOT a pytest test.

Running this script issues LIVE, PAID Apify scrape requests for every URL
LinkedInURLBuilder produces. It must be run deliberately from the command
line only; it must never be imported or collected by pytest, since import
alone would trigger real external network/API calls and cost money.
"""

from app.services.linkedin_url_builder import LinkedInURLBuilder
from app.services.apify_service import ApifyJobService


def main() -> None:
    builder = LinkedInURLBuilder()
    service = ApifyJobService()

    urls = builder.build_urls()

    print()
    print("=" * 80)
    print("Searching")
    print("=" * 80)

    all_jobs = []

    for search in urls:
        print(search["name"])
        jobs = service.scrape_jobs(search["url"], count=10)
        print("Jobs:", len(jobs))
        all_jobs.extend(jobs)

    print()
    print("=" * 80)
    print("TOTAL JOBS FOUND:", len(all_jobs))
    print("=" * 80)

    for job in all_jobs[:10]:
        print(job["company"])
        print(job["job_title"])
        print(job["location"])
        print(job["job_url"])
        print("-" * 60)


if __name__ == "__main__":
    main()
