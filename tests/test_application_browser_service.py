from __future__ import annotations

from app.config import APPLICATION_AUTO_SUBMIT, APPLICATION_DRY_RUN
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_route_resolver import ApplicationRouteResolver


def _isolated_service(tmp_path, **kwargs):
    # Real (non-synthetic) answer engine backed by a fresh, isolated vault --
    # these tests assert on real seeded answer content (e.g. candidate email),
    # so a brand-new tmp_path vault (which seeds identically to production's
    # original seed data) preserves behavior without ever touching
    # app/data/application_answer_vault.json.
    vault = ApplicationAnswerVault(tmp_path / "vault.json")
    return ApplicationBrowserService(preview_folder=tmp_path, answer_engine=ApplicationAnswerEngine(vault), **kwargs)


GREENHOUSE = '''<html><body class="greenhouse"><form id="application_form">
<label for="email">Email</label><input id="email" type="email" required>
<label for="phone">Mobile number</label><input id="phone" type="tel" required>
<label for="notice">What is your notice period?</label><input id="notice" required>
<label for="start">Earliest start date</label><input id="start" type="date" required>
<label for="sponsor">Will you require visa sponsorship?</label><select id="sponsor" required><option>Yes</option><option>No</option></select>
<label for="auth">Are you authorized to work in the UK?</label><select id="auth" required><option>Yes</option><option>No</option></select>
<label for="salary">Desired salary</label><input id="salary" type="number" required>
<label for="travel">What percentage are you willing to travel?</label><input id="travel" type="number" required>
<label for="legal">I certify the information is accurate</label><input id="legal" type="checkbox" required>
<label for="resume">Resume / CV</label><input id="resume" type="file" required>
<label for="cover">Cover Letter</label><input id="cover" type="file">
<textarea id="why" aria-label="Why are you interested in this role?" maxlength="500"></textarea>
<button type="submit">Submit Application</button></form></body></html>'''


def test_portal_detection_and_safe_plan(tmp_path):
    service = _isolated_service(tmp_path)
    plan = service.preview_html(GREENHOUSE, "https://boards.greenhouse.io/example/jobs/1", {"market": "united_kingdom", "company": "Example", "job_title": "Finance Manager"}, application_date="2026-09-10")
    fields = {field.label: field for field in plan.fields}
    assert plan.portal == "GREENHOUSE" and plan.final_submit_detected
    assert fields["Email"].answer == "gautamsankr@gmail.com"
    assert fields["Mobile number"].answer == "+9779851139824"
    assert fields["What is your notice period?"].answer == "7 calendar days"
    assert fields["Earliest start date"].answer == "2026-09-17"
    assert fields["Will you require visa sponsorship?"].answer == "Yes"
    assert fields["Are you authorized to work in the UK?"].answer == "No"
    assert fields["Desired salary"].action == "REVIEW"
    assert fields["What percentage are you willing to travel?"].action == "REVIEW"
    assert fields["I certify the information is accurate"].action == "REVIEW"
    assert len(plan.document_requirements) == 2 and not plan.application_submitted
    assert APPLICATION_DRY_RUN is True and APPLICATION_AUTO_SUBMIT is False


def test_lever_workday_generic_and_unknown_detection(tmp_path):
    service = _isolated_service(tmp_path)
    assert service.detect_portal("https://jobs.lever.co/company/x", "<form></form>") == "LEVER"
    assert service.detect_portal("https://tenant.myworkdayjobs.com/x", "<form></form>") == "WORKDAY"
    assert service.detect_portal("https://jobs.smartrecruiters.com/x", "<form></form>") == "SMARTRECRUITERS"
    assert service.detect_portal("https://jobs.successfactors.com/x", "<form></form>") == "SUCCESSFACTORS"
    assert service.detect_portal("https://company.oraclecloud.com/x", "<form></form>") == "ORACLE"
    assert service.detect_portal("https://jobs.ashbyhq.com/company/1", "<form></form>") == "ASHBY"
    assert service.detect_portal("https://example.test/x", "<form></form>") == "GENERIC"
    assert service.detect_portal("https://example.test/x", "<div>nothing</div>") == "UNKNOWN"


def test_auth_mfa_captcha_and_submit_are_detected_not_actioned(tmp_path):
    service = _isolated_service(tmp_path)
    for html, expected in (("<form>Log in<button>Apply</button></form>", "AUTH_REQUIRED"), ("<form>Enter verification code MFA</form>", "MFA_REQUIRED"), ("<form>reCAPTCHA</form>", "CAPTCHA_REQUIRED")):
        plan = service.preview_html(html, vacancy={"market": "united_kingdom"})
        assert plan.readiness == expected and plan.application_submitted is False
    plan = service.preview_html("<form><button>Apply</button><button>Next</button><button>Submit Application</button></form>")
    assert plan.final_submit_detected and plan.safe_navigation_detected and plan.application_submitted is False


