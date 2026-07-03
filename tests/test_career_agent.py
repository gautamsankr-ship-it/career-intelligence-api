from app.services.career_agent import CareerAgent

agent = CareerAgent()

summary = agent.dashboard_summary()

jobs = summary["jobs"]

print()
print("=" * 90)
print("CAREER INTELLIGENCE DASHBOARD")
print("=" * 90)
print()

print(f"Jobs Found      : {summary['total_jobs']}")
print(f"Approve & Send  : {summary['ready']}")
print(f"Generate Package: {summary['review']}")
print(f"Rejected        : {summary['rejected']}")

print()
print("=" * 90)
print("TOP MATCHES")
print("=" * 90)

for i, job in enumerate(jobs[:20], start=1):

    decision = "-"

    if job.decision:
        decision = job.decision.decision

    print(
        f"{i:02d}. "
        f"{job.raw_score:5.1f} | "
        f"{decision:20} | "
        f"{job.company:30} | "
        f"{job.job_title}"
    )