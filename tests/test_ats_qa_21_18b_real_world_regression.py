"""Task 21.18B/21.18C: local, sanitized regression fixtures pinning defects
found during real-ATS compatibility QA against live Greenhouse/Lever/Ashby/
Workday vacancies (see the Task 21.18B report) and their Task 21.18C fix.
No live network calls, no cookies, no session data, no PII -- these are
hand-built synthetic pages that reproduce the *structural pattern* observed
on real pages, not captured real HTML.

Task 21.18C fixed the CAPTCHA/auth false-positive defect documented below by
classifying against script/style-stripped visible text instead of raw HTML
(application_browser_service.py, application_route_resolver.py). This test
now asserts the corrected behavior.
"""
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_answer_vault import ApplicationAnswerVault

# A realistic Greenhouse-style page: a normal, fully fillable application
# form, PLUS the invisible-reCAPTCHA-v3 boilerplate that real Greenhouse/
# Lever/Ashby pages carry on essentially every page (a JS config constant
# and a CSS rule hiding the badge) -- present whether or not any human is
# ever actually challenged. Real Webflow/Nava/Blacksmith Agency vacancies
# tested live on 2026-08-30 all carried this exact pattern.
REALISTIC_GREENHOUSE_FORM_WITH_INVISIBLE_RECAPTCHA = """
<html><head>
<style>.grecaptcha-badge { visibility: hidden; }</style>
<script>window.GH = {"GOOGLE_RECAPTCHA_INVISIBLE_KEY":"6LfmcbcpAAAAAChNTbhUShzUOAMj_wY9LQIvLFX0","GOOGLE_RECAPTCHA_ENDPOINT":"https://www.recaptcha.net"};</script>
</head><body>
<form action="/apply" method="post">
  <label for="first_name">First Name</label><input id="first_name" name="first_name" type="text" required>
  <label for="last_name">Last Name</label><input id="last_name" name="last_name" type="text" required>
  <label for="email">Email</label><input id="email" name="email" type="email" required>
  <label for="resume">Resume/CV</label><input id="resume" name="resume" type="file" required>
  <button type="submit">Submit Application</button>
</form>
</body></html>
"""


def test_invisible_recaptcha_boilerplate_no_longer_false_positives_captcha_classification():
    """FIXED (Task 21.18C): page_purpose() and preview_html()'s
    captcha/mfa/authentication flags now classify against script/style-
    stripped visible text, so standard invisible-reCAPTCHA-v3 script/CSS
    boilerplate -- carried by virtually every real Greenhouse, Lever, and
    Ashby page, whether or not a human is ever actually challenged -- no
    longer masquerades as an active CAPTCHA challenge. This was confirmed
    live against boards.greenhouse.io/webflow, jobs.lever.co/nava, and
    jobs.ashbyhq.com/Blacksmith%20Agency vacancies during the original
    Task 21.18B audit and re-verified live during Task 21.18C.
    """
    # Isolated, real (non-bare) construction -- avoid any production vault path.
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        vault = ApplicationAnswerVault(Path(d) / "vault.json")
        browser = ApplicationBrowserService(answer_engine=ApplicationAnswerEngine(vault), preview_folder=Path(d) / "previews")
        plan = browser.preview_html(
            REALISTIC_GREENHOUSE_FORM_WITH_INVISIBLE_RECAPTCHA,
            "https://boards.greenhouse.io/example/jobs/123",
            vacancy={"company": "Example"},
            persist=False,
        )
        assert "<form" in REALISTIC_GREENHOUSE_FORM_WITH_INVISIBLE_RECAPTCHA
        assert plan.page_purpose == "APPLICATION_FORM"
        assert plan.captcha == "NO"
        assert plan.readiness != "CAPTCHA_REQUIRED"
        assert len(plan.fields) == 4