def test_required_optional_and_url_safety(tmp_path):
    service = _isolated_service(tmp_path)
    plan = service.preview_html('<form><label for="a">Unknown required answer</label><input id="a" required><label for="b">Optional mystery</label><input id="b"></form>')
    assert plan.fields[0].action == "REVIEW" and plan.fields[1].action == "SKIP"
    import pytest
    with pytest.raises(ValueError): service.preview_html("<form></form>", "file:///unsafe")


def test_route_classification_and_linkedin_listing_suppression(tmp_path):
    resolver = ApplicationRouteResolver()
    assert resolver.classify_url("https://uk.linkedin.com/jobs/view/123") == "JOB_LISTING_URL"
    assert resolver.classify_url("https://www.indeed.com/viewjob?jk=1") == "JOB_LISTING_URL"
    assert resolver.classify_url("https://boards.greenhouse.io/example/jobs/1") == "ATS_URL"
    external = resolver.resolve({"job_url": "https://uk.linkedin.com/jobs/view/123"}, '<html><a href="https://boards.greenhouse.io/example/jobs/1" aria-label="Apply on employer career site">Apply externally</a></html>')
    assert external.resolution_status == "RESOLVED" and external.portal == "GREENHOUSE"
    service = _isolated_service(tmp_path)
    listing = service.preview_html('<html><form><input type="email"><input type="password"><input name="search">Sign in reCAPTCHA</form></html>', "https://uk.linkedin.com/jobs/view/123", {"job_url": "https://uk.linkedin.com/jobs/view/123"})
    assert listing.page_purpose == "CAPTCHA" and listing.fields == []
    assert listing.readiness == "CAPTCHA_REQUIRED"


def test_final_action_labels_never_change_submission_state(tmp_path):
    service = _isolated_service(tmp_path)
    html = "<form>" + "".join(f"<button>{label}</button>" for label in ("Submit", "Submit Application", "Finish", "Complete Application", "Send Application", "Apply Now")) + "</form>"
    plan = service.preview_html(html)
    assert plan.final_submit_detected is True
    assert plan.application_submitted is False and plan.fields_filled == 0


GREENHOUSE_INVISIBLE_RECAPTCHA_BOILERPLATE = '''<html><head>
<style>.grecaptcha-badge{visibility:hidden;}.page-full-width{width:100%;}</style>
<script>window.gon = {"google_recaptcha_invisible_key":"6LfmCbCpAAAAACHntbHUshzUoAmJ_wY9lQivLFx0","dropbox_chooser_api_key":"x"};</script>
</head><body class="greenhouse"><form id="application_form">
<label for="email">Email</label><input id="email" type="email" required>
<button type="submit">Submit Application</button>
</form></body></html>'''

LEVER_INVISIBLE_HCAPTCHA_BOILERPLATE = '''<html><head>
<style>.page-centered,.g-recaptcha div,.h-captcha-spacing{display:block;margin:0 auto;}.linkedin-login-success{display:none;}</style>
<script type="text/javascript" src="https://js.hcaptcha.com/1/secure-api.js?host=jobs.lever.co&onload=onload" async defer></script>
</head><body><form id="application-form" action="/apply">
<label for="email">Email</label><input id="email" type="email" required>
<button type="submit">Submit Application</button>
</form></body></html>'''

GENUINE_VISIBLE_CAPTCHA = '''<html><body><form>
<label for="email">Email</label><input id="email" type="email" required>
<div class="challenge">Please complete the CAPTCHA below to continue.</div>
<button type="submit">Submit Application</button>
</form></body></html>'''

GENUINE_VISIBLE_LOGIN_MFA = '''<html><body>
<h1>Sign in to continue</h1>
<p>Enter the verification code (MFA) sent to your phone to access this application.</p>
<form><input type="password"><button>Log in</button></form>
</body></html>'''


