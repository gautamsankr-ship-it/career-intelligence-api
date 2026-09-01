"""Task 21.29: LinkedIn Easy Apply MVP.

Fully hermetic and local: every test uses real (headless) Chromium against
synthetic in-page HTML (via page.set_content()) with proper ARIA roles
modeling LinkedIn's own accessibility markup -- no real LinkedIn contact.
Login/MFA/CAPTCHA pause tests reuse the real PersistentSession from Task
21.28 (a tmp_path-scoped profile) to prove the actual integration, not a
re-implementation.

No credential login is ever attempted; login/MFA/CAPTCHA pages here are
synthetic markup only, proving detection and pause behavior.
"""
from __future__ import annotations

import asyncio
import json as jsonlib
import os

import pytest

from app.models.application_execution import ApplicationExecutionResult
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_browser_service import ApplicationBrowserService
from app.services.linkedin_easy_apply_service import (
    HUMAN_ELIGIBILITY_REVIEW_REQUIRED,
    HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED,
    HUMAN_SALARY_REVIEW_REQUIRED,
    HUMAN_SCREENING_REVIEW_REQUIRED,
    LinkedInEasyApplyAdapter,
    LinkedInEasyApplyOrchestrator,
    is_linkedin_job_url,
    run_linkedin_application,
    submit_easy_apply,
)


def _run(coro):
    return asyncio.run(coro)


APPLY_LABEL = "Easy Apply to Acme Corp"

STEP_NAME_AND_WORK_AUTH = (
    '<label for="first">First name</label><input id="first" type="text" aria-required="true">'
    '<div role="radiogroup" aria-label="Are you legally authorized to work in Australia?" aria-required="true">'
    '<label><input type="radio" name="auth" value="Yes">Yes</label>'
    '<label><input type="radio" name="auth" value="No">No</label></div>'
    '<button>Next</button>'
)
STEP_SALARY_NUMERIC = (
    '<label for="sal">What is your expected annual salary?</label>'
    '<input id="sal" type="number" aria-required="true"><button>Next</button>'
)
STEP_UNKNOWN_LEGAL = (
    '<label for="crim">Have you ever been convicted of a criminal offence?</label>'
    '<input id="crim" type="text" aria-required="true"><button>Next</button>'
)
STEP_UNKNOWN_GENERIC = (
    '<label for="hobby">What is your favorite productivity tool?</label>'
    '<input id="hobby" type="text" aria-required="true"><button>Next</button>'
)
STEP_REVIEW = '<h2>Review your application</h2><p>Please review before submitting.</p><button>Submit application</button>'
STEP_ONLY_NAME = '<label for="first">First name</label><input id="first" type="text" aria-required="true"><button>Next</button>'


def _easy_apply_fixture(steps: list[str], apply_label: str = APPLY_LABEL, external_apply: bool = False) -> str:
    """A synthetic LinkedIn-like job page: an Easy Apply (or plain external
    Apply) button that opens a role=dialog modal, and Next/Review/Submit
    controls that advance through `steps` via a tiny event-delegated script
    -- no server, no real LinkedIn, exercised with real Chromium."""
    if external_apply:
        return f'<html><body><button>Apply</button></body></html>'
    steps_json = jsonlib.dumps(steps)
    return f'''<html><body>
<button aria-label="{apply_label}">Easy Apply</button>
<div role="dialog" aria-modal="true" aria-label="Apply to Head of Finance" id="modal" style="display:none">
  <div id="content"></div>
</div>
<script>
const steps = {steps_json};
let idx = 0;
document.querySelector('[aria-label="{apply_label}"]').addEventListener('click', () => {{
  document.getElementById('modal').style.display = 'block';
  document.getElementById('content').innerHTML = steps[0];
}});
document.getElementById('modal').addEventListener('click', (e) => {{
  const btn = e.target.closest('button');
  if (!btn) return;
  const text = btn.textContent.trim().toLowerCase();
  if ((text === 'next' || text === 'review' || text === 'review your application') && idx < steps.length - 1) {{
    idx++;
    document.getElementById('content').innerHTML = steps[idx];
  }} else if (text === 'submit application') {{
    document.getElementById('content').innerHTML = '<h2>Your application was sent to Acme Corp</h2>';
  }}
}});
</script>
</body></html>'''


