import app.api.dashboard as dashboard


def test_persistent_mode_with_profile_is_truthful(monkeypatch):
    monkeypatch.setattr(dashboard, "APPLICATION_BROWSER_SESSION_MODE", "PERSISTENT_AUTHENTICATED")
    monkeypatch.setattr(dashboard, "APPLICATION_PERSISTENT_BROWSER_PROFILE_DIR", "C:/dedicated-profile")
    value = dashboard._browser_session_display()
    assert value == "Persistent Authenticated Mode; Profile Configured; Session Status Unknown"


def test_persistent_mode_without_profile_is_truthful(monkeypatch):
    monkeypatch.setattr(dashboard, "APPLICATION_BROWSER_SESSION_MODE", "PERSISTENT_AUTHENTICATED")
    monkeypatch.setattr(dashboard, "APPLICATION_PERSISTENT_BROWSER_PROFILE_DIR", "")
    value = dashboard._browser_session_display()
    assert value == "Persistent Authenticated Mode; Profile Not Configured; Session Status Unknown"


def test_isolated_mode_does_not_claim_a_profile_or_session(monkeypatch):
    monkeypatch.setattr(dashboard, "APPLICATION_BROWSER_SESSION_MODE", "ISOLATED")
    monkeypatch.setattr(dashboard, "APPLICATION_PERSISTENT_BROWSER_PROFILE_DIR", "C:/dedicated-profile")
    value = dashboard._browser_session_display()
    assert value == "Isolated Browser Mode; Session Status Unknown"
