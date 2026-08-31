"""Task 21.28: opt-in persistent authenticated browser session foundation.

Fully hermetic and local: every test uses a tmp_path-scoped Chromium profile
directory and synthetic in-page HTML (via page.set_content -- no real
website, no LocalATS network fixture needed since challenge classification
only depends on visible page text, not a specific host). No credential
login is ever attempted; login/MFA/CAPTCHA pages here are synthetic markup
only, proving detection and pause behavior, never solving/bypassing anything.

Isolated-mode behavior (existing Greenhouse/Lever preview/fill/submit code
paths) is entirely untouched by this file -- proven by the rest of the
existing suite continuing to pass unchanged.
"""
from __future__ import annotations

import asyncio

import pytest

import app.config as config
from app.services.application_browser_service import (
    ApplicationBrowserService,
    PersistentProfileInsideRepositoryError,
    PersistentProfileLockedError,
    PersistentProfileNotConfiguredError,
    PersistentSession,
    resolve_persistent_profile_dir,
)


# --- (1) isolated mode remains default --------------------------------------

def test_isolated_mode_is_the_default():
    assert config.APPLICATION_BROWSER_SESSION_MODE == config.APPLICATION_BROWSER_SESSION_MODE_ISOLATED
    assert config.APPLICATION_BROWSER_SESSION_MODE_ISOLATED == "ISOLATED"
    assert config.APPLICATION_BROWSER_SESSION_MODE_PERSISTENT_AUTHENTICATED == "PERSISTENT_AUTHENTICATED"


# --- (2) isolated mode behavior remains unchanged ----------------------------

def test_isolated_context_creation_comment_and_behavior_are_untouched():
    """The existing isolated-mode navigation methods still create a fresh,
    cookie-less context per call -- proven by source inspection of the
    unmodified comment marking that design, plus the full existing browser
    test suite continuing to pass unchanged alongside this file."""
    import inspect

    import app.services.application_browser_service as module
    source = inspect.getsource(module.ApplicationBrowserService._preview_url)
    assert "isolated: no imported browser profile/cookies" in source


# --- (3)/(4) fail-closed profile resolution ----------------------------------

def test_persistent_mode_requires_configured_external_profile_path(monkeypatch):
    import app.services.application_browser_service as module
    monkeypatch.setattr(module, "APPLICATION_PERSISTENT_BROWSER_PROFILE_DIR", "")
    with pytest.raises(PersistentProfileNotConfiguredError):
        resolve_persistent_profile_dir(None)


def test_repository_contained_profile_path_is_rejected(tmp_path):
    with pytest.raises(PersistentProfileInsideRepositoryError):
        resolve_persistent_profile_dir("app/data")
    with pytest.raises(PersistentProfileInsideRepositoryError):
        resolve_persistent_profile_dir(".")


def test_external_profile_path_is_accepted(tmp_path):
    resolved = resolve_persistent_profile_dir(tmp_path / "profile")
    assert resolved == (tmp_path / "profile").resolve()


# --- (6) no storage-state/cookie export ever occurs --------------------------

def test_no_storage_state_or_cookie_export_in_source():
    """Structural safeguard, not just documentation: the persistent-session
    code path must never call context.storage_state()/cookies() anywhere."""
    import inspect

    import app.services.application_browser_service as module
    source = inspect.getsource(module)
    assert "storage_state" not in source
    assert ".cookies(" not in source


def test_persistent_session_exposes_no_secret_export_api():
    public_callables = {
        name for name in dir(PersistentSession)
        if not name.startswith("_") and callable(getattr(PersistentSession, name, None))
    }
    assert public_callables == {"refresh_state", "close"}
    for forbidden in ("cookie", "storage", "token", "credential", "password", "export"):
        assert not any(forbidden in name.lower() for name in public_callables)


# --- helpers for real (headless, local-only) persistent-context tests -------

def _run(coro):
    return asyncio.run(coro)


def _profile(tmp_path, name="profile"):
    return tmp_path / name


# --- (5) persistent mode launches headed (default) --------------------------

def test_persistent_mode_defaults_to_headed():
    import inspect

    signature = inspect.signature(ApplicationBrowserService.open_persistent_session)
    assert signature.parameters["headed"].default is True


def test_persistent_session_can_launch_headed_and_headless(tmp_path):
    """Proves both the default (headed=True -> headless=False) and the
    test-safe override (headed=False) actually launch a real Chromium
    persistent context -- no synthetic/mocked browser object involved."""
    service = ApplicationBrowserService()

    async def run():
        headed_session = await service.open_persistent_session(headed=True, profile_dir=_profile(tmp_path, "headed"))
        try:
            assert headed_session.state == "READY"
        finally:
            await headed_session.close()
        headless_session = await service.open_persistent_session(headed=False, profile_dir=_profile(tmp_path, "headless"))
        try:
            assert headless_session.state == "READY"
        finally:
            await headless_session.close()

    _run(run())