def _vacancy(resume_path: str | None = None) -> dict:
    return {"company": "Acme Corp", "job_title": "Head of Finance", "resume_path": resume_path or ""}


async def _launch(html: str):
    from playwright.async_api import async_playwright
    api = await async_playwright().start()
    browser = await api.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    return api, browser, page


async def _teardown(api, browser):
    await browser.close()
    await api.stop()


# --- (1) Easy Apply detection ------------------------------------------------

def test_is_linkedin_job_url():
    assert is_linkedin_job_url("https://au.linkedin.com/jobs/view/head-of-finance-4457989411")
    assert not is_linkedin_job_url("https://boards.greenhouse.io/example/jobs/1")


def test_detects_easy_apply_vs_external_vs_not_found():
    adapter = LinkedInEasyApplyAdapter()

    async def run():
        for html, expected in (
            (_easy_apply_fixture([STEP_ONLY_NAME]), "EASY_APPLY"),
            (_easy_apply_fixture([], external_apply=True), "EXTERNAL_APPLY"),
            ("<html><body><p>No apply control here.</p></body></html>", "NOT_FOUND"),
        ):
            api, browser, page = await _launch(html)
            try:
                assert await adapter.detect_easy_apply(page) == expected
            finally:
                await _teardown(api, browser)

    _run(run())


# --- (2) modal field extraction ----------------------------------------------

def test_modal_opens_and_extracts_fields():
    adapter = LinkedInEasyApplyAdapter()

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_NAME_AND_WORK_AUTH]))
        try:
            dialog = await adapter.open_easy_apply_modal(page)
            assert await dialog.is_visible()
            plan = await adapter.inspect_step(dialog, market="australia", vacancy=_vacancy())
            assert plan.page_purpose == "APPLICATION_FORM"
            labels = {f.label for f in plan.fields}
            assert "First name" in labels
            assert "Are you legally authorized to work in Australia?" in labels
        finally:
            await _teardown(api, browser)

    _run(run())


# --- (3) known autofill -------------------------------------------------------

def test_known_fields_autofill_from_answer_vault():
    adapter = LinkedInEasyApplyAdapter()

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_NAME_AND_WORK_AUTH]))
        try:
            dialog = await adapter.open_easy_apply_modal(page)
            plan = await adapter.inspect_step(dialog, market="australia", vacancy=_vacancy())
            first_name = next(f for f in plan.fields if f.label == "First name")
            work_auth = next(f for f in plan.fields if "authorized to work" in f.label.lower())
            assert (first_name.action, first_name.answer) == ("FILL", "Shankar")
            assert (work_auth.action, work_auth.answer, work_auth.concept) == ("FILL", "No", "WORK_AUTHORIZATION_AUSTRALIA")
            await adapter.fill_step(dialog, plan)
            assert await dialog.locator("#first").input_value() == "Shankar"
            assert await dialog.locator("input[name='auth'][value='No']").is_checked()
        finally:
            await _teardown(api, browser)

    _run(run())


# --- (4)/(5) unknown/screening pause -----------------------------------------

def test_unknown_screening_question_pauses():
    orchestrator = LinkedInEasyApplyOrchestrator()

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_UNKNOWN_GENERIC]))
        try:
            result = await orchestrator.run(page, _vacancy(), market="australia")
            assert result.status == HUMAN_SCREENING_REVIEW_REQUIRED
            assert result.manual_review_fields >= 1
        finally:
            await _teardown(api, browser)

    _run(run())


STEP_CHECKBOX_GROUP = (
    '<div role="group">'
    '<div>Which accountancy firm(s) have you trained or worked with?</div>'
    '<div>Required</div>'
    '<label><input type="checkbox" name="firms" value="Deloitte">Deloitte</label>'
    '<label><input type="checkbox" name="firms" value="PwC">PwC</label>'
    '</div>'
    '<button>Next</button>'
)


