"""Manual job-discovery dashboard — NOT a pytest test.

Running this script loads the local job cache (no network call while
USE_CACHE=True) and prints every unique job found. It must be run
deliberately from the command line only; it must never be imported or
collected by pytest.
"""

from app.services.job_discovery_service import JobDiscoveryService


def main() -> None:
    service = JobDiscoveryService()

    jobs = service.discover_jobs()

    print("\n")
    print("=" * 80)
    print("UNIQUE JOBS:", len(jobs))
    print("=" * 80)

    for job in jobs[:20]:
        print(job.company)
        print(job.job_title)
        print(job.location)
        print("-" * 60)


if __name__ == "__main__":
    main()
