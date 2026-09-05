"""Task 21.35: Production Automation Runner / Operational MVP.

Pure integration: this module invents no new scoring, eligibility, browser
automation, or CRM logic of its own. It only calls the existing production
services, in the existing sequence each already assumes (discovery cache ->
`CareerAgent` -> `ApplicationPackageOrchestrator` -> `OpportunityCRMService`
-> `ApplicationExecutionOrchestrator` -> `FinalReviewService` ->
`ApplicationSubmissionService` -> `GmailOutcomeMonitor`), and folds their
results into one operational summary.

Safety, preserved exactly as each underlying service already enforces it --
this module adds no bypass of any of it:
  * `ApplicationExecutionOrchestrator` has no submit path at all (`PREPARE`
    fills a form; it never clicks Submit). A CAPTCHA/MFA/auth/unknown-field/
    unsupported-portal result is recorded as an open CRM human blocker and
    that opportunity is left alone -- never retried automatically within the
    same run, never blocks any other opportunity.
  * The only click-capable path is `ApplicationSubmissionService.submit()`,
    which this module calls only after `_confirm_prompt` returns the exact
    `"SUBMIT <review_id>"` phrase that service itself requires (the same
    phrase `application_submit.py` already prompts a human for) -- by
    default via a real interactive `input()` a human must type into. No
    flag exists anywhere in this module to bypass that prompt.
  * A tracker is only ever advanced to CRM stage APPLIED after the
    submission service itself reports `SUBMISSION_CONFIRMED` -- this module
    never marks anything APPLIED on its own judgment, and
    `OpportunityCRMService.record_submission_confirmation` is idempotent, so
    a repeated confirmed receipt is a safe no-op, not a duplicate event.
  * Gmail access happens only through `GmailOutcomeMonitor`, already
    read-only end to end (Task 21.34) -- nothing here grants it any other
    capability.
  * Every stage is wrapped so one opportunity's exception is recorded in
    `RunSummary.errors` and processing continues with the next opportunity --
    a single failure never aborts the run or corrupts unrelated records.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.services.application_execution_orchestrator import ApplicationExecutionOrchestrator
from app.services.application_history_service import ApplicationHistoryService
from app.services.application_package_orchestrator import ApplicationPackageOrchestrator
from app.services.application_submission_service import LOCK_DIR, RECEIPT_DIR, ApplicationSubmissionService
from app.services.career_agent import CareerAgent
from app.services.final_review_service import FinalReviewService
from app.services.gmail_outcome_monitor_service import GmailOutcomeMonitor
from app.services.opportunity_crm_service import OpportunityCRMService

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Genuine browser-automation blockers -> the existing, already-defined CRM
# human-blocker vocabulary (app.models.crm.HUMAN_BLOCKER_TYPES). Never
# invents a new blocker taxonomy; AUTH_REQUIRED is filed under
# HUMAN_MFA_REQUIRED -- both are "a human must be present in the browser
# session to authenticate" -- rather than adding a distinct type for it.
_BLOCKER_STATUS_TO_TYPE = {
    "CAPTCHA_REQUIRED": "HUMAN_CAPTCHA_REQUIRED",
    "MFA_REQUIRED": "HUMAN_MFA_REQUIRED",
    "AUTH_REQUIRED": "HUMAN_MFA_REQUIRED",
    "MANUAL_INPUT_REQUIRED": "HUMAN_ANSWER_APPROVAL_REQUIRED",
    "ACCOUNT_CREATION_REQUIRED": "OTHER",
    "UNSUPPORTED_PORTAL": "OTHER",
    "ROUTE_UNRESOLVED": "OTHER",
    "DIRECT_ROUTE_REQUIRED": "OTHER",
    "PACKAGE_REFRESH_REQUIRED": "OTHER",
    "BROWSER_ERROR": "OTHER",
}
_READY_FOR_REVIEW_STATUSES = {"PREPARED_FOR_FINAL_REVIEW", "FINAL_REVIEW_MANUAL_CONFIRMATION_REQUIRED"}
_PRIORITY_FIELD = {"A": "priority_a", "B": "priority_b", "C": "priority_c", "D": "priority_d", "E": "priority_e"}
_GMAIL_CLASSIFICATION_FIELDS = (
    "acknowledgements", "recruiter_responses", "screening_requests",
    "interviews", "assessments", "rejections", "offers",
)


@dataclass
class RunSummary:
    discovered: int | None = None
    unique_verified: int | None = None
    eligible: int = 0
    priority_a: int = 0
    priority_b: int = 0
    priority_c: int = 0
    priority_d: int = 0
    priority_e: int = 0
    packages_prepared: int = 0
    ready_for_human_action: int = 0
    confirmed_submitted: int = 0
    gmail_messages_checked: int = 0
    employer_responses_detected: int = 0
    crm_updates: int = 0
    unresolved_human_blockers: int = 0
    discovery_error: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "vacancies_discovered": self.discovered,
            "unique_verified": self.unique_verified,
            "eligible": self.eligible,
            "priority_a_priority_apply": self.priority_a,
            "priority_b_apply": self.priority_b,
            "priority_c_human_review": self.priority_c,
            "priority_d_watch": self.priority_d,
            "priority_e_reject": self.priority_e,
            "packages_prepared": self.packages_prepared,
            "ready_for_human_action": self.ready_for_human_action,
            "confirmed_applications_submitted": self.confirmed_submitted,
            "gmail_messages_checked": self.gmail_messages_checked,
            "employer_responses_detected": self.employer_responses_detected,
            "crm_updates": self.crm_updates,
            "unresolved_human_blockers": self.unresolved_human_blockers,
            "discovery_error": self.discovery_error,
            "errors": self.errors,
        }


def _refresh_job_cache(timeout: int = 900) -> dict:
    """Invoke the existing, unmodified `refresh_jobs.py` (live multi-source
    discovery -> dedup -> quality gate -> cache) as a subprocess -- reusing
    that script exactly as a human operator already runs it, rather than
    re-implementing its discovery/dedup/quality-gate logic a second time."""
    completed = subprocess.run(
        [sys.executable, "refresh_jobs.py"], cwd=PROJECT_ROOT,
        capture_output=True, text=True, timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"refresh_jobs.py failed (exit {completed.returncode}): {completed.stderr[-2000:]}")
    stdout = completed.stdout
    raw_match = re.search(r"Total raw:\s*(\d+)", stdout)
    normalized_match = re.search(r"Normalized jobs:\s*(\d+)", stdout)
    return {
        "discovered": int(raw_match.group(1)) if raw_match else None,
        "unique_verified": int(normalized_match.group(1)) if normalized_match else None,
        "stdout": stdout,
    }


def _default_confirm_prompt(review) -> str | None:
    """The one place a human authorizes a real submission. Returns the
    typed confirmation only if it exactly matches what
    `ApplicationSubmissionService.submit` itself requires; any other input
    (including a blank line, e.g. no interactive terminal) leaves the
    opportunity in READY_FOR_HUMAN_REVIEW for a later run or a direct
    `application_review.py` / `application_submit.py` call."""
    expected = f"SUBMIT {review.review_id}"
    prompt = (
        f"\nReady for final submit -- Tracker {review.tracker_id}: {review.company} / {review.job_title}\n"
        f"Type exactly '{expected}' to authorize submission now, or press Enter to leave for later: "
    )
    try:
        response = input(prompt).strip()
    except (EOFError, OSError):
        return None
    return response if response == expected else None


class CareerIntelligenceRunner:
    def __init__(
        self,
        history: ApplicationHistoryService | None = None,
        career_agent: CareerAgent | None = None,
        crm: OpportunityCRMService | None = None,
        package_orchestrator: ApplicationPackageOrchestrator | None = None,
        execution_orchestrator: ApplicationExecutionOrchestrator | None = None,
        final_review_service: FinalReviewService | None = None,
        submission_service: ApplicationSubmissionService | None = None,
        gmail_monitor: GmailOutcomeMonitor | None = None,
        refresh_job_cache=None,
        confirm_prompt=None,
        package_prepare_limit: int = 5,
        headed: bool = False,
        skip_discovery: bool = False,
    ) -> None:
        self.history = history or ApplicationHistoryService()
        self.career_agent = career_agent or CareerAgent(history_service=self.history)
        self.crm = crm or OpportunityCRMService(self.history)
        self.package_orchestrator = package_orchestrator or ApplicationPackageOrchestrator(history=self.history)
        self.execution = execution_orchestrator or ApplicationExecutionOrchestrator(package_service=self.package_orchestrator)
        self.final_review = final_review_service or FinalReviewService(package_service=self.package_orchestrator)
        self.submission = submission_service or ApplicationSubmissionService(
            review_service=self.final_review, receipt_dir=RECEIPT_DIR, lock_dir=LOCK_DIR,
        )
        self.gmail_monitor = gmail_monitor or GmailOutcomeMonitor(crm=self.crm)
        self.refresh_job_cache = refresh_job_cache or _refresh_job_cache
        self.confirm_prompt = confirm_prompt or _default_confirm_prompt
        self.package_prepare_limit = package_prepare_limit
        self.headed = headed
        self.skip_discovery = skip_discovery

    # -- orchestration -----------------------------------------------------
    def run(self) -> RunSummary:
        summary = RunSummary()
        self._discover(summary)
        self._evaluate_and_prioritize(summary)
        self._prepare_packages(summary)
        try:
            self.crm.migrate_legacy_records()
        except Exception as exc:
            summary.errors.append(f"crm.migrate_legacy_records: {exc}")
        self._execute_browser_workflow(summary)
        self._gmail_outcomes(summary)
        try:
            summary.unresolved_human_blockers = len(self.crm.list_open_blockers())
        except Exception as exc:
            summary.errors.append(f"crm.list_open_blockers: {exc}")
        return summary

    def gmail_only(self) -> dict:
        return self.gmail_monitor.run()

    def status_report(self) -> dict:
        return {
            "funnel_counts": self.crm.funnel_counts(),
            "pipeline_counts": self.crm.pipeline_counts(),
            "needs_attention": self.crm.needs_attention(),
            "open_blockers": self.crm.list_open_blockers(),
        }

    # -- 1. discovery --------------------------------------------------------
    def _discover(self, summary: RunSummary) -> None:
        if self.skip_discovery:
            return
        try:
            result = self.refresh_job_cache()
        except Exception as exc:
            summary.discovery_error = str(exc)
            return
        summary.discovered = result.get("discovered")
        summary.unique_verified = result.get("unique_verified")

    # -- 2-5. dedupe / hard eligibility / scoring / A-E priority --------------
    def _evaluate_and_prioritize(self, summary: RunSummary) -> None:
        max_id_before = self._max_history_id()
        try:
            self.career_agent.process_jobs()
        except Exception as exc:
            summary.errors.append(f"career_agent.process_jobs: {exc}")
        for record in self._new_records_since(max_id_before):
            if record.get("remote_eligibility") == "ELIGIBLE":
                summary.eligible += 1
            field_name = _PRIORITY_FIELD.get(record.get("intelligence_priority"))
            if field_name:
                setattr(summary, field_name, getattr(summary, field_name) + 1)

    def _max_history_id(self) -> int:
        row = self.history.connection.execute("SELECT COALESCE(MAX(id), 0) FROM application_history").fetchone()
        return row[0]

    def _new_records_since(self, max_id: int) -> list[dict]:
        return [
            dict(row) for row in
            self.history.connection.execute("SELECT * FROM application_history WHERE id > ?", (max_id,))
        ]

    # -- 6. package preparation ----------------------------------------------
    def _prepare_packages(self, summary: RunSummary) -> None:
        try:
            packages = self.package_orchestrator.prepare_ready(limit=self.package_prepare_limit)
            summary.packages_prepared = len(packages)
        except Exception as exc:
            summary.errors.append(f"package_orchestrator.prepare_ready: {exc}")

    # -- 8-12. browser execution / human blockers / final submit -------------
    def _execute_browser_workflow(self, summary: RunSummary) -> None:
        try:
            ready_packages = list(self.execution.ready())
        except Exception as exc:
            summary.errors.append(f"execution.ready: {exc}")
            return
        for package in ready_packages:
            tracker_id = package.tracker_id
            try:
                result = self.execution.execute(tracker_id, "PREPARE", headed=self.headed)
            except Exception as exc:
                summary.errors.append(f"execution.execute[{tracker_id}]: {exc}")
                continue
            try:
                self._handle_execution_result(tracker_id, result, summary)
            except Exception as exc:
                summary.errors.append(f"execution_followup[{tracker_id}]: {exc}")

    def _handle_execution_result(self, tracker_id: int, result, summary: RunSummary) -> None:
        status = result.status
        blocker_type = _BLOCKER_STATUS_TO_TYPE.get(status)
        if blocker_type:
            self.crm.record_human_blocker(tracker_id, blocker_type, detail=f"Browser execution status: {status}")
            summary.crm_updates += 1
            return
        if status not in _READY_FOR_REVIEW_STATUSES:
            # Not a recognized blocker and not a success state (e.g. the
            # tracker already reached a terminal status) -- nothing to do.
            return

        # Execution is healthy again -- resolve any blocker left open by a
        # previous run rather than leaving stale state behind. Continues the
        # SAME opportunity's workflow rather than restarting the pipeline.
        open_blockers = self.crm.list_open_blockers(tracker_id)
        for blocker in open_blockers:
            self.crm.resolve_human_blocker(
                blocker["id"], resolution_note=f"Cleared: execution now {status}", resolved_by="SYSTEM",
            )
            summary.crm_updates += 1

        review = self.final_review.create(tracker_id)
        if review.review_status != "READY_FOR_HUMAN_REVIEW":
            return
        summary.ready_for_human_action += 1

        confirmation = self.confirm_prompt(review)
        if not confirmation:
            return  # left for a later run or a direct application_submit.py call

        self.final_review.approve(review.review_id)
        receipt = self.submission.submit(review.review_id, confirmation)
        if receipt.outcome == "SUBMISSION_CONFIRMED":
            summary.confirmed_submitted += 1
            # ApplicationSubmissionService already marked the legacy
            # status/application_status APPLIED; this reconciles the newer
            # crm_stage lifecycle with the SAME confirmed evidence -- an
            # idempotent, additive call, never a second submission decision.
            self.crm.record_submission_confirmation(
                tracker_id, confirmation_source="BROWSER_SUBMISSION",
                confirmation_evidence=f"submission_receipt:{receipt.submission_id}",
                submission_reference=receipt.submission_id,
            )
            summary.crm_updates += 1
        else:
            summary.errors.append(f"submission[{tracker_id}]: {receipt.outcome}")

    # -- 13-14. Gmail outcome monitoring --------------------------------------
    def _gmail_outcomes(self, summary: RunSummary) -> None:
        try:
            report = self.gmail_monitor.run()
        except Exception as exc:
            summary.errors.append(f"gmail_monitor.run: {exc}")
            return
        summary.gmail_messages_checked = report.get("messages_checked", 0)
        summary.employer_responses_detected = report.get("matched", 0)
        summary.crm_updates += sum(report.get(key, 0) for key in _GMAIL_CLASSIFICATION_FIELDS)
        summary.crm_updates += report.get("human_review", 0)