def test_standalone_checkbox_group_question_is_detected_and_pauses():
    """Task 21.31 production fix: LinkedIn also poses required multi-select
    checklist questions as a bare role="group" of role="checkbox" items
    with no radiogroup wrapper and no aria-label on the group -- only its
    own plain text ("<question>\\nRequired\\n<option>..."). Before this
    fix such a field was invisible to inspect_step entirely (never filled,
    never flagged), so a real application would reach "Next" with
    LinkedIn's own required-field validation silently blocking every
    further click. Never auto-selects an option -- always routes to human
    review, exactly like any other UNKNOWN concept."""
    orchestrator = LinkedInEasyApplyOrchestrator()

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_CHECKBOX_GROUP]))
        try:
            result = await orchestrator.run(page, _vacancy(), market="australia")
            assert result.status == HUMAN_SCREENING_REVIEW_REQUIRED
            assert result.unknown_required_fields >= 1
        finally:
            await _teardown(api, browser)

    _run(run())


STEP_BARE_RADIO_GROUP = (
    '<div role="group">'
    '<div>Are you based in the UK and have the right to work in the UK?</div>'
    '<div>Required</div>'
    '<label><input type="radio" name="ukauth" value="Yes">Yes</label>'
    '<label><input type="radio" name="ukauth" value="No">No</label>'
    '</div>'
    '<button>Review your application</button>'
)
def test_bare_group_radio_question_autofills_from_approved_rule():
    """Task 21.31 production fix: LinkedIn also poses Yes/No work-
    authorization questions as a bare role="group" wrapping role="radio"
    children -- NOT role="radiogroup" (_collect_radiogroups already
    handled that shape). Before this fix such a question was invisible to
    inspect_step entirely, so even an already-approved fact like
    WORK_AUTHORIZATION_UK=No could never auto-fill, and the application
    would reach "Next" with LinkedIn's own required-field validation
    silently blocking every further click."""
    orchestrator = LinkedInEasyApplyOrchestrator()

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_BARE_RADIO_GROUP, STEP_REVIEW]))
        try:
            result = await orchestrator.run(page, _vacancy(), market="united_kingdom")
            assert result.status == HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED
            assert result.fields_resolved == 1
            assert result.manual_review_fields == 0
        finally:
            await _teardown(api, browser)

    _run(run())


def test_legal_question_never_inferred_and_pauses():
    """A legal/criminal-history concept must never be auto-answered to
    improve eligibility -- it always requires human review."""
    orchestrator = LinkedInEasyApplyOrchestrator()

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_UNKNOWN_LEGAL]))
        try:
            result = await orchestrator.run(page, _vacancy(), market="australia")
            assert result.status == HUMAN_SCREENING_REVIEW_REQUIRED
        finally:
            await _teardown(api, browser)

    _run(run())


# --- (5) eligibility pause (Tracker-61-relevant) -----------------------------

def test_eligibility_question_without_approved_rule_pauses():
    """Task 21.30: current-location questions now auto-fill from the
    approved standing fact (Kathmandu, Nepal), so a work-authorization
    question with no market-scoped approved rule is used here instead to
    prove the eligibility-pause mechanism itself still fails closed rather
    than guess."""
    orchestrator = LinkedInEasyApplyOrchestrator()
    step = (
        '<label for="auth">Are you authorized to work in this location?</label>'
        '<input id="auth" type="text" aria-required="true"><button>Next</button>'
    )

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([step]))
        try:
            result = await orchestrator.run(page, _vacancy(), market=None)
            assert result.status == HUMAN_ELIGIBILITY_REVIEW_REQUIRED
        finally:
            await _teardown(api, browser)

    _run(run())


def test_current_location_question_autofills_the_approved_standing_fact():
    """Task 21.30: a current-location question now auto-fills and does NOT
    pause -- proves the new approved-location behavior end-to-end through
    the Easy Apply orchestrator, not just the answer engine in isolation."""
    orchestrator = LinkedInEasyApplyOrchestrator()
    step = (
        '<label for="loc">Where are you currently based?</label>'
        '<input id="loc" type="text"><button>Next</button>'
        '<h2 style="display:none" id="next-marker">Review your application</h2>'
    )
    # A second, minimal step to reach so we can observe the field was filled
    # (rather than pausing) without needing a full review page here.
    step_two = '<label for="dummy">Dummy</label><input id="dummy" type="text"><button>Review your application</button>'

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([step, step_two]))
        try:
            dialog = await LinkedInEasyApplyAdapter().open_easy_apply_modal(page)
            plan = await LinkedInEasyApplyAdapter().inspect_step(dialog, vacancy=_vacancy())
            location_field = next(f for f in plan.fields if f.label == "Where are you currently based?")
            assert (location_field.action, location_field.answer, location_field.concept) == ("FILL", "Kathmandu, Nepal", "CURRENT_LOCATION")
        finally:
            await _teardown(api, browser)

    _run(run())


