"""Task 6 batch orchestration for safe, draft-only job applications."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from app.config import SCREENING_AUTO_APPLY, SCREENING_REVIEW, SCREENING_SKIP
from app.services.application_email_classifier import (
    ApplicationEmailClassifier,
    EmailClassification,
)
from app.services.application_history_service import (
    ApplicationHistoryService,
    fingerprint_for_opportunity,
)
from app.services.application_service import ApplicationService
from app.services.gmail_service import GmailService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.remote_work_eligibility import ELIGIBLE, INELIGIBLE, MANUAL_REVIEW, RemoteWorkEligibilityClassifier
from app.services.preview_evaluation_snapshot import PreviewEvaluationSnapshotStore


@dataclass
class AutoApplyRunSummary:
    cached_jobs_available: int = 0
    jobs_scanned: int = 0
    duplicates_skipped: int = 0
    new_jobs_evaluated: int = 0
    skipped: int = 0
    review: int = 0
    auto_apply_eligible: int = 0
    remote_eligible: int = 0
    remote_ineligible: int = 0
    remote_eligibility_review: int = 0
    preview_snapshots_reused: int = 0
    gmail_drafts_created: int = 0
    manual_web_required: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)
    results: list["AutoApplyJobResult"] = field(default_factory=list)


@dataclass(frozen=True)
class AutoApplyJobResult:
    company: str
    job_title: str
    career_score: float | None
    ats_score: float | None
    decision: str | None
    application_method: str | None
    status: str
    job_url: str | None
    tracker_id: int | None = None
    recipient_email: str | None = None
    gmail_message_id: str | None = None
    evaluation_source: str = "FRESH"


@dataclass(frozen=True)
class AutoApplyPreviewResult:
    company: str
    job_title: str
    source: str
    market: str
    job_url: str | None
    career_score: float | None
    ats_score: float | None
    decision: str | None
    work_arrangement: str | None
    remote_eligibility: str | None
    eligibility_reason: str | None
    application_route: str | None
    recommended_action: str
    evaluation_source: str = "FRESH"


@dataclass
class AutoApplyPreviewSummary:
    cached_jobs_available: int = 0
    jobs_scanned: int = 0
    duplicates_skipped: int = 0
    new_jobs_evaluated: int = 0
    failures: list[str] = field(default_factory=list)
    snapshots_saved: int = 0
    results: list[AutoApplyPreviewResult] = field(default_factory=list)


def format_run_results(summary: AutoApplyRunSummary) -> str:
    """Render already-recorded outcomes without triggering new work."""
    lines = ["AUTO-APPLICATION RESULTS"]
    if not summary.results:
        lines.append("No new vacancies evaluated.")
        return "\n".join(lines)

    for index, result in enumerate(summary.results, start=1):
        lines.append(f"\n{index}. {result.company} | {result.job_title}")
        lines.append(f"   Career Score: {_format_score(result.career_score)}")
        if result.ats_score is not None:
            lines.append(f"   ATS Score: {_format_score(result.ats_score)}")
        lines.append(f"   Decision: {result.decision or '-'}")
        lines.append(f"   Route: {result.application_method or '-'}")
        lines.append(f"   Status: {result.status}")
        lines.append(f"   Evaluation Source: {result.evaluation_source}")
        if result.tracker_id is not None:
            lines.append(f"   Tracker ID: {result.tracker_id}")
        if result.job_url:
            lines.append(f"   URL: {result.job_url}")
        if result.status == "MANUAL_WEB_REQUIRED" and result.tracker_id is not None:
            lines.append(f"   Next Action: Apply manually, then run: python job_tracker.py applied {result.tracker_id}")
        if result.status == "DRAFTED":
            if result.recipient_email:
                lines.append(f"   Recipient: {result.recipient_email}")
            if result.gmail_message_id:
                lines.append(f"   Gmail Draft ID: {result.gmail_message_id}")
            lines.append("   Next Action: Review the Gmail draft manually.")
    return "\n".join(lines)


def _format_score(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def format_preview_results(summary: AutoApplyPreviewSummary) -> str:
    """Render a strictly non-mutating operational application preview."""
    lines = ["AUTO-APPLICATION PREVIEW"]
    if not summary.results:
        lines.append("No new vacancies evaluated.")
        return "\n".join(lines)
    for index, result in enumerate(summary.results, start=1):
        lines.extend([
            f"\n{index}. {result.company} | {result.job_title}",
            f"   Source: {result.source or '-'}",
            f"   Market: {result.market or '-'}",
            f"   URL: {result.job_url or '-'}",
            f"   Career Score: {_format_score(result.career_score)}",
            f"   ATS Score: {_format_score(result.ats_score)}",
            f"   Career Decision: {result.decision or '-'}",
            f"   Work Arrangement: {result.work_arrangement or 'UNKNOWN'}",
            f"   Remote Eligibility: {result.remote_eligibility or '-'}",
            f"   Eligibility Reason: {result.eligibility_reason or '-'}",
            f"   Application Route: {result.application_route or '-'}",
            f"   Recommended Action: {result.recommended_action}",
            f"   Evaluation Source: {result.evaluation_source}",
        ])
    return "\n".join(lines)


class AutoApplicationOrchestrator:
    """Process cached vacancies while preserving the 70/78 screening gate."""

    def __init__(
        self,
        discovery_service=None,
        application_service=None,
        history_service=None,
        email_classifier=None,
        gmail_service=None,
        eligibility_classifier=None,
        preview_snapshot_store=None,
    ) -> None:
        self.discovery = discovery_service or JobDiscoveryService()
        self.application_service = application_service or ApplicationService()
        self.history = history_service or ApplicationHistoryService()
        self.email_classifier = email_classifier or ApplicationEmailClassifier()
        # Construction is offline; OAuth only occurs when an explicit email
        # vacancy reaches create_draft_for_application.
        self.gmail = gmail_service or GmailService()
        self.eligibility = eligibility_classifier or RemoteWorkEligibilityClassifier()
        self.preview_snapshots = preview_snapshot_store or PreviewEvaluationSnapshotStore()

    def run(self, opportunities: Iterable | None = None, limit: int | None = None) -> AutoApplyRunSummary:
        jobs = list(
            self.discovery.discover_jobs(limit=None)
            if opportunities is None
            else opportunities
        )
        summary = AutoApplyRunSummary(cached_jobs_available=len(jobs))

        for opportunity in jobs:
            if limit is not None and summary.new_jobs_evaluated >= limit:
                break
            summary.jobs_scanned += 1
            self._process_one(opportunity, summary)
        return summary

    def preview(self, opportunities: Iterable | None = None, limit: int | None = None) -> AutoApplyPreviewSummary:
        """Evaluate cached jobs without history, document, Gmail, or lifecycle mutations."""
        jobs = list(self.discovery.discover_jobs(limit=None) if opportunities is None else opportunities)
        summary = AutoApplyPreviewSummary(cached_jobs_available=len(jobs))
        for opportunity in jobs:
            if limit is not None and summary.new_jobs_evaluated >= limit:
                break
            summary.jobs_scanned += 1
            if self.history.duplicate_record_for_opportunity(opportunity):
                summary.duplicates_skipped += 1
                continue
            summary.new_jobs_evaluated += 1
            try:
                evaluation = self.application_service.evaluate_job(opportunity.job_description)
                decision = evaluation.screening_decision
                eligibility = None
                route = None
                if decision == SCREENING_SKIP:
                    action = "SKIP"
                elif decision == SCREENING_REVIEW:
                    action = "MANUAL_CAREER_REVIEW"
                elif decision == SCREENING_AUTO_APPLY:
                    eligibility = self.eligibility.classify(opportunity)
                    if eligibility.decision == INELIGIBLE:
                        action = "BLOCKED_REMOTE_INELIGIBLE"
                    elif eligibility.decision == MANUAL_REVIEW:
                        action = "REVIEW_REMOTE_ELIGIBILITY"
                    else:
                        email = self.email_classifier.classify_opportunity(opportunity, evaluation.job_analysis)
                        if email.classification == EmailClassification.EXPLICIT_APPLICATION_EMAIL:
                            route, action = "EMAIL", "READY_FOR_EMAIL_DRAFT"
                        elif email.classification == EmailClassification.WEB_APPLICATION_ONLY or opportunity.job_url:
                            route, action = "WEB", "READY_FOR_WEB_APPLICATION"
                        else:
                            action = "MANUAL_CAREER_REVIEW"
                else:
                    raise ValueError(f"Unsupported screening decision: {decision}")
                ats_score = (evaluation.ats_result.get("ats_score") or {}).get("overall_score")
                if self.preview_snapshots.save(
                    opportunity, evaluation, eligibility,
                    self.preview_snapshots.current_scoring_config_hash(self.application_service),
                ):
                    summary.snapshots_saved += 1
                summary.results.append(AutoApplyPreviewResult(
                    company=opportunity.company, job_title=opportunity.job_title,
                    source=opportunity.source, market=getattr(opportunity, "market", ""),
                    job_url=getattr(opportunity, "application_url", "") or opportunity.job_url or None,
                    career_score=evaluation.career_decision.overall_score, ats_score=ats_score,
                    decision=decision, work_arrangement=getattr(opportunity, "work_arrangement", "UNKNOWN"),
                    remote_eligibility=eligibility.decision if eligibility else None,
                    eligibility_reason=eligibility.reason if eligibility else None,
                    application_route=route, recommended_action=action,
                    evaluation_source="FRESH",
                ))
            except Exception as exc:
                summary.failures.append(f"{opportunity.company} — {opportunity.job_title}: {exc}")
        return summary

    def _process_one(self, opportunity, summary: AutoApplyRunSummary) -> None:
        fingerprint = fingerprint_for_opportunity(opportunity)
        existing = self.history.duplicate_record_for_opportunity(opportunity)
        if existing:
            summary.duplicates_skipped += 1
            return

        summary.new_jobs_evaluated += 1
        try:
            snapshot = self.preview_snapshots.get(
                opportunity,
                self.preview_snapshots.current_profile_hash(self.application_service),
                self.preview_snapshots.current_scoring_config_hash(self.application_service),
            )
            evaluation_source = "PREVIEW_SNAPSHOT" if snapshot else "FRESH"
            evaluation = snapshot.evaluation if snapshot else self.application_service.evaluate_job(opportunity.job_description)
            if snapshot:
                summary.preview_snapshots_reused += 1
                self.preview_snapshots.consume(opportunity)
            decision = evaluation.screening_decision
            if decision == SCREENING_SKIP:
                if not self._record_or_count_duplicate(opportunity, evaluation, "SKIPPED", summary):
                    return
                summary.skipped += 1
                self._append_result(opportunity, fingerprint, summary, evaluation_source)
                return
            if decision == SCREENING_REVIEW:
                if not self._record_or_count_duplicate(opportunity, evaluation, "REVIEW", summary):
                    return
                summary.review += 1
                self._append_result(opportunity, fingerprint, summary, evaluation_source)
                return
            if decision != SCREENING_AUTO_APPLY:
                raise ValueError(f"Unsupported screening decision: {decision}")

            summary.auto_apply_eligible += 1
            eligibility = snapshot.eligibility if snapshot and snapshot.eligibility else self.eligibility.classify(opportunity)
            if eligibility.decision == INELIGIBLE:
                summary.remote_ineligible += 1
                self.history.record_evaluation(
                    opportunity, evaluation, "REMOTE_INELIGIBLE",
                    remote_eligibility=eligibility.decision,
                    remote_eligibility_reason=eligibility.reason,
                    remote_eligibility_evidence=eligibility.evidence,
                )
                self._append_result(opportunity, fingerprint, summary, evaluation_source)
                return
            if eligibility.decision == MANUAL_REVIEW:
                summary.remote_eligibility_review += 1
                self.history.record_evaluation(
                    opportunity, evaluation, "REMOTE_ELIGIBILITY_REVIEW",
                    remote_eligibility=eligibility.decision,
                    remote_eligibility_reason=eligibility.reason,
                    remote_eligibility_evidence=eligibility.evidence,
                )
                self._append_result(opportunity, fingerprint, summary, evaluation_source)
                return
            if eligibility.decision == ELIGIBLE:
                summary.remote_eligible += 1
            email_result = self.email_classifier.classify_opportunity(
                opportunity,
                evaluation.job_analysis,
            )
            if email_result.classification == EmailClassification.EXPLICIT_APPLICATION_EMAIL:
                fingerprint, accepted = self.history.record_evaluation(
                    opportunity,
                    evaluation,
                    "ELIGIBLE",
                    application_method="EMAIL",
                    recipient_email=email_result.selected_email,
                    remote_eligibility=eligibility.decision,
                    remote_eligibility_reason=eligibility.reason,
                    remote_eligibility_evidence=eligibility.evidence,
                )
                if not accepted:
                    summary.duplicates_skipped += 1
                    return
                self._draft_email_application(
                    opportunity, evaluation, fingerprint, email_result.selected_email, summary
                )
                self._append_result(opportunity, fingerprint, summary, evaluation_source)
                return

            # Manual-web status is appropriate for an explicit web instruction
            # or a retained vacancy URL.  No address is ever inferred.
            if (
                email_result.classification == EmailClassification.WEB_APPLICATION_ONLY
                or opportunity.job_url
            ):
                status = "MANUAL_WEB_REQUIRED"
                method = "WEB"
                summary.manual_web_required += 1
            else:
                status = "REVIEW"
                method = None
                summary.review += 1
            _, accepted = self.history.record_evaluation(
                opportunity,
                evaluation,
                status,
                application_method=method,
                remote_eligibility=eligibility.decision,
                remote_eligibility_reason=eligibility.reason,
                remote_eligibility_evidence=eligibility.evidence,
            )
            if not accepted:
                summary.duplicates_skipped += 1
                if status == "MANUAL_WEB_REQUIRED":
                    summary.manual_web_required -= 1
                else:
                    summary.review -= 1
                return
            self._append_result(opportunity, fingerprint, summary, evaluation_source)
        except Exception as exc:
            self._record_failure(opportunity, fingerprint, exc)
            summary.failed += 1
            summary.failures.append(f"{opportunity.company} — {opportunity.job_title}: {exc}")
            self._append_result(opportunity, fingerprint, summary, "FRESH")

    def _record_or_count_duplicate(self, opportunity, evaluation, status: str, summary) -> bool:
        _, accepted = self.history.record_evaluation(opportunity, evaluation, status)
        if not accepted:
            summary.duplicates_skipped += 1
        return accepted

    def _draft_email_application(self, opportunity, evaluation, fingerprint: str, recipient: str, summary) -> None:
        result = self.application_service.generate_application_documents(evaluation)
        self.history.update_record(
            fingerprint,
            resume_path=result.docx_path,
            cover_letter_path=result.cover_letter_docx_path,
            processed_at=self.history._now(),
        )
        candidate = (evaluation.profile or {}).get("candidate", {})
        candidate_name = candidate.get("full_name") or "Candidate"
        subject = f"Application for {opportunity.job_title} - {candidate_name}"
        body = (
            "Dear Hiring Team,\n\n"
            "Please find attached my resume and cover letter for the advertised role.\n\n"
            "Kind regards,\n"
            f"{candidate_name}"
        )
        draft_id = self.gmail.create_draft_for_application(
            self.history,
            fingerprint,
            recipient,
            subject,
            body,
            attachments=(result.docx_path, result.cover_letter_docx_path),
        )
        self.history.update_record(fingerprint, processed_at=self.history._now())
        if not draft_id:
            raise RuntimeError("Gmail draft creation did not return an ID.")
        summary.gmail_drafts_created += 1

    def _record_failure(self, opportunity, fingerprint: str, exc: Exception) -> None:
        message = str(exc)
        existing = self.history.get_record(fingerprint)
        if existing:
            self.history.update_record(
                fingerprint,
                status="FAILED",
                error_message=message,
                processed_at=self.history._now(),
            )
            return
        self.history.claim_job(
            fingerprint,
            source=opportunity.source,
            external_job_id=opportunity.id or (opportunity.metadata or {}).get("id"),
            job_url=opportunity.job_url,
            company=opportunity.company,
            job_title=opportunity.job_title,
            location=opportunity.location,
            status="FAILED",
            error_message=message,
            processed_at=self.history._now(),
        )

    def _append_result(self, opportunity, fingerprint: str, summary: AutoApplyRunSummary, evaluation_source: str = "FRESH") -> None:
        record = self.history.get_record(fingerprint)
        if not record:
            return
        summary.results.append(
            AutoApplyJobResult(
                company=record["company"] or opportunity.company,
                job_title=record["job_title"] or opportunity.job_title,
                career_score=record["career_score"],
                ats_score=record["ats_score"],
                decision=record["decision"],
                application_method=record["application_method"],
                status=record["status"],
                job_url=record.get("application_url") or record["job_url"] or getattr(opportunity, "application_url", "") or opportunity.job_url or None,
                tracker_id=record.get("id"),
                recipient_email=record.get("recipient_email"),
                gmail_message_id=record.get("gmail_message_id"),
                evaluation_source=evaluation_source,
            )
        )
