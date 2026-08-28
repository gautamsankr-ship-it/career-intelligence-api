"""Manual production dashboard for CareerAgent — NOT a pytest test.

Running this script executes CareerAgent().dashboard_summary() against the
live production tracker database (app/data/application_history.db) and can
write new tracker records / generate application documents for AUTO_APPLY
jobs found in the cached job list. Run it deliberately from the command
line only; it must never be imported or collected by pytest.
"""

from app.services.career_agent import CareerAgent


def main() -> None:
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


if __name__ == "__main__":
    main()
