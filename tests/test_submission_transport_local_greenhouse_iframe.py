"""Real-Chromium Greenhouse IFRAME final-submission safety validation (Task 21.8C.4).

Mirrors tests/test_submission_transport_local_greenhouse.py and
tests/test_submission_transport_local_greenhouse_wrapper.py, but the ATS is
embedded as a real trusted cross-origin iframe (LocalGreenhouseIframeEmployer,
Task 21.8C.3) instead of served directly or reached via an anchor link. This
proves the SAME production submission pipeline --
ApplicationSubmissionService -> browser.submit_final_url/reconcile_final_url
-> select_application_surface -> the shared Page|Frame primitives -- safely
reaches final review / confirmed submission / failure / uncertain outcomes,
and fails closed on every blocker, when the working surface is a trusted
Frame. No separate iframe submission engine exists; nothing here reimplements
field filling, upload, navigation, or outcome classification.

Localhost/synthetic only. No real employer is ever accessed, and no test here
allows a final click except the three outcome tests (D/E/F), each exactly once.
"""
import json
import pytest

from app.models.submission import SubmissionContext
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_submission_service import ApplicationSubmissionService
from helpers.local_ats_server import LocalATS, LocalGreenhouseIframeEmployer
from helpers.synthetic_answer_engine import SyntheticAnswerEngine
from test_final_review_service import setup


def _synthetic_pdf(path, label):
    path.write_bytes(("%PDF-1.4\n1 0 obj<<>>endobj\nstream\n"+label+"\nendstream\nendobj\ntrailer<<>>\n%%EOF\n").encode())
    return path


def _prepared_iframe_transport(tmp_path, review_variant="matching", network_events=None):
    ats=LocalATS(review_variant); ats_url=ats.start()
    employer=LocalGreenhouseIframeEmployer(ats_url); wrapper_url=employer.start()
    reviews,_,pkg,execution=setup(tmp_path)
    pkg.application_url=wrapper_url; pkg.application_portal="GREENHOUSE"
    resume=_synthetic_pdf(tmp_path/"Test_Candidate_Resume.pdf", "Synthetic Resume")
    cover=_synthetic_pdf(tmp_path/"Test_Candidate_Cover_Letter.pdf", "Synthetic Cover Letter")
    pkg.resume_path=str(resume); pkg.cover_letter_path=str(cover)
    execution.update({"package_id":pkg.package_id,"application_url":wrapper_url,"portal":"GREENHOUSE"})
    (tmp_path / "executions" / "exec.json").write_text(json.dumps(execution))
    review=reviews.create(42); review.review_status="APPROVED_FOR_SUBMISSION"; reviews._save(review)
    browser=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),package_service=reviews.package_service,review_service=reviews,allowed_hosts={"127.0.0.1","localhost"},network_events=network_events)
    context=SubmissionContext(review.review_id,42,pkg.package_id,"exec","GREENHOUSE",wrapper_url,review.fingerprint)
    return browser,context,review,ats,employer,pkg


# ---- Test A: reconcile-only reaches FINAL_REVIEW_READY, never clicks ----

def test_a_reconcile_only_reaches_final_review_ready_through_trusted_iframe(tmp_path):
    browser,context,review,ats,employer,pkg=_prepared_iframe_transport(tmp_path)
    try:
        result=browser.reconcile_final_url(context)
        assert result == {"outcome":"FINAL_REVIEW_READY","final_submit_detected":True,"final_submit_clicked":False,"signals":["FINAL_REVIEW_RECONCILED","FINAL_CONTROL_VERIFIED"]}
        assert ats.data["visits"][:4] == ["1","2","3","4"]
        assert ats.data["page1_values"] == {"first":"Test","last":"Candidate","email":"test.candidate@example.invalid","phone":"+447000000000"}
        assert ats.data["page2_values"] == {"notice":"1 month","authorization":"Yes","sponsorship":"No"}
        assert ats.data["uploads"] == {"cv":"Test_Candidate_Resume.pdf","cover":"Test_Candidate_Cover_Letter.pdf"}
        assert ats.data["review_clicks"] == 1
        assert ats.data["submit"] == 0
        assert pkg.application_url == context.application_url  # stable route never replaced
    finally:
        employer.close(); ats.close()


# ---- Tests B/C: missing / wrong confirmation, wrong review id -> never click ----