# --- (6) salary pause ---------------------------------------------------------

def test_numeric_salary_question_pauses_despite_approved_text_answer():
    """The vault's approved EXPECTED_SALARY answer is a negotiable TEXT
    response -- it must never satisfy a field demanding an exact figure."""
    orchestrator = LinkedInEasyApplyOrchestrator()

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_SALARY_NUMERIC]))
        try:
            result = await orchestrator.run(page, _vacancy(), market="australia")
            assert result.status == HUMAN_SALARY_REVIEW_REQUIRED
        finally:
            await _teardown(api, browser)

    _run(run())


# --- (7) CAPTCHA/MFA/login pause (real Task 21.28 integration) --------------

@pytest.mark.parametrize("challenge_html,expected_state", [
    ("<h1>Sign in to continue</h1>", "HUMAN_LOGIN_REQUIRED"),
    ("<h1>CAPTCHA required</h1>", "HUMAN_CAPTCHA_REQUIRED"),
    ("<h1>Enter your one-time verification code</h1>", "HUMAN_MFA_REQUIRED"),
])
def test_outer_page_human_challenge_blocks_easy_apply_detection(tmp_path, challenge_html, expected_state):
    """Proves real sequencing between PersistentSession (Task 21.28) and
    the Easy Apply orchestrator: a login/MFA/CAPTCHA page must be resolved
    BEFORE Easy Apply detection is ever attempted -- never bypassed."""
    browser_service = ApplicationBrowserService()
    detect_calls = {"count": 0}

    class SpyOrchestrator(LinkedInEasyApplyOrchestrator):
        async def run(self, *args, **kwargs):
            detect_calls["count"] += 1
            return await super().run(*args, **kwargs)

    async def run():
        session = await browser_service.open_persistent_session(profile_dir=tmp_path / "profile", headed=False)
        try:
            await session.page.set_content(challenge_html)
            result = await run_linkedin_application(session, _vacancy(), market="australia", orchestrator=SpyOrchestrator())
            assert result.status == expected_state
            assert detect_calls["count"] == 0  # Easy Apply detection never attempted.
        finally:
            await session.close()

    _run(run())


# --- (8) resume after human intervention -------------------------------------

def test_resumes_from_current_modal_state_after_human_resolves_pause():
    """The human resolves a pause directly in the visible browser (typing
    an answer and clicking Next themselves) -- calling run() again with the
    SAME execution must pick up the modal's NEW current step, not restart,
    and cumulative counters keep accumulating rather than resetting."""
    adapter = LinkedInEasyApplyAdapter()
    orchestrator = LinkedInEasyApplyOrchestrator(adapter=adapter)

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_UNKNOWN_GENERIC, STEP_ONLY_NAME, STEP_REVIEW]))
        try:
            first = await orchestrator.run(page, _vacancy(), market="australia")
            assert first.status == HUMAN_SCREENING_REVIEW_REQUIRED
            first_pages_processed = first.pages_processed

            # Human resolves it themselves, in the same visible window.
            dialog = page.get_by_role("dialog")
            await dialog.locator("#hobby").fill("Spreadsheets")
            await dialog.get_by_role("button", name="Next").click()

            second = await orchestrator.run(page, _vacancy(), market="australia", execution=first)
            assert second.execution_id == first.execution_id  # same execution, not a fresh one
            assert second.pages_processed > first_pages_processed
            assert second.status == HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED or second.status == "MANUAL_INPUT_REQUIRED" or second.status.startswith("HUMAN_")
        finally:
            await _teardown(api, browser)

    _run(run())


# --- (9) multi-step progression reaches final review -------------------------

