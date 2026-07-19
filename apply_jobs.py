from app.services.career_agent import CareerAgent


def main():

    print()
    print("=" * 100)
    print("CAREER INTELLIGENCE PLATFORM")
    print("=" * 100)

    agent = CareerAgent()

    dashboard = agent.dashboard_summary()

    print()
    print("=" * 100)
    print("TOP OPPORTUNITIES")
    print("=" * 100)

    for job in dashboard["jobs"]:

        recruiter = job.recruiter

        if recruiter is None:
            continue

        print()

        print("-" * 100)

        print(f"Company             : {job.company}")
        print(f"Position            : {job.job_title}")

        print(f"Career Score        : {job.raw_score:.1f}")

        print(f"Recruiter Score     : {recruiter.final_score:.1f}")

        print(
            f"Interview Chance    : "
            f"{recruiter.interview_probability:.1f}%"
        )

        print(
            f"Recommendation      : "
            f"{recruiter.recommendation}"
        )

        print(
            f"Risk                : "
            f"{recruiter.risk_level}"
        )

        print(
            f"Resume              : "
            f"{job.resume_file}"
        )

        print()

        print("Strengths")

        for item in recruiter.strengths:

            print(f"  ✓ {item}")

        print()

        print("Critical Gaps")

        if recruiter.critical_gaps:

            for gap in recruiter.critical_gaps:

                print(f"  • {gap}")

        else:

            print("  None")

    print()

    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)

    print(f"Jobs Processed      : {dashboard['total_jobs']}")

    print(f"Apply               : {dashboard['apply']}")

    print(f"Review              : {dashboard['review']}")

    print(f"Skip                : {dashboard['skip']}")

    print(
        f"Average Career      : "
        f"{dashboard['career_average']}"
    )

    print(
        f"Average Recruiter   : "
        f"{dashboard['recruiter_average']}"
    )

    if dashboard["highest"]:

        print()

        print("BEST OPPORTUNITY")

        print(
            f"Company             : "
            f"{dashboard['highest'].company}"
        )

        print(
            f"Role                : "
            f"{dashboard['highest'].job_title}"
        )

        print(
            f"Recruiter Score     : "
            f"{dashboard['highest'].optimized_score:.1f}"
        )


if __name__ == "__main__":

    main()