@pytest.mark.parametrize("bad_confirmation", ["", "not the exact confirmation", "SUBMIT wrong-review-id"])
def test_bc_missing_or_wrong_confirmation_never_clicks_through_trusted_iframe(tmp_path, bad_confirmation):
    browser,context,review,ats,employer,pkg=_prepared_iframe_transport(tmp_path)
    try:
        service=ApplicationSubmissionService(review_service=browser.review_service,browser=browser,receipt_dir=tmp_path/"receipts",lock_dir=tmp_path/"locks")
        receipt=service.submit(review.review_id, bad_confirmation)
        assert receipt.outcome == "SUBMISSION_CANCELLED"
        assert ats.data["submit"] == 0
    finally:
        employer.close(); ats.close()


# ---- Tests D/E/F: confirmed success / failure / uncertain -- exactly one click each,
#      plus duplicate protection (Tests P/Q) ----

@pytest.mark.parametrize(("variant","expected"),[("matching","SUBMISSION_CONFIRMED"),("failure","SUBMISSION_FAILED"),("uncertain","SUBMISSION_OUTCOME_UNCERTAIN")])
def test_defpq_confirmed_outcomes_click_exactly_once_and_block_duplicates(tmp_path, variant, expected):
    browser,context,review,ats,employer,pkg=_prepared_iframe_transport(tmp_path, variant)
    try:
        service=ApplicationSubmissionService(review_service=browser.review_service,browser=browser,receipt_dir=tmp_path/"receipts",lock_dir=tmp_path/"locks")
        receipt=service.submit(review.review_id, f"SUBMIT {review.review_id}")
        assert receipt.outcome == expected
        assert ats.data["submit"] == 1  # exactly one final click reached the ATS inside the iframe
        if expected == "SUBMISSION_CONFIRMED":
            assert service.history.get_record_by_id(42)["status"] == "APPLIED"
            second=service.submit(review.review_id, f"SUBMIT {review.review_id}")
            assert second.outcome == "ALREADY_SUBMITTED"
        else:
            assert service.history.get_record_by_id(42)["status"] != "APPLIED"
            if expected == "SUBMISSION_OUTCOME_UNCERTAIN":
                second=service.submit(review.review_id, f"SUBMIT {review.review_id}")
                assert second.outcome == "SUBMISSION_OUTCOME_UNCERTAIN"
        assert ats.data["submit"] == 1  # no automatic retry ever reaches the ATS again
        persisted=next((tmp_path/"receipts").glob("*.json")).read_text().lower()
        assert all(word not in persisted for word in ("cookie","csrf","otp","password","validitytoken","token"))
        assert pkg.application_url == context.application_url  # stable route never replaced
    finally:
        employer.close(); ats.close()


# ---- Tests G/H/J/K/M: pre-click fail-closed variants -- never click ----

@pytest.mark.parametrize(("variant","signal"),[
    ("screening_mismatch","MISMATCH_REVIEW_ANSWER"),   # Test G
    ("document_mismatch","MISMATCH_REVIEW_DOCUMENT"),   # Test H
    ("ambiguous","FINAL_SUBMIT_AMBIGUOUS"),              # Test J
    ("missing","FINAL_SUBMIT_NOT_FOUND"),                # Test K
    ("captcha","CAPTCHA"),                               # Test M
    ("login","LOGIN"),                                   # Test M
    ("mfa","MFA"),                                       # Test M
    ("account","ACCOUNT_CREATION"),                      # Test M
])
def test_preclick_blockers_never_click_through_trusted_iframe(tmp_path, variant, signal):
    browser,context,review,ats,employer,pkg=_prepared_iframe_transport(tmp_path, variant)
    try:
        result=browser.reconcile_final_url(context)
        assert signal in result["signals"]
        assert ats.data["submit"] == 0
    finally:
        employer.close(); ats.close()


# ---- Test L: outstanding manual/legal confirmation blocks final click ----

def test_l_manual_legal_confirmation_blocks_final_click_through_trusted_iframe(tmp_path):
    browser,context,review,ats,employer,pkg=_prepared_iframe_transport(tmp_path)
    try:
        review.pending_manual_actions=["Accuracy certification"]; browser.review_service._save(review)
        result=browser.reconcile_final_url(context)
        assert result["outcome"] == "SUBMISSION_FAILED"
        assert "MANUAL_REVIEW_REQUIRED" in result["signals"]
        assert ats.data["submit"] == 0
    finally:
        employer.close(); ats.close()


