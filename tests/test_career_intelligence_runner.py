import pytest

from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
from app.services.career_intelligence_runner import CareerIntelligenceRunner
from app.services.opportunity_crm_service import OpportunityCRMService


# --- shared fakes -----------------------------------------------------------
class FakeCareerAgent:
    def __init__(self, order=None, on_process=None):
        self.order = order
        self.process_jobs_calls = 0
        self.on_process = on_process

    def process_jobs(self):
        self.process_jobs_calls += 1
        if self.order is not None:
            self.order.append("career_agent")
        if self.on_process:
            self.on_process()
        return []


class FakePackageOrchestrator:
    def __init__(self, prepared_count=0, order=None):
        self.prepared_count = prepared_count
        self.order = order
        self.prepare_ready_calls = []

    def prepare_ready(self, limit=5):
        self.prepare_ready_calls.append(limit)
        if self.order is not None:
            self.order.append("package_orchestrator")
        return [object()] * self.prepared_count


class FakePackage:
    def __init__(self, tracker_id):
        self.tracker_id = tracker_id


class FakeResult:
    def __init__(self, status):
        self.status = status


class FakeExecutionOrchestrator:
    def __init__(self, ready_packages, results_by_tracker, order=None):
        self._ready = ready_packages
        self._results = results_by_tracker  # tracker_id -> FakeResult | Exception | list of either
        self.order = order
        self.execute_calls = []

    def ready(self):
        if self.order is not None:
            self.order.append("execution.ready")
        return self._ready

    def execute(self, tracker_id, mode, headed=False):
        self.execute_calls.append((tracker_id, mode, headed))
        if self.order is not None:
            self.order.append(f"execution.execute[{tracker_id}]")
        value = self._results[tracker_id]
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeReview:
    def __init__(self, review_id, tracker_id, review_status, company="Acme", job_title="Engineer"):
        self.review_id = review_id
        self.tracker_id = tracker_id
        self.review_status = review_status
        self.company = company
        self.job_title = job_title


class FakeFinalReviewService:
    def __init__(self, review_by_tracker, order=None):
        self._review_by_tracker = review_by_tracker
        self.order = order
        self.create_calls = []
        self.approve_calls = []

    def create(self, tracker_id):
        self.create_calls.append(tracker_id)
        if self.order is not None:
            self.order.append(f"final_review.create[{tracker_id}]")
        return self._review_by_tracker[tracker_id]

    def approve(self, review_id, note=""):
        self.approve_calls.append(review_id)


class FakeReceipt:
    def __init__(self, outcome, submission_id="sub-1"):
        self.outcome = outcome
        self.submission_id = submission_id


class FakeSubmissionService:
    def __init__(self, receipt, order=None):
        self.receipt = receipt
        self.order = order
        self.submit_calls = []

    def submit(self, review_id, confirmation):
        self.submit_calls.append((review_id, confirmation))
        if self.order is not None:
            self.order.append("submission.submit")
        return self.receipt


class FakeGmailMonitor:
    def __init__(self, report, order=None):
        self.report = report
        self.order = order
        self.run_calls = 0

    def run(self):
        self.run_calls += 1
        if self.order is not None:
            self.order.append("gmail_monitor.run")
        return self.report


DEFAULT_GMAIL_REPORT = {
    "messages_checked": 0, "matched": 0, "unmatched": 0, "already_processed": 0,
    "acknowledgements": 0, "recruiter_responses": 0, "screening_requests": 0,
    "interviews": 0, "assessments": 0, "rejections": 0, "offers": 0, "human_review": 0,
    "details": [],
}


def no_op_refresh():
    return {"discovered": 0, "unique_verified": 0}


def crm_pair(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    return history, OpportunityCRMService(history)


def make_applied_tracker(crm, external_id, company="Acme", job_title="Engineer"):
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id=external_id)
    record = crm.create_opportunity(fingerprint, company=company, job_title=job_title)
    crm.history.update_record(fingerprint, applied_at="2026-08-31T00:00:00+00:00")
    return record["id"]


