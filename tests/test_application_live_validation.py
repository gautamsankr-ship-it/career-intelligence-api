import asyncio
from pathlib import Path

import pytest

from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_live_validation import LiveValidationService


@pytest.fixture(autouse=True)
def _isolated_production_defaults(tmp_path, monkeypatch):
    """LiveValidationService(session_dir=tmp_path) deliberately leaves `browser`
    at its true default (test_real_profile_is_explicit_opt_in below depends on
    the real, non-synthetic answer engine that default wires up). Rather than
    letting that default chain reach the real production vault/preview paths,
    redirect the bound __init__ defaults themselves -- for this test module
    only -- to isolated tmp_path locations. Behavior is identical; nothing
    production is ever read or written.
    """
    monkeypatch.setattr(ApplicationAnswerVault.__init__, "__defaults__", (tmp_path / "vault.json",))
    monkeypatch.setattr(ApplicationBrowserService.__init__, "__defaults__", (None, tmp_path / "previews", None, None, None, None, None))


GREENHOUSE = '''<html><body><form id="application_form"><label for="first">First name</label><input id="first" required><label for="email">Email address</label><input id="email" type="email" required><label for="phone">Phone number</label><input id="phone" type="tel"><label for="notice">What is your notice period?</label><input id="notice"><label for="legal">I certify that this is accurate</label><input id="legal" type="checkbox" required><label for="cv">Resume/CV</label><input id="cv" type="file"><button type="button">Next</button><button>Submit Application</button></form></body></html>'''
LEVER = '''<html><body><form class="lever-application"><label for="country">Current country of residence</label><select id="country"><option>Nepal</option></select><label for="notes">Additional details</label><textarea id="notes"></textarea><button type="button">Continue</button><button type="submit">Apply Now</button></form></body></html>'''
WRAPPER = '''<html><body><h1>Accounting Manager</h1><iframe src="https://boards.greenhouse.io/embed/job_app?for=example"></iframe></body></html>'''
EMBEDDED = '''<form data-ats="greenhouse"><label for="email">Email address</label><input id="email" type="email"><button>Submit Application</button></form>'''
ENTRY = '''<html><body><h1>Role</h1><a href="https://boards.greenhouse.io/example/jobs/42">Apply</a></body></html>'''


def service(tmp_path): return LiveValidationService(session_dir=tmp_path)


def test_synthetic_inspection_is_tracker_isolated(tmp_path):
    session, plan = service(tmp_path).validate_html(GREENHOUSE, "https://boards.greenhouse.io/example/jobs/1")
    assert session["mode"] == "LIVE_VALIDATION" and session["tracker_id"] is None
    assert session["fields_filled"] == 0 and session["application_submitted"] is False
    assert any(x["concept"] == "LEGAL_DECLARATION" for x in session["exceptions"])
    assert plan.final_submit_detected is True


def test_synthetic_fill_uses_synthetic_contact_values_only(tmp_path):
    session, plan = service(tmp_path).validate_html(GREENHOUSE, "https://boards.greenhouse.io/example/jobs/1", fill=True)
    values={f.concept:f.answer for f in plan.fields}
    assert values["EMAIL_ADDRESS"] == "validation@example.test"
    assert values["PHONE_NUMBER"] == "+10000000000"
    assert "gautamsankr@gmail.com" not in str(values)
    assert session["application_submitted"] is False


def test_real_profile_is_explicit_opt_in(tmp_path):
    _, plan = service(tmp_path).validate_html(GREENHOUSE, "https://boards.greenhouse.io/example/jobs/1", use_real_profile=True, fill=True)
    values={f.concept:f.answer for f in plan.fields}
    assert values["EMAIL_ADDRESS"] == "gautamsankr@gmail.com"


def test_lever_and_navigation_are_inspected_but_not_clicked(tmp_path):
    session, plan = service(tmp_path).validate_html(LEVER, "https://jobs.lever.co/example/123", fill=True, allow_safe_navigation=True)
    assert plan.portal == "LEVER" and plan.safe_navigation_detected
    assert session["navigation_actions"] == []
    assert session["final_submit_detected"] is True


def test_test_document_restrictions_and_session_has_no_secrets(tmp_path):
    file=tmp_path / "resume.txt"; file.write_text("test")
    session, _ = service(tmp_path).validate_html(GREENHOUSE, "https://boards.greenhouse.io/example/jobs/1", fill=True, test_resume=file)
    assert session["documents"][0]["action"] == "READY_FOR_UPLOAD"
    assert not {"password", "cookie", "otp", "token", "csrf"}.intersection(str(session).lower().split())
    with pytest.raises(ValueError):
        asyncio.run(service(tmp_path).validate_url("https://boards.greenhouse.io/example/jobs/1", test_resume=tmp_path / "bad.exe"))


def test_job_board_url_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        service(tmp_path).validate_html(GREENHOUSE, "https://www.linkedin.com/jobs/view/1")


def test_employer_hosted_greenhouse_wrapper_and_frame_are_detected(tmp_path):
    session, plan = service(tmp_path).validate_html(WRAPPER, "https://employer.example/careers/job?gh_jid=123", frame_html=EMBEDDED, frame_url="https://boards.greenhouse.io/embed/job_app?for=example")
    assert plan.portal == "GREENHOUSE" and session["wrapper_detected"] is True
    assert session["application_surface"] == "IFRAME" and session["fields_detected"] == 1
    assert "QUERY_PARAM_GH_JID" in session["portal_evidence"]["signals"]


def test_greenhouse_query_only_has_specific_wrapper_diagnostic(tmp_path):
    session, _ = service(tmp_path).validate_html("<html><body><h1>Role</h1></body></html>", "https://employer.example/careers/job?gh_jid=123")
    assert session["portal"] == "GREENHOUSE"
    assert session["state"] == "GREENHOUSE_WRAPPER_FORM_NOT_FOUND"


def test_direct_embedded_greenhouse_form_and_entry_link_distinction(tmp_path):
    session, plan = service(tmp_path).validate_html(EMBEDDED, "https://employer.example/careers/job?gh_jid=123")
    assert session["portal"] == "GREENHOUSE" and plan.fields and plan.final_submit_detected
    assert service(tmp_path)._greenhouse_entry_url("https://employer.example/careers/job?gh_jid=123", ENTRY) == "https://boards.greenhouse.io/example/jobs/42"


def test_ordinary_greenhouse_text_is_not_a_portal_signal(tmp_path):
    session, _ = service(tmp_path).validate_html("<html><body>Our greenhouse is beautiful.</body></html>", "https://employer.example/careers/job")
    assert session["portal"] == "UNKNOWN"


@pytest.mark.parametrize(("html", "state"), [
    ("<form>CAPTCHA</form>", "CAPTCHA_REQUIRED"),
    ("<form>Sign in to continue</form>", "AUTH_REQUIRED"),
    ("<form>Verification code</form>", "MFA_REQUIRED"),
    ("<form>Create an account</form>", "ACCOUNT_CREATION_REQUIRED"),
])
def test_access_boundaries_stop_without_interaction(tmp_path, html, state):
    session, _ = service(tmp_path).validate_html(html, "https://boards.greenhouse.io/example/jobs/1")
    assert session["state"] == state and session["fields_filled"] == 0