# ---- Test I: stale fingerprint (state mutated after approval) fails closed ----

def test_i_stale_fingerprint_fails_closed_through_trusted_iframe(tmp_path):
    browser,context,review,ats,employer,pkg=_prepared_iframe_transport(tmp_path)
    try:
        review.fingerprint="stale-fingerprint-does-not-match"; browser.review_service._save(review)
        result=browser.reconcile_final_url(context)
        assert result["outcome"] == "SUBMISSION_FAILED"
        assert "APPLICATION_CHANGED_AFTER_REVIEW" in result["signals"]
        assert "MISMATCH_FINGERPRINT" in result["signals"]
        assert ats.data["submit"] == 0
    finally:
        employer.close(); ats.close()


# ---- Test N: trusted frame detached before selection is excluded, not silently replaced ----

def test_n_detached_trusted_frame_is_excluded_not_silently_selected(tmp_path):
    async def run():
        from playwright.async_api import async_playwright
        ats=LocalATS(); ats_url=ats.start()
        employer=LocalGreenhouseIframeEmployer(ats_url); wrapper_url=employer.start()
        browser_service=ApplicationBrowserService()
        try:
            async with async_playwright() as api:
                chromium=await api.chromium.launch(headless=True); context=await chromium.new_context(); page=await context.new_page()
                try:
                    await page.goto(wrapper_url, wait_until="domcontentloaded")
                    # Deterministically detach the trusted iframe under full
                    # test control (no timing race), mirroring Task 21.8C.1's
                    # LocalFrameSurface detachment technique.
                    await page.locator("#remove_ats").click()
                    selection=await browser_service.select_application_surface(page, "GREENHOUSE")
                    assert selection["surface"] is None
                    assert selection["status"] == "APPLICATION_SURFACE_NOT_FOUND"
                finally:
                    await context.close(); await chromium.close()
        finally:
            employer.close(); ats.close()
    import asyncio
    asyncio.run(run())


# ---- Test: frame replaced by an untrusted/wrong-portal surface fails closed ----

def test_portal_surface_mismatch_fails_closed_through_trusted_iframe(tmp_path):
    browser,context,review,ats,employer,pkg=_prepared_iframe_transport(tmp_path, "portal_mismatch")
    try:
        result=browser.reconcile_final_url(context)
        assert result["outcome"] == "SUBMISSION_FAILED"
        # The wrong-portal iframe is never trusted, so no application surface
        # is selected at all rather than the mismatched iframe being used.
        assert ats.data["submit"] == 0
    finally:
        employer.close(); ats.close()


# ---- Test O: external client-side navigation from inside the trusted iframe is blocked ----

def test_o_external_navigation_from_inside_trusted_iframe_is_aborted(tmp_path):
    events=[]
    browser,context,review,ats,employer,pkg=_prepared_iframe_transport(tmp_path, "external_client_navigation", network_events=events)
    try:
        result=browser.reconcile_final_url(context)
        assert result == {"outcome":"SUBMISSION_FAILED","signals":["EXTERNAL_REDIRECT_BLOCKED"]}
        assert any(item.get("aborted") for item in events)
        assert all(item["host"] == "untrusted.example.invalid" for item in events)
        assert ats.data["submit"] == 0
    finally:
        employer.close(); ats.close()


# ---- Decoy controls: wrapper-level and unrelated-iframe "Submit"-style buttons
#      are permanently present in the fixture (see LocalGreenhouseIframeEmployer);
#      every test above already proves they're ignored, since a false pickup
#      of either decoy would surface as FINAL_SUBMIT_AMBIGUOUS instead of the
#      clean outcomes asserted above. This test only documents that intent.

def test_wrapper_and_unrelated_iframe_decoy_submit_controls_are_never_used(tmp_path):
    browser,context,review,ats,employer,pkg=_prepared_iframe_transport(tmp_path)
    try:
        result=browser.reconcile_final_url(context)
        assert result["outcome"] == "FINAL_REVIEW_READY"
        assert result["signals"] == ["FINAL_REVIEW_RECONCILED","FINAL_CONTROL_VERIFIED"]
        assert ats.data["submit"] == 0
    finally:
        employer.close(); ats.close()
