import json

from app.services.scraper_service import JobScraper

with open(
    "app/data/sample_jobs.json",
    encoding="utf-8"
) as f:

    jobs = json.load(f)

scraper = JobScraper()

normalized = scraper.normalize(jobs)

print()

print("=" * 60)

print("Jobs Loaded:", len(normalized))

print("=" * 60)

for job in normalized:

    print(job["company"], "-", job["job_title"])