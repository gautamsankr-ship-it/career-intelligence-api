from app.services.job_discovery_service import JobDiscoveryService
from app.services.queue_service import QueueService
from app.services.application_service import build_decision
from app.services.profile_service import ProfileService
from app.services.resume_optimizer import ResumeOptimizer
from app.services.resume_generator import ResumeGenerator


class CareerAgent:
    """
    Career Intelligence Orchestrator

    Discovery
        ↓
    Job Analysis
        ↓
    Employer Intelligence
        ↓
    Career Decision
        ↓
    Queue
    """

    def __init__(self):

        self.discovery = JobDiscoveryService()

        self.queue = QueueService()

    def discover_jobs(self):

        return self.discovery.discover_jobs()

    def process_jobs(self):

        # Development mode
        opportunities = self.discovery.discover_jobs()[:10]

        processed = []

        for index, opportunity in enumerate(opportunities, start=1):

            print(
                f"\n[{index}/{len(opportunities)}] "
                f"{opportunity.company}"
            )

            try:

                context = build_decision(
                    opportunity.job_description
                )

                optimizer = ResumeOptimizer()
                optimization = optimizer.optimize(
                    ProfileService().get_profile(),
                    context.job_analysis,
                    context.decision
                )
                generator = ResumeGenerator()

                resume_file = generator.generate(
                    ProfileService().get_profile(),
                    context.job_analysis,
                    optimization
                )
                opportunity.resume_file = resume_file

                print(f"Score: {context.decision.overall_score:.1f}")

                opportunity.job_analysis = context.job_analysis
                opportunity.employer = context.employer
                opportunity.decision = context.decision

                opportunity.raw_score = (
                    context.decision.overall_score
                )

                opportunity.confidence = (
                    context.decision.confidence
                )

                opportunity.priority = (
                    context.decision.priority
                )

                opportunity.automation_level = (
                    context.decision.automation_level
                )

                queue_item = self.queue.add_application(
                    opportunity
                )

                opportunity.metadata["queue_status"] = (
                    queue_item.status
                )

                processed.append(opportunity)

            except Exception as ex:

                print(
                    f"FAILED : {opportunity.company}"
                )

                print(ex)

        processed.sort(
            key=lambda x: x.raw_score,
            reverse=True
        )

        return processed

    def dashboard_summary(self):

        jobs = self.process_jobs()

        ready = len([
            j
            for j in jobs
            if j.decision
            and j.decision.decision == "APPROVE_AND_SEND"
        ])

        review = len([
            j
            for j in jobs
            if j.decision
            and j.decision.decision == "GENERATE_AND_QUEUE"
        ])

        rejected = len([
            j
            for j in jobs
            if j.decision
            and j.decision.decision == "REJECT"
        ])

        return {

            "total_jobs": len(jobs),

            "ready": ready,

            "review": review,

            "rejected": rejected,

            "jobs": jobs

        }