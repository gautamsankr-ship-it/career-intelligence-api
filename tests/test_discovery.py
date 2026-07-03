from app.services.job_discovery_service import JobDiscoveryService

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