# --- (7)/(8)/(9) challenge detection -----------------------------------------

@pytest.mark.parametrize("html,expected_state", [
    ("<h1>Sign in to continue</h1>", "HUMAN_LOGIN_REQUIRED"),
    ("<h1>CAPTCHA required</h1><div class=\"g-recaptcha\"></div>", "HUMAN_CAPTCHA_REQUIRED"),
    ("<h1>Enter your one-time verification code</h1>", "HUMAN_MFA_REQUIRED"),
])
def test_challenge_pages_produce_correct_human_pause_state(tmp_path, html, expected_state):
    service = ApplicationBrowserService()

    async def run():
        session = await service.open_persistent_session(profile_dir=_profile(tmp_path), headed=False)
        try:
            await session.page.set_content(html)
            state = await session.refresh_state()
            assert state == expected_state
            assert session.state == expected_state
        finally:
            await session.close()

    _run(run())


# --- (10) challenge never triggers automated credential filling -------------

def test_login_challenge_never_fills_credential_fields(tmp_path):
    service = ApplicationBrowserService()
    login_html = (
        "<h1>Sign in to continue</h1>"
        "<form><label for='email'>Email</label><input id='email' type='email'>"
        "<label for='password'>Password</label><input id='password' type='password'></form>"
    )

    async def run():
        session = await service.open_persistent_session(profile_dir=_profile(tmp_path), headed=False)
        try:
            await session.page.set_content(login_html)
            state = await session.refresh_state()
            assert state == "HUMAN_LOGIN_REQUIRED"
            assert await session.page.locator("#email").input_value() == ""
            assert await session.page.locator("#password").input_value() == ""
        finally:
            await session.close()

    _run(run())
    import inspect

    import app.services.application_browser_service as module
    source = inspect.getsource(module.PersistentSession) + inspect.getsource(module.ApplicationBrowserService.open_persistent_session)
    for forbidden in (".fill(", ".type(", ".press("):
        assert forbidden not in source


# --- (11) persistent browser remains usable after synthetic intervention ----

def test_persistent_session_remains_usable_after_synthetic_human_intervention(tmp_path):
    """Same still-open context/page: hits a challenge, a "human" resolves it
    (simulated locally by navigating to ordinary content), and the SAME
    session/page continues to work -- proving the context is not torn down
    merely because a pause state was detected."""
    service = ApplicationBrowserService()

    async def run():
        session = await service.open_persistent_session(profile_dir=_profile(tmp_path), headed=False)
        try:
            await session.page.set_content("<h1>CAPTCHA required</h1>")
            assert await session.refresh_state() == "HUMAN_CAPTCHA_REQUIRED"
            # Human resolves the challenge in the same visible window --
            # simulated locally by moving the same page to ordinary content.
            await session.page.set_content("<main><p>Application form</p><input id='resumed'></main>")
            state = await session.refresh_state()
            assert state == "READY"
            assert session.page.url  # same live page object, still responsive
            await session.page.locator("#resumed").focus()
        finally:
            await session.close()

    _run(run())


# --- (12) concurrency protection ---------------------------------------------

def test_concurrent_use_of_same_profile_is_blocked(tmp_path):
    service = ApplicationBrowserService()
    profile_dir = _profile(tmp_path)

    async def run():
        first = await service.open_persistent_session(profile_dir=profile_dir, headed=False)
        try:
            with pytest.raises(PersistentProfileLockedError):
                await service.open_persistent_session(profile_dir=profile_dir, headed=False)
        finally:
            await first.close()
        # Lock released after close(); the profile can be reused afterward.
        second = await service.open_persistent_session(profile_dir=profile_dir, headed=False)
        await second.close()

    _run(run())


def test_failed_open_releases_the_lock(tmp_path):
    """A launch failure must not leave a stale lock behind."""
    profile_dir = _profile(tmp_path)
    service = ApplicationBrowserService()

    async def run():
        with pytest.raises(Exception):
            await service.open_persistent_session(url="not-a-valid-url", profile_dir=profile_dir, headed=False)
        assert not (profile_dir / ".career_intelligence_browser.lock").exists()
        session = await service.open_persistent_session(profile_dir=profile_dir, headed=False)
        await session.close()

    _run(run())