def test_invisible_recaptcha_boilerplate_does_not_false_positive_greenhouse(tmp_path):
    """Task 21.18C regression A: ordinary Greenhouse page carrying invisible
    reCAPTCHA-v3 JS config + CSS badge boilerplate (present on virtually every
    real Greenhouse page) must not be classified as CAPTCHA."""
    service = _isolated_service(tmp_path)
    plan = service.preview_html(GREENHOUSE_INVISIBLE_RECAPTCHA_BOILERPLATE, "https://boards.greenhouse.io/example/jobs/1", {"market": "united_kingdom"})
    assert plan.page_purpose == "APPLICATION_FORM"
    assert plan.readiness not in {"CAPTCHA_REQUIRED", "AUTH_REQUIRED", "MFA_REQUIRED"}
    assert plan.captcha == "NO" and plan.fields


def test_invisible_hcaptcha_boilerplate_does_not_false_positive_lever(tmp_path):
    """Task 21.18C regression B: ordinary Lever page carrying invisible
    hCaptcha script tag + CSS selector boilerplate must not be classified as
    CAPTCHA, and unrelated CSS class names containing "login" must not
    trigger LOGIN classification either."""
    service = _isolated_service(tmp_path)
    plan = service.preview_html(LEVER_INVISIBLE_HCAPTCHA_BOILERPLATE, "https://jobs.lever.co/company/x/apply", {"market": "united_kingdom"})
    assert plan.page_purpose == "APPLICATION_FORM"
    assert plan.readiness not in {"CAPTCHA_REQUIRED", "AUTH_REQUIRED", "MFA_REQUIRED"}
    assert plan.captcha == "NO" and plan.authentication == "NO" and plan.fields


def test_genuinely_visible_captcha_still_fails_closed(tmp_path):
    """Task 21.18C regression C: real, human-visible CAPTCHA text must still
    block -- the fix must not weaken real protection."""
    service = _isolated_service(tmp_path)
    plan = service.preview_html(GENUINE_VISIBLE_CAPTCHA, "https://boards.greenhouse.io/example/jobs/1", {"market": "united_kingdom"})
    assert plan.page_purpose == "CAPTCHA" and plan.readiness == "CAPTCHA_REQUIRED"


def test_genuinely_visible_login_mfa_still_fails_closed(tmp_path):
    """Task 21.18C regression D: real, human-visible login/MFA text must
    still block."""
    service = _isolated_service(tmp_path)
    plan = service.preview_html(GENUINE_VISIBLE_LOGIN_MFA, "https://example.test/apply", {"market": "united_kingdom"})
    assert plan.page_purpose in {"LOGIN", "MFA"} and plan.readiness in {"AUTH_REQUIRED", "MFA_REQUIRED"}


def test_lever_apply_route_resolution():
    resolver = ApplicationRouteResolver()
    # Base JD URL (no /apply) resolves to the /apply form.
    route = resolver.resolve({"application_url": "https://jobs.lever.co/nava/f6acbe27-62e4-42de-820f-6ae8c0dadcd8"})
    assert route.resolution_status == "RESOLVED"
    assert route.application_url == "https://jobs.lever.co/nava/f6acbe27-62e4-42de-820f-6ae8c0dadcd8/apply"
    assert route.portal == "LEVER"

    # A URL that already points at the apply form is kept unchanged.
    route = resolver.resolve({"application_url": "https://jobs.lever.co/nava/f6acbe27-62e4-42de-820f-6ae8c0dadcd8/apply"})
    assert route.application_url == "https://jobs.lever.co/nava/f6acbe27-62e4-42de-820f-6ae8c0dadcd8/apply"

    # Trailing slash on the base URL is handled without double-appending.
    route = resolver.resolve({"application_url": "https://jobs.lever.co/nava/f6acbe27-62e4-42de-820f-6ae8c0dadcd8/"})
    assert route.application_url == "https://jobs.lever.co/nava/f6acbe27-62e4-42de-820f-6ae8c0dadcd8/apply"

    # Non-Lever ATS hosts are never touched by the Lever-specific suffix.
    route = resolver.resolve({"application_url": "https://boards.greenhouse.io/example/jobs/1"})
    assert route.application_url == "https://boards.greenhouse.io/example/jobs/1"


def test_document_preparation_uses_only_exact_existing_vacancy_documents(tmp_path):
    service = _isolated_service(tmp_path)
    resume = tmp_path / "Resume.docx"; resume.write_bytes(b"fixture")
    cover = tmp_path / "Cover.docx"; cover.write_bytes(b"fixture")
    plan = service.preview_html(GREENHOUSE, vacancy={"market": "united_kingdom", "resume_path": str(resume), "cover_letter_path": str(cover)})
    documents = {item["kind"]: item for item in plan.document_requirements}
    assert documents["RESUME"]["action"] == "READY_FOR_UPLOAD"
    assert documents["COVER_LETTER"]["action"] == "READY_FOR_UPLOAD"
