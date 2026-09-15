import pytest

from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
from app.services.execution_readiness_service import ExecutionReadinessService
from app.services.execution_resolution_service import ExecutionResolutionService
from app.services.opportunity_crm_service import OpportunityCRMService


def make_crm(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    return history, OpportunityCRMService(history)


def add(crm, external_id):
    fp = job_fingerprint(source="LinkedIn", external_job_id=external_id)
    record = crm.create_opportunity(fp, company=external_id, job_title="Accountant")
    crm.history.update_record(fp, intelligence_priority="B")
    return crm.get_opportunity(record["id"])


def test_application_specific_resolution_does_not_touch_vault_or_priority(tmp_path):
    history, crm = make_crm(tmp_path)
    record = add(crm, "one")
    blocker = crm.record_human_blocker(record["id"], "HUMAN_ANSWER_APPROVAL_REQUIRED", "Question one")
    vault = ApplicationAnswerVault(tmp_path / "vault.json")
    result = ExecutionResolutionService(crm, vault).resolve_blocker(
        blocker["id"], resolution="Answered for this application", answer="yes"
    )
    assert result.reusable_answer_saved is False
    assert vault.get_answer("QUESTION_ONE") is None
    assert crm.get_opportunity(record["id"])["intelligence_priority"] == "B"
    assert not crm.list_open_blockers(record["id"])
    crm.close()


def test_confirmed_reusable_group_updates_vault_and_each_audit_trail(tmp_path):
    history, crm = make_crm(tmp_path)
    first, second = add(crm, "one"), add(crm, "two")
    one = crm.record_human_blocker(first["id"], "HUMAN_ANSWER_APPROVAL_REQUIRED", "Notice period")
    two = crm.record_human_blocker(second["id"], "HUMAN_ANSWER_APPROVAL_REQUIRED", "Notice period")
    vault = ApplicationAnswerVault(tmp_path / "vault.json")
    service = ExecutionResolutionService(crm, vault)
    with pytest.raises(ValueError):
        service.resolve_group([one, two], resolution="7 days", concept="NOTICE_PERIOD", answer="7 days")
    result = service.resolve_group(
        [one, two], resolution="7 days", concept="CUSTOM_NOTICE_PERIOD", answer="7 days", confirm=True
    )
    assert len(result.resolved_blocker_ids) == 2
    assert vault.get_answer("CUSTOM_NOTICE_PERIOD").value == "7 days"
    assert crm.list_open_blockers() == []
    for tracker_id in (first["id"], second["id"]):
        events = crm.get_timeline(tracker_id)
        assert any(event["detail"].get("event_type") == "REUSABLE_ANSWER_APPLIED" for event in events if event["kind"] == "EVENT")
        assert crm.get_opportunity(tracker_id)["intelligence_priority"] == "B"
    crm.close()


def test_grouping_rejects_security_and_final_submit_blockers(tmp_path):
    history, crm = make_crm(tmp_path)
    first, second = add(crm, "one"), add(crm, "two")
    one = crm.record_human_blocker(first["id"], "HUMAN_CAPTCHA_REQUIRED", "same")
    two = crm.record_human_blocker(second["id"], "HUMAN_CAPTCHA_REQUIRED", "same")
    with pytest.raises(ValueError):
        ExecutionResolutionService(crm, ApplicationAnswerVault(tmp_path / "vault.json")).resolve_group(
            [one, two], resolution="Completed", concept="CAPTCHA", answer="done", confirm=True
        )
    crm.close()


def test_safe_answer_reuses_existing_authoritative_resolver(tmp_path):
    history, crm = make_crm(tmp_path)
    service = ExecutionResolutionService(crm, ApplicationAnswerVault(tmp_path / "vault.json"))
    decision = service.safe_answer("Are you ACA or ACCA?")
    assert decision.answer == "NO"
    assert decision.manual_review is False
    crm.close()
