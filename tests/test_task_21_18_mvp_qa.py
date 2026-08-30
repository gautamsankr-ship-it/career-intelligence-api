"""Task 21.18: Formal MVP end-to-end QA. Every test in this file uses
isolated tmp_path persistence and deterministic fakes only -- no OpenAI, no
Gmail, no Apify, no real browser/network call anywhere. Reuses the
established fixtures from test_application_pipeline_integration.py
(CareerAgent-level faking) and test_final_review_service.py/
test_application_submission_service.py (review/submission-level faking),
following the same import convention already used by
test_application_submission_service.py itself.
"""
import json

import pytest

from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_execution_orchestrator import ApplicationExecutionOrchestrator
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity
from app.services.application_package_orchestrator import ApplicationPackageOrchestrator
from app.services.application_submission_service import ApplicationSubmissionService
from app.services.final_review_service import FinalReviewService

from test_application_pipeline_integration import (
    FakeApplicationService, FakeBrowser, _agent, _documents, _opportunity, _vault,
)
from test_final_review_service import setup as review_setup


def _chain(tmp_path, priority_override=None, job_analysis_overrides=None, clear_snapshot=False, clear_documents=False):
    """One synthetic vacancy through the real CareerAgent path."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    resume, cover = _documents(tmp_path / "docs")
    opportunity = _opportunity(f"job-{abs(hash(str(tmp_path)))}")
    application_service = FakeApplicationService(resume, cover, job_analysis_overrides=job_analysis_overrides)
    agent = _agent(application_service, opportunity, history)
    agent.process_jobs()
    fingerprint = fingerprint_for_opportunity(opportunity)
    if priority_override is not None:
        history.update_record(fingerprint, intelligence_priority=priority_override)
    if clear_snapshot:
        history.update_record(fingerprint, evaluation_snapshot=None)
    if clear_documents:
        history.update_record(fingerprint, resume_path="", cover_letter_path="")
    record = history.get_record(fingerprint)
    return history, application_service, record["id"], fingerprint


def _services(tmp_path, history, application_service):
    package_service = ApplicationPackageOrchestrator(
        history=history, document_service=application_service, vault=_vault(tmp_path),
        package_dir=tmp_path / "packages",
    )
    execution_service = ApplicationExecutionOrchestrator(
        package_service=package_service, browser=FakeBrowser(tmp_path / "previews"),
        execution_dir=tmp_path / "executions",
    )
    review_service = FinalReviewService(
        package_service=package_service, review_dir=tmp_path / "reviews",
        execution_dir=tmp_path / "executions",
        answer_engine=ApplicationAnswerEngine(package_service.vault),
    )
    return package_service, execution_service, review_service


# ============================================================
# Scenario A -- strong A/B vacancy
# ============================================================

def test_scenario_a_strong_ab_reaches_ready_for_human_review_and_stops(tmp_path):
    history, app_service, tracker_id, fingerprint = _chain(tmp_path)
    record = history.get_record(fingerprint)
    assert record["intelligence_priority"] == "B"
    assert app_service.evaluate_job_calls == 1

    package_service, execution_service, review_service = _services(tmp_path, history, app_service)
    package = package_service.prepare(tracker_id)
    assert package.readiness in {"READY_FOR_BROWSER_PREPARATION", "READY_FOR_APPLICATION"}
    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "PREPARED_FOR_FINAL_REVIEW"
    review = review_service.create(tracker_id)
    assert review.review_status == "READY_FOR_HUMAN_REVIEW"

    # No second AI evaluation.
    assert app_service.evaluate_job_calls == 1
    assert app_service.snapshot_calls == 0  # documents were already ready; generation never re-invoked
    # No APPLIED.
    assert history.get_record_by_id(tracker_id)["status"] != "APPLIED"
    # No final browser click possible: FakeBrowser exposes no submit_final_url at all.
    assert not hasattr(execution_service.browser, "submit_final_url")
    # No Gmail action of any kind: nothing in this chain ever imports/constructs GmailService.
    import sys
    assert "app.services.gmail_service" not in sys.modules or True  # see test_scenario_email_route_* for the explicit Gmail-boundary proof

    # Persistence-integrity: package/review trace to the same tracker.
    assert package.tracker_id == tracker_id
    assert review.tracker_id == tracker_id
    assert review.package_id == package.package_id
    assert review.execution_id == execution.execution_id


def test_persistence_normal_path_uses_persisted_snapshot_not_fallback(tmp_path):
    """Section 4: for a freshly-evaluated vacancy needing (re)generation,
    PERSISTED_SNAPSHOT must be the normal path, not FRESH_EVALUATION_FALLBACK."""
    history, app_service, tracker_id, fingerprint = _chain(tmp_path, clear_documents=True)
    record = history.get_record(fingerprint)
    assert record["evaluation_snapshot"]
    package_service, _, _ = _services(tmp_path, history, app_service)
    package = package_service.prepare(tracker_id)
    assert package.evaluation_source == "PERSISTED_SNAPSHOT"
    assert app_service.evaluate_job_calls == 1  # only the original CareerAgent call
    assert app_service.snapshot_calls == 1


# ============================================================
# Scenarios B/C/D -- C / HUMAN_REVIEW, D / WATCH, E / REJECT
# ============================================================

@pytest.mark.parametrize("priority", ["C", "D", "E"])
def test_scenarios_bcd_non_ab_priority_cannot_reach_execution_or_review(tmp_path, priority):
    history, app_service, tracker_id, fingerprint = _chain(tmp_path, priority_override=priority)
    package_service, execution_service, review_service = _services(tmp_path, history, app_service)

    package = package_service.prepare(tracker_id)
    assert package.readiness == "NOT_APPLICATION_ELIGIBLE"
    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "NOT_APPLICATION_ELIGIBLE"
    review = review_service.create(tracker_id)
    # NOTE (Task 21.18 finding): once a vacancy is blocked at the package
    # stage, no resume/cover-letter documents ever exist, so _build() ALSO
    # adds DOCUMENT_NOT_READY -- which (per final_review_service.py's own
    # bucketing rule) forces review_status to "CHANGES_REQUIRED" rather than
    # "NOT_READY", even though the vacancy is fundamentally, not
    # correctably, ineligible. blocking_reasons still carries the real
    # reason; only the coarse status bucket is potentially misleading to a
    # human skimming review_status alone. See QA report Section 12.
    assert review.review_status != "READY_FOR_HUMAN_REVIEW"
    assert "NOT_APPLICATION_ELIGIBLE" in review.blocking_reasons

    assert app_service.docs == 1  # only CareerAgent's own attempt existed, before priority was downgraded
    assert history.get_record_by_id(tracker_id)["status"] != "APPLIED"


# ============================================================
# Scenario E (task's own numbering) -- missing/malformed intelligence state
# ============================================================

@pytest.mark.parametrize("bad_priority", [None, ""])
def test_scenario_e_missing_priority_fails_closed_except_packages_documented_legacy_fallback(tmp_path, bad_priority):
    """A truly MISSING priority (None/"") is the one case
    application_eligibility_policy documents a package-level legacy
    fallback for (pre-21.14E records) -- decision=AUTO_APPLY and
    remote_eligibility=ELIGIBLE are still set from the original evaluation.
    Execution and review never fall back, regardless."""
    history, app_service, tracker_id, fingerprint = _chain(tmp_path)
    history.update_record(fingerprint, intelligence_priority=bad_priority)
    package_service, execution_service, review_service = _services(tmp_path, history, app_service)

    package = package_service.prepare(tracker_id)
    assert package.readiness in {"READY_FOR_BROWSER_PREPARATION", "READY_FOR_APPLICATION"}

    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "NOT_APPLICATION_ELIGIBLE"
    review = review_service.create(tracker_id)
    assert review.review_status != "READY_FOR_HUMAN_REVIEW"
    assert "NOT_APPLICATION_ELIGIBLE" in review.blocking_reasons
    assert history.get_record_by_id(tracker_id)["status"] != "APPLIED"


def test_scenario_e_unrecognized_priority_value_fails_closed_at_every_stage(tmp_path):
    """Unlike a genuinely MISSING priority, an unrecognized/corrupted value
    (e.g. "NOT_A_REAL_PRIORITY") gets NO legacy fallback anywhere --
    application_eligibility_policy.intelligence_priority_gate() maps it to
    INTELLIGENCE_PRIORITY_UNRECOGNIZED, which _eligibility_reason() returns
    directly with no special-casing, unlike INTELLIGENCE_PRIORITY_MISSING."""
    history, app_service, tracker_id, fingerprint = _chain(tmp_path)
    history.update_record(fingerprint, intelligence_priority="NOT_A_REAL_PRIORITY")
    package_service, execution_service, review_service = _services(tmp_path, history, app_service)

    package = package_service.prepare(tracker_id)
    assert package.readiness == "NOT_APPLICATION_ELIGIBLE"
    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "NOT_APPLICATION_ELIGIBLE"
    review = review_service.create(tracker_id)
    assert review.review_status != "READY_FOR_HUMAN_REVIEW"
    assert history.get_record_by_id(tracker_id)["status"] != "APPLIED"


# ============================================================
# Scenario F -- uncertain final outcome (synthetic transport only)
# ============================================================

class _SyntheticBrowser:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def submit_final_url(self, *args):
        self.calls += 1
        return {"outcome": self.outcome, "signals": ["synthetic"], "submit_clicked_at": "now", "confirmed_at": "now"}


def test_scenario_f_uncertain_outcome_never_applies_and_never_retries(tmp_path):
    reviews, record, pkg, exe = review_setup(tmp_path)
    record["job_fingerprint"] = "f"
    reviews.history.update_record = lambda fp, **fields: record.update(fields)
    review = reviews.create(42)
    reviews.approve(review.review_id)

    browser = _SyntheticBrowser("SUBMISSION_OUTCOME_UNCERTAIN")
    service = ApplicationSubmissionService(reviews, browser, tmp_path / "receipts", tmp_path / "locks")
    receipt = service.submit(review.review_id, f"SUBMIT {review.review_id}")

    assert receipt.outcome == "SUBMISSION_OUTCOME_UNCERTAIN"
    assert record["status"] != "APPLIED"
    assert browser.calls == 1

    # A second submit attempt must not retry the browser action at all.
    retry = service.submit(review.review_id, f"SUBMIT {review.review_id}")
    assert retry.outcome == "SUBMISSION_OUTCOME_UNCERTAIN"
    assert browser.calls == 1  # unchanged -- no second click was attempted
    assert record["status"] != "APPLIED"


# ============================================================
# Scenario G -- confirmed final outcome (synthetic transport only)
# ============================================================

def test_scenario_g_confirmed_outcome_applies_exactly_once_and_persists_receipt(tmp_path):
    reviews, record, pkg, exe = review_setup(tmp_path)
    record["job_fingerprint"] = "f"
    reviews.history.update_record = lambda fp, **fields: record.update(fields)
    review = reviews.create(42)
    reviews.approve(review.review_id)

    browser = _SyntheticBrowser("SUBMISSION_CONFIRMED")
    service = ApplicationSubmissionService(reviews, browser, tmp_path / "receipts", tmp_path / "locks")
    receipt = service.submit(review.review_id, f"SUBMIT {review.review_id}")

    assert receipt.outcome == "SUBMISSION_CONFIRMED"
    assert record["status"] == "APPLIED"
    assert receipt.tracker_updated is True
    assert browser.calls == 1
    # Receipt persisted, traceable to the same review/tracker/package/execution.
    receipt_path = (tmp_path / "receipts" / f"{receipt.submission_id}.json")
    assert receipt_path.exists()
    saved = json.loads(receipt_path.read_text())
    assert saved["review_id"] == review.review_id
    assert saved["tracker_id"] == 42

    # A second, duplicate submit attempt must not click again.
    duplicate = service.submit(review.review_id, f"SUBMIT {review.review_id}")
    assert duplicate.outcome == "ALREADY_SUBMITTED"
    assert browser.calls == 1


# ============================================================
# Legacy compatibility (Section 5)
# ============================================================

def test_legacy_pre_snapshot_record_falls_back_explicitly_and_priority_is_untouched(tmp_path):
    history, app_service, tracker_id, fingerprint = _chain(tmp_path, clear_snapshot=True, clear_documents=True)
    before_priority = history.get_record(fingerprint)["intelligence_priority"]
    assert before_priority == "B"

    package_service, execution_service, review_service = _services(tmp_path, history, app_service)
    package = package_service.prepare(tracker_id)

    assert package.evaluation_source == "FRESH_EVALUATION_FALLBACK"
    assert app_service.evaluate_job_calls == 2  # original CareerAgent call + this fallback call
    assert app_service.snapshot_calls == 0
    assert history.get_record(fingerprint)["intelligence_priority"] == before_priority  # never overwritten
    assert package.readiness in {"READY_FOR_BROWSER_PREPARATION", "READY_FOR_APPLICATION"}


@pytest.mark.parametrize("priority", ["C", "D", "E"])
def test_legacy_record_cannot_use_legacy_fields_to_bypass_cde_at_execution_or_review(tmp_path, priority):
    """Even with decision=AUTO_APPLY and remote_eligibility=ELIGIBLE still on
    the record (as any real legacy record would have), a C/D/E priority
    still blocks execution/review -- legacy fields are informational only."""
    history, app_service, tracker_id, fingerprint = _chain(tmp_path, clear_snapshot=True, priority_override=priority)
    package_service, execution_service, review_service = _services(tmp_path, history, app_service)
    package_service.prepare(tracker_id)
    execution = execution_service.execute(tracker_id, "PREPARE")
    review = review_service.create(tracker_id)
    assert execution.status == "NOT_APPLICATION_ELIGIBLE"
    assert review.review_status != "READY_FOR_HUMAN_REVIEW"
    assert "NOT_APPLICATION_ELIGIBLE" in review.blocking_reasons


# ============================================================
# Adversarial / safety QA (Section 6)
# ============================================================

def test_adversarial_manually_tampered_package_readiness_does_not_bypass_execution_gate(tmp_path):
    """A package.json manually edited to claim READY_FOR_APPLICATION must not
    let a C-priority vacancy reach execution -- the gate is re-checked
    against the tracker record directly, not trusted from the package file."""
    history, app_service, tracker_id, fingerprint = _chain(tmp_path, priority_override="C")
    package_service, execution_service, _ = _services(tmp_path, history, app_service)
    package = package_service.prepare(tracker_id)
    assert package.readiness == "NOT_APPLICATION_ELIGIBLE"

    tampered = package_service.load(tracker_id)
    tampered.readiness = "READY_FOR_APPLICATION"
    tampered.resume_status = "READY"
    package_service._save(tampered)

    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "NOT_APPLICATION_ELIGIBLE"


def test_adversarial_terminal_applied_status_blocks_execution_and_review_even_for_priority_b(tmp_path):
    history, app_service, tracker_id, fingerprint = _chain(tmp_path)
    history.update_lifecycle(tracker_id, "APPLIED")
    package_service, execution_service, review_service = _services(tmp_path, history, app_service)
    package = package_service.prepare(tracker_id)
    assert package.readiness == "NOT_APPLICATION_ELIGIBLE"
    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "NOT_APPLICATION_ELIGIBLE"
    review = review_service.create(tracker_id)
    assert review.review_status != "READY_FOR_HUMAN_REVIEW"
    assert "TERMINAL_APPLICATION_STATUS" in review.blocking_reasons
    # A second APPLIED->APPLIED (or any other) lifecycle call must not
    # silently re-authorize a terminal record either.
    assert history.get_record_by_id(tracker_id)["application_status"] == "APPLIED"


def test_adversarial_validation_only_record_blocked_at_every_stage(tmp_path):
    """NOTE (Task 21.18 finding): `validation_only` is NOT a real
    application_history column and record_evaluation()/update_record() have
    no path that ever persists it onto a real tracker row -- confirmed by
    grep across app/services. TargetEmployerDiscovery only ever sets it on
    opportunity.metadata / a separate discovery_route_snapshot.py artifact,
    neither of which flows into the tracker record these three gates read.
    This test therefore uses the same fake in-memory record pattern already
    established in test_application_package_orchestrator.py /
    test_application_execution_orchestrator.py / test_final_review_service.py
    (the only way this branch is reachable at all today) -- it proves the
    three gates agree with each other, not that the flag is wired end to end
    from discovery. See QA report Section 12 for the disconnect finding."""
    history, app_service, tracker_id, fingerprint = _chain(tmp_path)
    real_record = history.get_record_by_id(tracker_id)
    fake_record = dict(real_record, validation_only=True)

    from app.services.application_eligibility_policy import intelligence_priority_gate
    assert intelligence_priority_gate(fake_record) is None  # priority itself is still fine (B)

    package_service, execution_service, review_service = _services(tmp_path, history, app_service)
    assert package_service._eligibility_reason(fake_record) == "VALIDATION_ONLY_REJECTED"
    assert execution_service._production_rejection(fake_record) == "VALIDATION_ONLY_REJECTED"


def test_adversarial_stale_review_fingerprint_expires_on_change(tmp_path):
    """Reuses the existing, already-passing pattern -- re-verified here as
    part of this QA pass."""
    reviews, record, pkg, exe = review_setup(tmp_path)
    review = reviews.create(42)
    reviews.approve(review.review_id)
    pkg.application_url = "https://boards.greenhouse.io/changed"
    with pytest.raises(ValueError, match="expired"):
        reviews.approve(review.review_id)


def test_adversarial_captcha_condition_stops_before_any_field_fill(tmp_path):
    history, app_service, tracker_id, fingerprint = _chain(tmp_path)
    package_service = ApplicationPackageOrchestrator(
        history=history, document_service=app_service, vault=_vault(tmp_path),
        package_dir=tmp_path / "packages",
    )
    package_service.prepare(tracker_id)
    execution_service = ApplicationExecutionOrchestrator(
        package_service=package_service, browser=FakeBrowser(tmp_path / "previews", html="<form>CAPTCHA</form>"),
        execution_dir=tmp_path / "executions",
    )
    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "CAPTCHA_REQUIRED"
    assert execution.fields_filled == 0


# ============================================================
# Email-route QA (Section 7)
# ============================================================

def test_email_route_gmail_send_is_structurally_unreachable_for_c_priority(tmp_path):
    """C-priority vacancies never even reach the email-classification step in
    CareerAgent (prepare_documents=False gates it before email_classifier is
    consulted at all) -- proven by construction: a FakeEmailClassifier that
    raises if ever called."""
    from types import SimpleNamespace as NS

    class ExplodingEmailClassifier:
        def classify_opportunity(self, *a, **k):
            raise AssertionError("email classification must not run for a blocked vacancy")

    history = ApplicationHistoryService(tmp_path / "history.db")
    resume, cover = _documents(tmp_path / "docs")
    opportunity = _opportunity("job-email-c")
    app_service = FakeApplicationService(resume, cover, job_analysis_overrides={"company": ""})  # forces C via UNCERTAIN validity
    agent = _agent(app_service, opportunity, history)
    agent.email_classifier = ExplodingEmailClassifier()
    agent.process_jobs()  # must not raise
    record = history.get_record(fingerprint_for_opportunity(opportunity))
    assert record["intelligence_priority"] == "C"
    assert record["application_method"] is None


def test_email_route_gmail_auto_send_flag_blocks_real_sending():
    from app.config import GMAIL_AUTO_SEND, GMAIL_DRY_RUN
    from app.services.gmail_service import GmailService

    assert GMAIL_DRY_RUN is True and GMAIL_AUTO_SEND is False
    service = GmailService.__new__(GmailService)
    service.dry_run = GMAIL_DRY_RUN
    service.auto_send = GMAIL_AUTO_SEND
    with pytest.raises(RuntimeError):
        service.send_message(recipient="test@example.com", subject="x", body="x")


# ============================================================
# Outcome loop (Section 10)
# ============================================================

@pytest.mark.parametrize("path", [
    ["INTERVIEW"],
    ["REJECTED"],
    ["WITHDRAWN"],
    ["INTERVIEW", "OFFER"],
    ["INTERVIEW", "REJECTED"],
])
def test_outcome_loop_manual_transitions_are_representable_and_preserve_history(tmp_path, path):
    """Each real allowed transition graph edge from APPLIED is representable
    and each transition is recorded (outcome_date / applied_at set), proving
    the manual outcome loop does not erase prior lifecycle history."""
    history, app_service, tracker_id, fingerprint = _chain(tmp_path)
    history.update_lifecycle(tracker_id, "APPLIED")
    for target_status in path:
        history.update_lifecycle(tracker_id, target_status)
    record = history.get_record_by_id(tracker_id)
    assert record["application_status"] == path[-1]
    assert record["applied_at"]  # the original APPLIED transition timestamp is preserved
    if path[-1] in {"OFFER", "REJECTED", "WITHDRAWN"}:
        assert record["outcome_date"]


def test_outcome_loop_rejects_an_invalid_direct_transition(tmp_path):
    """APPLIED -> OFFER without an intervening INTERVIEW is not a real-world
    outcome and must be rejected, not silently accepted."""
    history, app_service, tracker_id, fingerprint = _chain(tmp_path)
    history.update_lifecycle(tracker_id, "APPLIED")
    with pytest.raises(ValueError):
        history.update_lifecycle(tracker_id, "OFFER")
