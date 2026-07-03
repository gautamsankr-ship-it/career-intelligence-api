from app.services.career_agent import CareerAgent


def main():

    print("=" * 80)
    print("CAREER INTELLIGENCE - APPLICATION PIPELINE")
    print("=" * 80)

    agent = CareerAgent()

    dashboard = agent.dashboard_summary()

    print("\n")
    print("=" * 80)
    print("READY TO APPLY")
    print("=" * 80)

    for job in dashboard["jobs"]:

        if not job.decision:
            continue

        print(f"\nCompany      : {job.company}")
        print(f"Position     : {job.job_title}")
        print(f"Score        : {job.raw_score:.1f}")
        print(f"Decision     : {job.decision.decision}")
        print(f"Resume       : {job.resume_file}")
        print(f"Cover Letter : {job.cover_letter_file}")

    print("\n")
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Jobs Processed : {dashboard['total_jobs']}")
    print(f"Ready          : {dashboard['ready']}")
    print(f"Review         : {dashboard['review']}")
    print(f"Rejected       : {dashboard['rejected']}")


if __name__ == "__main__":
    main()