def build_runner(tmp_path, **overrides):
    history, crm = crm_pair(tmp_path)
    defaults = dict(
        history=history,
        crm=crm,
        career_agent=FakeCareerAgent(),
        package_orchestrator=FakePackageOrchestrator(),
        execution_orchestrator=FakeExecutionOrchestrator([], {}),
        final_review_service=FakeFinalReviewService({}),
        submission_service=FakeSubmissionService(FakeReceipt("SUBMISSION_CONFIRMED")),
        gmail_monitor=FakeGmailMonitor(dict(DEFAULT_GMAIL_REPORT)),
        refresh_job_cache=no_op_refresh,
        confirm_prompt=lambda review: None,
        skip_discovery=True,
    )
    defaults.update(overrides)
    return crm, CareerIntelligenceRunner(**defaults)


# --- orchestration order ------------------------------------------------
def test_orchestration_runs_in_the_documented_order(tmp_path):
    order = []
    tracker_id = 1
    crm, runner = build_runner(
        tmp_path,
        career_agent=FakeCareerAgent(order=order),
        package_orchestrator=FakePackageOrchestrator(order=order),
        execution_orchestrator=FakeExecutionOrchestrator(
            [FakePackage(tracker_id)], {tracker_id: FakeResult("PREPARED_FOR_FINAL_REVIEW")}, order=order,
        ),
        final_review_service=FakeFinalReviewService(
            {tracker_id: FakeReview("r1", tracker_id, "READY_FOR_HUMAN_REVIEW")}, order=order,
        ),
        submission_service=FakeSubmissionService(FakeReceipt("SUBMISSION_CONFIRMED"), order=order),
        gmail_monitor=FakeGmailMonitor(dict(DEFAULT_GMAIL_REPORT), order=order),
        confirm_prompt=lambda review: f"SUBMIT {review.review_id}",
    )
    make_applied_tracker(crm, "1")

    runner.run()

    assert order == [
        "career_agent", "package_orchestrator", "execution.ready", "execution.execute[1]",
        "final_review.create[1]", "submission.submit", "gmail_monitor.run",
    ]


def test_existing_services_are_reused_via_injection(tmp_path):
    """The runner never constructs its own instances when given explicit
    ones -- proving it orchestrates the caller's existing services rather
    than building parallel ones."""
    career_agent = FakeCareerAgent()
    package_orchestrator = FakePackageOrchestrator()
    _, runner = build_runner(tmp_path, career_agent=career_agent, package_orchestrator=package_orchestrator)
    runner.run()
    assert runner.career_agent is career_agent
    assert runner.package_orchestrator is package_orchestrator
    assert career_agent.process_jobs_calls == 1
    assert package_orchestrator.prepare_ready_calls == [5]


# --- human blocker pause / resume -----------------------------------------
def test_captcha_blocker_pauses_and_never_reaches_final_review(tmp_path):
    history, crm = crm_pair(tmp_path)
    tracker_id = make_applied_tracker(crm, "1")
    final_review = FakeFinalReviewService({})
    _, runner = build_runner(
        tmp_path, history=history, crm=crm,
        execution_orchestrator=FakeExecutionOrchestrator(
            [FakePackage(tracker_id)], {tracker_id: FakeResult("CAPTCHA_REQUIRED")},
        ),
        final_review_service=final_review,
    )

    summary = runner.run()

    blockers = crm.list_open_blockers(tracker_id)
    assert len(blockers) == 1
    assert blockers[0]["blocker_type"] == "HUMAN_CAPTCHA_REQUIRED"
    assert final_review.create_calls == []  # never reached -- blocked upstream
    assert summary.ready_for_human_action == 0
    assert summary.unresolved_human_blockers == 1


def test_blocker_resolves_and_workflow_continues_once_execution_succeeds(tmp_path):
    """Resuming after human resolution continues the SAME opportunity's
    workflow (blocker cleared, review created) rather than restarting
    anything."""
    history, crm = crm_pair(tmp_path)
    tracker_id = make_applied_tracker(crm, "1")
    crm.record_human_blocker(tracker_id, "HUMAN_MFA_REQUIRED", detail="prior run")
    assert len(crm.list_open_blockers(tracker_id)) == 1

    final_review = FakeFinalReviewService({tracker_id: FakeReview("r1", tracker_id, "READY_FOR_HUMAN_REVIEW")})
    _, runner = build_runner(
        tmp_path, history=history, crm=crm,
        execution_orchestrator=FakeExecutionOrchestrator(
            [FakePackage(tracker_id)], {tracker_id: FakeResult("PREPARED_FOR_FINAL_REVIEW")},
        ),
        final_review_service=final_review,
        confirm_prompt=lambda review: None,
    )

    summary = runner.run()

    assert crm.list_open_blockers(tracker_id) == []
    assert final_review.create_calls == [tracker_id]
    assert summary.ready_for_human_action == 1
    assert summary.unresolved_human_blockers == 0


