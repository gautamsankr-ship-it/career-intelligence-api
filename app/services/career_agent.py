from app.services.application_service import ApplicationService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.queue_service import QueueService
from app.services.application_history_service import (
    ApplicationHistoryService,
    fingerprint_for_opportunity,
)
from app.services.application_email_classifier import (
    ApplicationEmailClassifier,
    EmailClassification,
)
from app.config import SCREENING_AUTO_APPLY, SCREENING_REVIEW, SCREENING_SKIP


class CareerAgent:
    """Batch workflow for discovery, application generation, and queueing."""

    def __init__(self, history_service=None) -> None:
        self.discovery = JobDiscoveryService()
        self.queue = QueueService()
        self.application_service = ApplicationService()
        self.history = history_service or ApplicationHistoryService()
        self.email_classifier = ApplicationEmailClassifier()

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
                fingerprint = fingerprint_for_opportunity(opportunity)
                existing = self.history.get_record(fingerprint)
                if existing and existing["status"] != "FAILED":
                    opportunity.metadata["history_status"] = "DUPLICATE"
                    print(
                        "DUPLICATE: already recorded with status "
                        f"{existing['status']}"
                    )
                    continue

                evaluation = self.application_service.evaluate_job(
                    opportunity.job_description
                )
                recruiter = evaluation.recruiter

                opportunity.job_analysis = evaluation.job_analysis
                opportunity.employer = evaluation.employer
                opportunity.decision = evaluation.career_decision
                opportunity.recruiter = recruiter
                opportunity.ats = evaluation.ats_result
                opportunity.raw_score = evaluation.career_decision.overall_score
                opportunity.optimized_score = recruiter.final_score
                opportunity.confidence = evaluation.career_decision.confidence
                opportunity.priority = evaluation.career_decision.priority
                opportunity.automation_level = evaluation.career_decision.automation_level

                queue_item = self.queue.add_application(opportunity)
                opportunity.metadata["queue_status"] = queue_item.status

                history_status = {
                    SCREENING_SKIP: "SKIPPED",
                    SCREENING_REVIEW: "REVIEW",
                    SCREENING_AUTO_APPLY: "ELIGIBLE",
                }[evaluation.screening_decision]
                application_method = None
                recipient_email = None
                if evaluation.screening_decision == SCREENING_AUTO_APPLY:
                    email_result = self.email_classifier.classify_opportunity(
                        opportunity,
                        evaluation.job_analysis,
                    )
                    opportunity.metadata["email_classification"] = (
                        email_result.classification.value
                    )
                    if (
                        email_result.classification
                        == EmailClassification.EXPLICIT_APPLICATION_EMAIL
                    ):
                        application_method = "EMAIL"
                        recipient_email = email_result.selected_email
                    elif (
                        email_result.classification
                        == EmailClassification.WEB_APPLICATION_ONLY
                        or opportunity.job_url
                    ):
                        application_method = "WEB"
                        history_status = "MANUAL_WEB_REQUIRED"
                    else:
                        # No recipient or web route was found.  Keep the job
                        # visible for a person without implying it is safe to email.
                        history_status = "REVIEW"
                fingerprint, accepted = self.history.record_evaluation(
                    opportunity,
                    evaluation,
                    history_status,
                    application_method=application_method,
                    recipient_email=recipient_email,
                )
                if not accepted:
                    opportunity.metadata["history_status"] = "DUPLICATE"
                    print("DUPLICATE: history claim was already taken")
                    continue
                opportunity.metadata["job_fingerprint"] = fingerprint
                opportunity.status = history_status

                result = None
                if evaluation.screening_decision == SCREENING_AUTO_APPLY:
                    result = self.application_service.generate_application_documents(
                        evaluation
                    )
                if result:
                    opportunity.resume_improvement = result.resume_strategy
                    opportunity.resume_file = result.docx_path
                    opportunity.cover_letter_file = result.cover_letter_docx_path
                    opportunity.metadata["markdown_file"] = result.markdown_path
                    self.history.update_record(
                        fingerprint,
                        resume_path=result.docx_path,
                        cover_letter_path=result.cover_letter_docx_path,
                    )

                print()
                print(f"Career Score      : {evaluation.career_decision.overall_score:.1f}")
                print(f"Recruiter Score   : {recruiter.final_score:.1f}")
                print(
                    "ATS Score         : "
                    f"{evaluation.ats_result['ats_score']['overall_score']:.1f}"
                )
                print(
                    "ATS Rating        : "
                    f"{evaluation.ats_result['ats_score']['recommendation']}"
                )
                print(
                    "Keyword Coverage  : "
                    f"{evaluation.ats_result['keyword_summary']['coverage'] * 100:.1f}%"
                )
                print(f"Interview Chance  : {recruiter.interview_probability:.1f}%")
                print(f"Recommendation    : {recruiter.recommendation}")
                print(f"Risk Level        : {recruiter.risk_level}")

                print()
                if result:
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
                else:
                    print("Documents         : Not generated; screening decision is "
                          f"{evaluation.screening_decision}")

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
                if "fingerprint" in locals():
                    self.history.update_record(
                        fingerprint,
                        status="FAILED",
                        error_message=str(exc),
                        processed_at=self.history._now(),
                    )
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
            if job.decision and job.decision.decision == SCREENING_AUTO_APPLY
        ]
        review = [
            job
            for job in jobs
            if job.decision and job.decision.decision == SCREENING_REVIEW
        ]
        skip = [
            job
            for job in jobs
            if job.decision and job.decision.decision == SCREENING_SKIP
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
