from app.services.application_service import ApplicationService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.queue_service import QueueService
from app.services.recruiter_reasoning_service import RecruiterReasoningService


class CareerAgent:
    """Batch workflow for discovery, application generation, and queueing."""

    def __init__(self) -> None:
        self.discovery = JobDiscoveryService()
        self.queue = QueueService()
        self.application_service = ApplicationService()
        self.recruiter = RecruiterReasoningService()

    def discover_jobs(self):
        return self.discovery.discover_jobs()

    def process_jobs(self):
        opportunities = self.discovery.discover_jobs()
        processed = []

        print()
        print("=" * 90)
        print("PROCESSING JOBS")
        print("=" * 90)

        for index, opportunity in enumerate(opportunities, start=1):
            print()
            print("=" * 90)
            print(f"JOB {index} OF {len(opportunities)}")
            print("=" * 90)
            print(f"Company : {opportunity.company}")
            print(f"Role    : {opportunity.job_title}")

            try:
                result = self.application_service.generate_documents(
                    opportunity.job_description
                )
                recruiter = self.recruiter.evaluate(
                    result.profile,
                    result.job_analysis,
                    result.employer,
                    result.career_decision,
                )

                opportunity.job_analysis = result.job_analysis
                opportunity.employer = result.employer
                opportunity.decision = result.career_decision
                opportunity.recruiter = recruiter
                opportunity.ats = result.ats_result
                opportunity.resume_improvement = result.resume_strategy
                opportunity.resume_file = result.docx_path
                opportunity.metadata["markdown_file"] = result.markdown_path
                opportunity.raw_score = result.career_decision.overall_score
                opportunity.optimized_score = recruiter.final_score
                opportunity.confidence = result.career_decision.confidence
                opportunity.priority = result.career_decision.priority
                opportunity.automation_level = result.career_decision.automation_level

                queue_item = self.queue.add_application(opportunity)
                opportunity.metadata["queue_status"] = queue_item.status

                print()
                print(f"Career Score      : {result.career_decision.overall_score:.1f}")
                print(f"Recruiter Score   : {recruiter.final_score:.1f}")
                print(
                    "ATS Score         : "
                    f"{result.ats_result['ats_score']['overall_score']:.1f}"
                )
                print(
                    "ATS Rating        : "
                    f"{result.ats_result['ats_score']['recommendation']}"
                )
                print(
                    "Keyword Coverage  : "
                    f"{result.ats_result['keyword_summary']['coverage'] * 100:.1f}%"
                )
                print(f"Interview Chance  : {recruiter.interview_probability:.1f}%")
                print(f"Recommendation    : {recruiter.recommendation}")
                print(f"Risk Level        : {recruiter.risk_level}")

                print()
                print("Resume Focus")
                for item in result.resume_strategy["summary_focus"]:
                    print(f"  - {item}")

                print()
                print("Keywords to Strengthen")
                for item in result.resume_strategy["keywords_to_strengthen"][:5]:
                    print(f"  - {item}")

                print()
                print("Keywords Missing")
                for item in result.resume_strategy["keywords_missing"][:5]:
                    print(f"  - {item}")

                print()
                print("Strengths")
                for strength in recruiter.strengths:
                    print(f"  - {strength}")

                print()
                print("Critical Gaps")
                if recruiter.critical_gaps:
                    for gap in recruiter.critical_gaps:
                        print(f"  - {gap}")
                else:
                    print("  None")

                processed.append(opportunity)

            except Exception as exc:
                print()
                print(f"FAILED : {opportunity.company}")
                print(exc)

        processed.sort(key=lambda opportunity: opportunity.optimized_score, reverse=True)
        return processed

    def dashboard_summary(self):
        jobs = self.process_jobs()
        apply = [
            job
            for job in jobs
            if job.recruiter and job.recruiter.recommendation == "APPLY"
        ]
        review = [
            job
            for job in jobs
            if job.recruiter and job.recruiter.recommendation == "REVIEW"
        ]
        skip = [
            job
            for job in jobs
            if job.recruiter and job.recruiter.recommendation == "SKIP"
        ]
        recruiter_scores = [job.recruiter.final_score for job in jobs if job.recruiter]
        career_scores = [job.raw_score for job in jobs]

        return {
            "total_jobs": len(jobs),
            "career_average": round(sum(career_scores) / len(career_scores), 1)
            if career_scores
            else 0,
            "recruiter_average": round(
                sum(recruiter_scores) / len(recruiter_scores), 1
            )
            if recruiter_scores
            else 0,
            "apply": len(apply),
            "review": len(review),
            "skip": len(skip),
            "highest": max(jobs, key=lambda job: job.optimized_score) if jobs else None,
            "jobs": jobs,
        }