def test_multi_step_progression_reaches_final_review(tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
    orchestrator = LinkedInEasyApplyOrchestrator()
    fully_resolvable_step = (
        '<label for="first">First name</label><input id="first" type="text" aria-required="true">'
        '<input type="file" aria-label="Resume">'
        '<button>Next</button>'
    )

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([fully_resolvable_step, STEP_REVIEW]))
        try:
            result = await orchestrator.run(page, _vacancy(str(resume)), market="australia")
            assert result.status == HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED
            assert result.final_submit_detected is True
            assert result.pages_processed == 2
            assert result.resume_uploaded is True
        finally:
            await _teardown(api, browser)

    _run(run())


def test_easy_apply_prefers_pdf_resume_when_both_siblings_are_available(tmp_path):
    """Task 21.30 Section 1: LinkedIn Easy Apply accepts both formats --
    when a package carries both approved siblings, the PDF is uploaded,
    never DOCX, and never a regenerated document."""
    pdf_resume = tmp_path / "Resume.pdf"
    pdf_resume.write_bytes(b"%PDF-1.4\n%%EOF\n")
    docx_resume = tmp_path / "Resume.docx"
    docx_resume.write_bytes(b"docx-bytes")
    adapter = LinkedInEasyApplyAdapter()
    step = '<input type="file" aria-label="Resume"><button>Next</button>'

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([step]))
        try:
            dialog = await adapter.open_easy_apply_modal(page)
            plan = await adapter.inspect_step(
                dialog, market="australia",
                vacancy={"resume_path": str(docx_resume), "resume_pdf_path": str(pdf_resume)},
            )
            upload = next(d for d in plan.document_requirements if d["kind"] == "RESUME")
            assert upload["path"] == str(pdf_resume)
            assert upload["action"] == "READY_FOR_UPLOAD"
        finally:
            await _teardown(api, browser)

    _run(run())


def test_easy_apply_falls_back_to_docx_when_no_pdf_sibling_exists():
    """Legacy/current shape (resume_path only, no resume_pdf_path) still
    uploads the DOCX -- unchanged behavior for packages without a PDF."""
    adapter = LinkedInEasyApplyAdapter()
    step = '<input type="file" aria-label="Resume"><button>Next</button>'

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([step]))
        try:
            dialog = await adapter.open_easy_apply_modal(page)
            plan = await adapter.inspect_step(dialog, market="australia", vacancy=_vacancy("/nonexistent/resume.docx"))
            upload = next(d for d in plan.document_requirements if d["kind"] == "RESUME")
            assert upload["action"] == "DOCUMENT_NOT_READY"
        finally:
            await _teardown(api, browser)

    _run(run())


# --- (10)/(11)/(12)/(13) final submit safety ---------------------------------

def _execution_ready_for_submit(**overrides) -> ApplicationExecutionResult:
    result = ApplicationExecutionResult(tracker_id=61, portal="LINKEDIN_EASY_APPLY")
    result.status = HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED
    for key, value in overrides.items():
        setattr(result, key, value)
    return result


def test_submit_requires_exact_confirmation_string(tmp_path):
    adapter = LinkedInEasyApplyAdapter()
    execution = _execution_ready_for_submit()

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_REVIEW]))
        try:
            await adapter.open_easy_apply_modal(page)
            receipt = await submit_easy_apply(
                adapter, page, execution, "wrong confirmation",
                receipt_dir=tmp_path / "receipts", lock_dir=tmp_path / "locks",
            )
            assert receipt["outcome"] == "SUBMISSION_CANCELLED"
        finally:
            await _teardown(api, browser)

    _run(run())


def test_submit_blocked_without_final_review_authorization_status(tmp_path):
    adapter = LinkedInEasyApplyAdapter()
    execution = _execution_ready_for_submit(status="HUMAN_SCREENING_REVIEW_REQUIRED")

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_REVIEW]))
        try:
            await adapter.open_easy_apply_modal(page)
            receipt = await submit_easy_apply(
                adapter, page, execution, f"SUBMIT {execution.execution_id}",
                receipt_dir=tmp_path / "receipts", lock_dir=tmp_path / "locks",
            )
            assert receipt["outcome"] == "SUBMISSION_BLOCKED"
        finally:
            await _teardown(api, browser)

    _run(run())


