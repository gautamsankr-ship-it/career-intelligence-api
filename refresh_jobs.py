from app.config import MAX_JOBS

from app.services.linkedin_url_builder import LinkedInURLBuilder
from app.services.apify_service import ApifyJobService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.cache_service import CacheService


def main():

    print("\n" + "=" * 80)
    print("REFRESHING LINKEDIN JOB CACHE")
    print("=" * 80)

    builder = LinkedInURLBuilder()

    scraper = ApifyJobService()

    discovery = JobDiscoveryService()

    cache = CacheService()

    searches = builder.build_urls()

    urls = [

        search["url"]

        for search in searches

    ]

    print(f"\nSearching {len(urls)} LinkedIn URLs...")

    jobs = scraper.scrape_jobs(

        urls,

        count=100

    )

    print(f"\nDownloaded {len(jobs)} jobs.")

    jobs = discovery.remove_duplicates(jobs)

    print(f"After duplicate removal : {len(jobs)}")

    jobs = discovery.filter_remote_jobs(jobs)

    print(f"Remote jobs : {len(jobs)}")

    jobs.sort(

        key=lambda x: x.posted_date,

        reverse=True

    )

    cache.save_jobs(jobs)

    print("\nCache updated successfully.")

    print(f"Saved {len(jobs)} remote jobs.")

    print(f"Development will process first {MAX_JOBS} jobs.")

    print("=" * 80)


if __name__ == "__main__":

    main()