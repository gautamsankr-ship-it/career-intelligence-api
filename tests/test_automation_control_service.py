import time

import pytest

import app.services.automation_control_service as control_module
from app.services.automation_control_service import (
    AutomationControlService,
    AutomationRunAlreadyActive,
)


class FakeSummary:
    def to_dict(self):
        return {"packages_prepared": 2, "confirmed_applications_submitted": None, "errors": []}


class FakeRunner:
    def run(self):
        return FakeSummary()

    def gmail_only(self):
        return {"messages_checked": 3, "matched": 1, "human_review": 0}


def service(tmp_path, runner_factory=None, gmail_factory=None):
    return AutomationControlService(
        tmp_path / "history.db", tmp_path / "automation.lock",
        runner_factory=runner_factory, gmail_runner_factory=gmail_factory,
    )


def test_run_history_single_flight_and_release(tmp_path):
    control = service(tmp_path, runner_factory=lambda: FakeRunner())
    queued = control.acquire("FULL", "TEST")
    assert queued["lifecycle_status"] == "QUEUED"
    with pytest.raises(AutomationRunAlreadyActive):
        control.acquire("FULL", "DOUBLE_CLICK")

    control._run(queued["run_id"], "FULL")
    record = control.get(queued["run_id"])
    assert record["lifecycle_status"] == "COMPLETED"
    assert record["summary_counts"]["packages_prepared"] == 2
    assert record["summary_counts"]["confirmed_applications_submitted"] is None
    assert control.current() is None
    next_run = control.acquire("GMAIL", "TEST")
    assert next_run["mode"] == "GMAIL"
    control._run(next_run["run_id"], "GMAIL")


def test_stop_request_is_durable(tmp_path):
    control = service(tmp_path, runner_factory=lambda: FakeRunner())
    run = control.acquire("FULL", "TEST")
    requested = control.request_stop(run["run_id"])
    assert requested["stop_requested"] is True
    assert requested["lifecycle_status"] == "STOP_REQUESTED"
    control._run(run["run_id"], "FULL")
    assert control.get(run["run_id"])["lifecycle_status"] == "STOP_REQUESTED"


def test_restart_reconciles_running_conservatively_and_does_not_resume(tmp_path):
    first = service(tmp_path)
    run = first.acquire("FULL", "TEST")
    first.update(run["run_id"], status="RUNNING", stage="BROWSER_PREPARATION")

    control_module._PROCESS_ACTIVE_RUN_IDS.clear()  # simulate a new process
    try:
        restarted = service(tmp_path)
        record = restarted.get(run["run_id"])
        assert record["lifecycle_status"] == "INTERRUPTED"
        assert "automatic resume" in record["human_pause_reason"]
        assert restarted.current() is None
        assert not (tmp_path / "automation.lock").exists()
    finally:
        control_module._PROCESS_ACTIVE_RUN_IDS.clear()


def test_gmail_summary_does_not_expose_message_content(tmp_path):
    control = service(tmp_path, gmail_factory=lambda: FakeRunner())
    run = control.acquire("GMAIL", "TEST")
    control._run(run["run_id"], "GMAIL")
    record = control.get(run["run_id"])
    assert record["summary_counts"] == {"messages_checked": 3, "matched": 1, "human_review": 0}
    assert "details" not in record["summary_counts"]


def test_preexisting_crm_action_items_do_not_change_ready_global_state(tmp_path):
    control = service(tmp_path)

    class ExistingActions:
        def action_required_items(self):
            return [{"tracker_id": 1, "reason": "Existing application action"}] * 114

    assert control.global_state(ExistingActions()) == "READY"


def test_waiting_for_human_run_is_distinct_from_existing_crm_actions(tmp_path):
    control = service(tmp_path)
    run = control.acquire("FULL", "TEST")
    control.update(run["run_id"], status="WAITING_FOR_HUMAN", stage="BROWSER_PREPARATION",
                   human_pause_reason="CAPTCHA requires human action")
    control._release_lock(run["run_id"])
    assert control.global_state() == "WAITING FOR YOU"


def test_historical_warning_does_not_override_healthy_current_services(tmp_path):
    control = service(tmp_path)
    run = control.acquire("FULL", "TEST")
    control.update(run["run_id"], status="COMPLETED_WITH_WARNINGS", stage="COMPLETE",
                   warnings=["historical Gmail invalid_grant"])
    control._release_lock(run["run_id"])
    health = {name: "Available" for name in {"Worker", "Browser", "Gmail Monitor", "CRM Database", "Discovery"}}
    health["Gmail Monitor"] = "Connected Read-Only"
    assert control.global_state(current_health=health) == "READY"
    record = control.get(run["run_id"])
    assert record["lifecycle_status"] == "COMPLETED_WITH_WARNINGS"
    assert record["warnings"] == ["historical Gmail invalid_grant"]


def test_current_gmail_unavailable_requires_attention(tmp_path):
    control = service(tmp_path)
    run = control.acquire("FULL", "TEST")
    control.update(run["run_id"], status="COMPLETED_WITH_WARNINGS", stage="COMPLETE",
                   warnings=["historical Gmail invalid_grant"])
    control._release_lock(run["run_id"])
    assert control.global_state(current_health={"Gmail Monitor": "Unavailable"}) == "ATTENTION REQUIRED"
