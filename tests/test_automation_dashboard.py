from fastapi.testclient import TestClient

import app.api.dashboard as dashboard
from app.services.automation_control_service import AutomationControlService


class FakeSummary:
    def to_dict(self):
        return {"packages_prepared": 1, "confirmed_applications_submitted": None, "errors": []}


class FakeRunner:
    def run(self):
        return FakeSummary()

    def gmail_only(self):
        return {"messages_checked": 0, "matched": 0, "human_review": 0}


def test_automation_page_and_controls_use_temp_store(tmp_path, monkeypatch):
    control = AutomationControlService(
        tmp_path / "history.db", tmp_path / "automation.lock",
        runner_factory=lambda: FakeRunner(), gmail_runner_factory=lambda: FakeRunner(),
    )
    monkeypatch.setattr(dashboard, "_automation_control", control)

    client = TestClient(dashboard.app)
    page = client.get("/automation")
    assert page.status_code == 200
    assert "Run and monitor Career Intelligence safely." in page.text
    assert "Run Career Intelligence" in page.text
    assert "Check Employer Inbox" in page.text
    assert "Gmail Monitoring" in page.text
    assert "read-only" in page.text

    response = client.post("/automation/run", follow_redirects=False)
    assert response.status_code == 303
    run = control.latest()
    assert run["mode"] == "FULL"


def test_automation_stop_route_is_idempotent(tmp_path, monkeypatch):
    control = AutomationControlService(tmp_path / "history.db", tmp_path / "automation.lock")
    run = control.acquire("FULL", "TEST")
    control.update(run["run_id"], status="RUNNING")
    monkeypatch.setattr(dashboard, "_automation_control", control)

    client = TestClient(dashboard.app)
    response = client.post("/automation/stop", follow_redirects=False)
    assert response.status_code == 303
    assert control.get(run["run_id"])["stop_requested"] is True
