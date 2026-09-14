from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
from app.services.execution_readiness_service import (
    ExecutionReadinessService, READY_FOR_HUMAN_REVIEW, WAITING_FOR_ANSWER,
    WAITING_FOR_BROWSER, WAITING_FOR_ELIGIBILITY,
)
from app.services.opportunity_crm_service import OpportunityCRMService


class PackageView:
    def __init__(self, readiness="READY_FOR_APPLICATION"):
        self.readiness = readiness
        self.blocking_reasons = []

    def load(self, tracker_id):
        return self


def make_service(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    return history, OpportunityCRMService(history)


def add(crm, key, priority="A", **fields):
    row = crm.create_opportunity(job_fingerprint(source="Test", external_job_id=key), **fields)
    crm.update_opportunity(row["id"], intelligence_priority=priority)
    return row["id"]


def test_known_answers_use_the_existing_authoritative_resolver(tmp_path):
    vault = ApplicationAnswerVault(tmp_path / "vault.json")
    engine = ApplicationAnswerEngine(vault)
    decision = engine.resolve("Are you ACA or ACCA specifically?")
    assert decision.answer == "NO"
    assert decision.manual_review is False
    assert engine.resolve("Are you a qualified accountant?").answer == "YES"


def test_unknown_remote_eligibility_stays_human_review(tmp_path):
    history, crm = make_service(tmp_path)
    tracker = add(crm, "eligibility", priority="B", remote_eligibility="MANUAL_REVIEW", crm_stage="ELIGIBILITY_REVIEW")
    readiness = ExecutionReadinessService(crm, package_service=PackageView()).for_tracker(tracker)
    assert readiness.state == WAITING_FOR_ELIGIBILITY
    assert readiness.blocker_category == "ELIGIBILITY"
    crm.close()


def test_blocker_taxonomy_keeps_browser_security_human_only(tmp_path):
    history, crm = make_service(tmp_path)
    tracker = add(crm, "captcha")
    crm.record_human_blocker(tracker, "HUMAN_CAPTCHA_REQUIRED", "CAPTCHA is present")
    readiness = ExecutionReadinessService(crm, package_service=PackageView()).for_tracker(tracker)
    assert readiness.state == WAITING_FOR_BROWSER
    assert readiness.user_action_required is True
    assert readiness.blocker_category == "CAPTCHA"
    crm.close()


def test_answer_blockers_are_waiting_for_answer_and_can_group_only_when_identical(tmp_path):
    history, crm = make_service(tmp_path)
    first = add(crm, "one")
    second = add(crm, "two")
    crm.record_human_blocker(first, "HUMAN_ANSWER_APPROVAL_REQUIRED", "Confirm notice period")
    crm.record_human_blocker(second, "HUMAN_ANSWER_APPROVAL_REQUIRED", "Confirm notice period")
    service = ExecutionReadinessService(crm, package_service=PackageView())
    assert service.for_tracker(first).state == WAITING_FOR_ANSWER
    groups = service.group_blockers(crm.list_open_blockers())
    assert len(groups) == 1 and groups[0]["groupable"] is True
    crm.close()


def test_application_specific_final_approval_is_not_grouped(tmp_path):
    history, crm = make_service(tmp_path)
    first = add(crm, "one")
    second = add(crm, "two")
    crm.record_human_blocker(first, "READY_FOR_HUMAN_SUBMIT", "Review one")
    crm.record_human_blocker(second, "READY_FOR_HUMAN_SUBMIT", "Review one")
    groups = ExecutionReadinessService(crm, package_service=PackageView()).group_blockers(crm.list_open_blockers())
    assert len(groups) == 2
    assert all(group["groupable"] is False for group in groups)
    crm.close()


def test_resolving_existing_blocker_recomputes_without_changing_priority(tmp_path):
    history, crm = make_service(tmp_path)
    tracker = add(crm, "resume", priority="B")
    blocker = crm.record_human_blocker(tracker, "HUMAN_ANSWER_APPROVAL_REQUIRED", "Unknown answer")
    service = ExecutionReadinessService(crm, package_service=PackageView())
    assert service.for_tracker(tracker).state == WAITING_FOR_ANSWER
    crm.resolve_human_blocker(blocker["id"], "Approved application-specific answer", "USER")
    assert service.for_tracker(tracker).state == "READY_FOR_AUTOMATION"
    assert crm.get_opportunity(tracker)["intelligence_priority"] == "B"
    crm.close()