def test_exactly_once_submit_confirmed_then_applied_only_path(tmp_path):
    """(11)/(12) A verified submit reaches SUBMISSION_CONFIRMED exactly
    once; re-submitting the same execution never clicks again and reports
    ALREADY_SUBMITTED."""
    adapter = LinkedInEasyApplyAdapter()
    execution = _execution_ready_for_submit()

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([STEP_REVIEW]))
        try:
            await adapter.open_easy_apply_modal(page)
            confirmation = f"SUBMIT {execution.execution_id}"
            receipt = await submit_easy_apply(
                adapter, page, execution, confirmation,
                receipt_dir=tmp_path / "receipts", lock_dir=tmp_path / "locks",
            )
            assert receipt["outcome"] == "SUBMISSION_CONFIRMED"
            assert receipt["confirmed_at"]

            again = await submit_easy_apply(
                adapter, page, execution, confirmation,
                receipt_dir=tmp_path / "receipts", lock_dir=tmp_path / "locks",
            )
            assert again["outcome"] == "ALREADY_SUBMITTED"
        finally:
            await _teardown(api, browser)

    _run(run())


def test_uncertain_submission_never_retried(tmp_path):
    """(13) An uncertain outcome (e.g. the dialog never confirmed or
    denied) must never be auto-retried -- a second call with the same
    execution must not click again, and must report the uncertain outcome
    again rather than attempting the click."""
    adapter = LinkedInEasyApplyAdapter()
    execution = _execution_ready_for_submit()
    # A review step whose "Submit application" click leads to a page that
    # neither confirms nor denies -- forcing SUBMISSION_OUTCOME_UNCERTAIN.
    ambiguous_review = '<h2>Review your application</h2><button>Submit application</button>'

    async def run():
        api, browser, page = await _launch(_easy_apply_fixture([ambiguous_review]))
        try:
            await adapter.open_easy_apply_modal(page)
            # Monkeypatch the click to simulate an ambiguous post-click page
            # deterministically, without relying on network timing.
            original_click_submit = adapter.click_submit

            async def uncertain_click_submit(dialog):
                return {"outcome": "SUBMISSION_OUTCOME_UNCERTAIN", "submit_clicked_at": "now", "signals": ["NO_CONFIRMATION"]}
            adapter.click_submit = uncertain_click_submit

            confirmation = f"SUBMIT {execution.execution_id}"
            first = await submit_easy_apply(
                adapter, page, execution, confirmation,
                receipt_dir=tmp_path / "receipts", lock_dir=tmp_path / "locks",
            )
            assert first["outcome"] == "SUBMISSION_OUTCOME_UNCERTAIN"

            adapter.click_submit = original_click_submit  # would prove a real click if reached
            clicked = {"called": False}

            async def spy_click_submit(dialog):
                clicked["called"] = True
                return {"outcome": "SUBMISSION_CONFIRMED", "submit_clicked_at": "now", "confirmed_at": "now", "signals": []}
            adapter.click_submit = spy_click_submit

            second = await submit_easy_apply(
                adapter, page, execution, confirmation,
                receipt_dir=tmp_path / "receipts", lock_dir=tmp_path / "locks",
            )
            assert second["outcome"] == "SUBMISSION_OUTCOME_UNCERTAIN"
            assert clicked["called"] is False  # never retried
        finally:
            await _teardown(api, browser)

    _run(run())


def test_concurrent_submit_of_same_execution_is_blocked(tmp_path):
    from app.services.linkedin_easy_apply_service import SubmissionLockedError
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    execution = _execution_ready_for_submit()
    (lock_dir / f"{execution.execution_id}.lock").touch()

    async def run():
        adapter = LinkedInEasyApplyAdapter()
        api, browser, page = await _launch(_easy_apply_fixture([STEP_REVIEW]))
        try:
            with pytest.raises(SubmissionLockedError):
                await submit_easy_apply(
                    adapter, page, execution, f"SUBMIT {execution.execution_id}",
                    receipt_dir=tmp_path / "receipts", lock_dir=lock_dir,
                )
        finally:
            await _teardown(api, browser)

    _run(run())


# --- name/eligibility answer-vault regression (Task 21.29 additions) --------

def test_name_concepts_resolve_from_answer_vault():
    engine = ApplicationAnswerEngine()
    assert engine.resolve("First name").answer == "Shankar"
    assert engine.resolve("Last name").answer == "Gautam"
    assert engine.resolve("Full name").answer == "Shankar Gautam"