# --- final submit stays human-authorized -----------------------------------
def test_final_submit_never_happens_without_explicit_human_confirmation(tmp_path):
    history, crm = crm_pair(tmp_path)
    tracker_id = make_applied_tracker(crm, "1")
    submission = FakeSubmissionService(FakeReceipt("SUBMISSION_CONFIRMED"))
    _, runner = build_runner(
        tmp_path, history=history, crm=crm,
        execution_orchestrator=FakeExecutionOrchestrator(
            [FakePackage(tracker_id)], {tracker_id: FakeResult("PREPARED_FOR_FINAL_REVIEW")},
        ),
        final_review_service=FakeFinalReviewService({tracker_id: FakeReview("r1", tracker_id, "READY_FOR_HUMAN_REVIEW")}),
        submission_service=submission,
        confirm_prompt=lambda review: None,  # human declines / non-interactive
    )

    summary = runner.run()

    assert submission.submit_calls == []
    assert summary.confirmed_submitted == 0
    assert summary.ready_for_human_action == 1
    assert crm.get_opportunity(tracker_id)["crm_stage"] != "APPLIED"


def test_final_submit_happens_only_with_the_exact_confirmation_phrase(tmp_path):
    history, crm = crm_pair(tmp_path)
    tracker_id = make_applied_tracker(crm, "1")
    submission = FakeSubmissionService(FakeReceipt("SUBMISSION_CONFIRMED"))
    _, runner = build_runner(
        tmp_path, history=history, crm=crm,
        execution_orchestrator=FakeExecutionOrchestrator(
            [FakePackage(tracker_id)], {tracker_id: FakeResult("PREPARED_FOR_FINAL_REVIEW")},
        ),
        final_review_service=FakeFinalReviewService({tracker_id: FakeReview("r1", tracker_id, "READY_FOR_HUMAN_REVIEW")}),
        submission_service=submission,
        confirm_prompt=lambda review: f"SUBMIT {review.review_id}",
    )

    summary = runner.run()

    assert submission.submit_calls == [("r1", "SUBMIT r1")]
    assert summary.confirmed_submitted == 1


# --- APPLIED requires actual confirmation -----------------------------------
def test_applied_is_never_set_on_an_uncertain_submission_outcome(tmp_path):
    history, crm = crm_pair(tmp_path)
    tracker_id = make_applied_tracker(crm, "1")
    submission = FakeSubmissionService(FakeReceipt("SUBMISSION_OUTCOME_UNCERTAIN"))
    _, runner = build_runner(
        tmp_path, history=history, crm=crm,
        execution_orchestrator=FakeExecutionOrchestrator(
            [FakePackage(tracker_id)], {tracker_id: FakeResult("PREPARED_FOR_FINAL_REVIEW")},
        ),
        final_review_service=FakeFinalReviewService({tracker_id: FakeReview("r1", tracker_id, "READY_FOR_HUMAN_REVIEW")}),
        submission_service=submission,
        confirm_prompt=lambda review: f"SUBMIT {review.review_id}",
    )

    summary = runner.run()

    assert summary.confirmed_submitted == 0
    assert crm.get_opportunity(tracker_id)["crm_stage"] != "APPLIED"
    assert any("submission[" in error for error in summary.errors)


