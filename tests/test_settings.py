from fastapi.testclient import TestClient

import app.api.dashboard as dashboard
import app.services.settings_read_model as settings_model
from app.services.application_history_service import ApplicationHistoryService
from app.services.automation_control_service import AutomationControlService
from app.services.opportunity_crm_service import OpportunityCRMService


def _crm_override(db_path):
    def override():
        service = OpportunityCRMService(ApplicationHistoryService(db_path))
        try:
            yield service
        finally:
            service.close()
    return override


def test_settings_loads_authoritative_sections_without_secret_values(tmp_path, monkeypatch):
    control = AutomationControlService(tmp_path / "automation.db", tmp_path / "automation.lock")
    monkeypatch.setattr(dashboard, "_automation_control", control)
    dashboard.app.dependency_overrides[dashboard.get_crm_service] = _crm_override(tmp_path / "crm.db")
    try:
        body = TestClient(dashboard.app).get("/settings").text
        assert "Settings &amp; User Account" in body
        assert "Kathmandu, Nepal" in body
        assert "Chartered Accountant" in body
        assert "Institute of Chartered Accountants of India" in body
        assert "Answer Vault" in body
        assert "Learning &amp; Policies" in body
        assert "Audit History" in body
        assert "refresh_token" not in body.lower()
        assert "client_secret" not in body.lower()
        assert "token.json" not in body
    finally:
        dashboard.app.dependency_overrides.clear()


def test_settings_read_model_reuses_vault_and_does_not_invent_future_effective_date():
    model = settings_model.build_settings_read_model(
        learning=[],
        integrations={"Gmail": "Connected Read-Only"},
    )
    profile = {item["label"]: item["value"] for item in model["profile"]}
    assert profile["Current location"] == "Kathmandu, Nepal"
    assert any(item["topic"] == "Accounting Qualification Aca Acca" and item["answer"] == "No" for item in model["answers"])
    assert any(item["label"] == "Planned education" and item["value"] == "Master of Financial Technology" for item in model["future_facts"])
    assert all("effective" not in item["value"].lower() for item in model["future_facts"])


def test_settings_has_no_generic_consequential_write_surface(tmp_path, monkeypatch):
    assert not any(route.path == "/settings" and "POST" in route.methods for route in dashboard.app.routes)
    control = AutomationControlService(tmp_path / "automation.db", tmp_path / "automation.lock")
    monkeypatch.setattr(dashboard, "_automation_control", control)
    dashboard.app.dependency_overrides[dashboard.get_crm_service] = _crm_override(tmp_path / "crm.db")
    try:
        body = TestClient(dashboard.app).get("/settings").text
        assert "Evidence-controlled" in body
        assert "System-controlled" in body
        assert "historical application answers remain immutable" in body
    finally:
        dashboard.app.dependency_overrides.clear()