def test_applied_is_set_and_idempotent_on_confirmed_submission(tmp_path):
    history, crm = crm_pair(tmp_path)
    tracker_id = make_applied_tracker(crm, "1")
    submission = FakeSubmissionService(FakeReceipt("SUBMISSION_CONFIRMED", submission_id="sub-xyz"))
    final_review = FakeFinalReviewService({tracker_id: FakeReview("r1", tracker_id, "READY_FOR_HUMAN_REVIEW")})
    _, runner = build_runner(
        tmp_path, history=history, crm=crm,
        execution_orchestrator=FakeExecutionOrchestrator(
            [FakePackage(tracker_id)], {tracker_id: FakeResult("PREPARED_FOR_FINAL_REVIEW")},
        ),
        final_review_service=final_review,
        submission_service=submission,
        confirm_prompt=lambda review: f"SUBMIT {review.review_id}",
    )

    summary = runner.run()
    assert summary.confirmed_submitted == 1
    assert crm.get_opportunity(tracker_id)["crm_stage"] == "APPLIED"
    events_after_first = crm.get_timeline(tracker_id)

    # A second confirmed receipt for the same submission must never create a
    # duplicate APPLIED transition/event.
    runner.crm.record_submission_confirmation(
        tracker_id, confirmation_source="BROWSER_SUBMISSION",
        confirmation_evidence="submission_receipt:sub-xyz", submission_reference="sub-xyz",
    )
    assert crm.get_timeline(tracker_id) == events_after_first


# --- failure isolation --------------------------------------------------
def test_failure_in_one_opportunity_does_not_corrupt_others(tmp_path):
    history, crm = crm_pair(tmp_path)
    failing_tracker = make_applied_tracker(crm, "1", company="Broken Co", job_title="Role A")
    healthy_tracker = make_applied_tracker(crm, "2", company="Healthy Co", job_title="Role B")
    final_review = FakeFinalReviewService({healthy_tracker: FakeReview("r2", healthy_tracker, "READY_FOR_HUMAN_REVIEW")})
    _, runner = build_runner(
        tmp_path, history=history, crm=crm,
        execution_orchestrator=FakeExecutionOrchestrator(
            [FakePackage(failing_tracker), FakePackage(healthy_tracker)],
            {failing_tracker: RuntimeError("browser crashed"), healthy_tracker: FakeResult("PREPARED_FOR_FINAL_REVIEW")},
        ),
        final_review_service=final_review,
        confirm_prompt=lambda review: None,
    )

    summary = runner.run()

    assert any("browser crashed" in error for error in summary.errors)
    assert final_review.create_calls == [healthy_tracker]  # the healthy one still proceeded
    assert summary.ready_for_human_action == 1
    assert crm.get_opportunity(failing_tracker)["crm_stage"] != "APPLIED"


# --- summary counts ------------------------------------------------------
def test_summary_counts_reflect_new_records_and_gmail_report(tmp_path):
    history, crm = crm_pair(tmp_path)

    def seed_two_new_records():
        for i, priority in enumerate(("A", "C"), start=1):
            fingerprint = job_fingerprint(source="LinkedIn", external_job_id=f"seed-{i}")
            history.claim_job(fingerprint, status="ELIGIBLE", company=f"Co{i}", job_title=f"Role{i}")
            history.update_record(
                fingerprint, intelligence_priority=priority,
                remote_eligibility="ELIGIBLE" if priority == "A" else None,
            )

    gmail_report = dict(DEFAULT_GMAIL_REPORT)
    gmail_report.update(messages_checked=5, matched=2, acknowledgements=2)

    _, runner = build_runner(
        tmp_path, history=history, crm=crm,
        career_agent=FakeCareerAgent(on_process=seed_two_new_records),
        gmail_monitor=FakeGmailMonitor(gmail_report),
    )

    summary = runner.run()

    assert summary.eligible == 1
    assert summary.priority_a == 1
    assert summary.priority_c == 1
    assert summary.priority_b == summary.priority_d == summary.priority_e == 0
    assert summary.gmail_messages_checked == 5
    assert summary.employer_responses_detected == 2
    assert summary.crm_updates == 2  # 2 acknowledgements recorded by the gmail monitor
    assert summary.discovered is None and summary.unique_verified is None  # skip_discovery=True by default


def test_skip_discovery_never_calls_refresh(tmp_path):
    calls = []
    _, runner = build_runner(tmp_path, refresh_job_cache=lambda: calls.append(1) or {}, skip_discovery=True)
    runner.run()
    assert calls == []


def test_discovery_failure_is_isolated_and_reported(tmp_path):
    def failing_refresh():
        raise RuntimeError("Apify quota exceeded")

    _, runner = build_runner(tmp_path, refresh_job_cache=failing_refresh, skip_discovery=False)
    summary = runner.run()
    assert "Apify quota exceeded" in summary.discovery_error
    assert summary.discovered is None
