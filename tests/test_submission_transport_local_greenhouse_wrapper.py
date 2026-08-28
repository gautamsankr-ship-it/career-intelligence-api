"""Two-origin localhost employer-wrapper → Greenhouse transport validation."""
import json
import pytest
from urllib.parse import urlparse

from app.models.submission import SubmissionContext
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_submission_service import ApplicationSubmissionService
from app.services.portal_evidence import detect_portal_evidence
from helpers.local_ats_server import LocalATS, LocalEmployerWrapper
from helpers.synthetic_answer_engine import SyntheticAnswerEngine
from test_final_review_service import setup

def test_client_navigation_route_guard_aborts_real_chromium_request():
    """Standalone real Chromium proof for an abortable client-side navigation."""
    import asyncio
    from playwright.async_api import async_playwright
    ats=LocalATS("external_client_navigation"); url=ats.start(); events=[]
    async def run():
        async with async_playwright() as api:
            browser=await api.chromium.launch(headless=True); context=await browser.new_context(); page=await context.new_page()
            async def guard(route):
                parsed=urlparse(route.request.url)
                if parsed.hostname not in {"127.0.0.1","localhost"}:
                    events.append({"event":"route_abort","host":parsed.hostname or ""}); await route.abort(); return
                await route.continue_()
            await page.route("**/*",guard)
            try: await page.goto(url,wait_until="domcontentloaded",timeout=3000); await page.wait_for_timeout(250)
            except Exception: pass
            await context.close(); await browser.close()
    try:
        asyncio.run(run())
        assert events == [{"event":"route_abort","host":"untrusted.example.invalid"}]
    finally: ats.close()


def _pdf(path, label):
    path.write_bytes(("%PDF-1.4\nstream\n"+label+"\nendstream\n%%EOF\n").encode())
    return path


def _wrapper_transport(tmp_path, variant="matching", network_events=None):
    ats=LocalATS("matching" if variant in {"matching","no_evidence","multiple"} else variant)
    ats_url=ats.start()
    wrapper=LocalEmployerWrapper(ats_url, variant)
    wrapper_url=wrapper.start()
    ats.redirect_url=wrapper_url
    reviews,record,pkg,execution=setup(tmp_path)
    pkg.application_url=wrapper_url; pkg.application_portal="GREENHOUSE"
    pkg.resume_path=str(_pdf(tmp_path/"Test_Candidate_Resume.pdf", "Synthetic Resume"))
    pkg.cover_letter_path=str(_pdf(tmp_path/"Test_Candidate_Cover_Letter.pdf", "Synthetic Cover Letter"))
    execution.update({"package_id":pkg.package_id,"application_url":wrapper_url,"portal":"GREENHOUSE"})
    (tmp_path/"executions"/"exec.json").write_text(json.dumps(execution))
    review=reviews.create(42); review.review_status="APPROVED_FOR_SUBMISSION"; reviews._save(review)
    browser=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),package_service=reviews.package_service,review_service=reviews,allowed_hosts={"127.0.0.1","localhost"},network_events=network_events,preview_folder=tmp_path)
    context=SubmissionContext(review.review_id,42,pkg.package_id,"exec","GREENHOUSE",wrapper_url,review.fingerprint)
    return ats,wrapper,browser,context,review,record


def test_wrapper_evidence_and_reconcile_only_use_one_cross_origin_transition(tmp_path):
    ats,wrapper,browser,context,review,_=_wrapper_transport(tmp_path)
    try:
        evidence=detect_portal_evidence(context.application_url, '<main data-portal="greenhouse"><a data-portal="greenhouse">Apply</a></main>')
        assert evidence.portal == "GREENHOUSE" and evidence.confidence == "HIGH" and evidence.wrapper_detected
        result=browser.reconcile_final_url(context)
        assert result["outcome"] == "FINAL_REVIEW_READY"
        assert ats.data["visits"][:4] == ["1","2","3","4"]
        assert wrapper.data["visits"]
        assert ats.data["review_clicks"] == 1 and ats.data["submit"] == 0
        assert context.application_url.startswith("http://127.0.0.1")
    finally:
        wrapper.close(); ats.close()


@pytest.mark.parametrize(("variant","expected"),[("matching","SUBMISSION_CONFIRMED"),("failure","SUBMISSION_FAILED"),("uncertain","SUBMISSION_OUTCOME_UNCERTAIN")])
def test_wrapper_submission_service_outcomes_keep_one_click(tmp_path, variant, expected):
    ats,wrapper,browser,context,review,record=_wrapper_transport(tmp_path,variant)
    try:
        service=ApplicationSubmissionService(review_service=browser.review_service,browser=browser,receipt_dir=tmp_path/"receipts",lock_dir=tmp_path/"locks")
        assert service.submit(review.review_id,"wrong").outcome == "SUBMISSION_CANCELLED"
        receipt=service.submit(review.review_id,f"SUBMIT {review.review_id}")
        assert receipt.outcome == expected and ats.data["submit"] == 1
        if expected == "SUBMISSION_CONFIRMED":
            assert record["status"] == "APPLIED"
            assert service.submit(review.review_id,f"SUBMIT {review.review_id}").outcome == "ALREADY_SUBMITTED"
        elif expected == "SUBMISSION_OUTCOME_UNCERTAIN":
            assert service.submit(review.review_id,f"SUBMIT {review.review_id}").outcome == "SUBMISSION_OUTCOME_UNCERTAIN"
        assert ats.data["submit"] == 1
    finally:
        wrapper.close(); ats.close()


@pytest.mark.parametrize(("variant","signal"),[("no_evidence","ROUTE_UNRESOLVED"),("multiple","WRAPPER_TARGET_AMBIGUOUS"),("conflict","WRAPPER_PORTAL_CONFLICT"),("portal_mismatch","MISMATCH_PORTAL"),("dead","ROUTE_UNRESOLVED"),("external","EXTERNAL_REDIRECT_BLOCKED"),("captcha","CAPTCHA"),("login","LOGIN"),("mfa","MFA"),("account","ACCOUNT_CREATION")])
def test_untrusted_or_ambiguous_wrapper_never_enters_ats(tmp_path, variant, signal):
    ats,wrapper,browser,context,_,_=_wrapper_transport(tmp_path,variant)
    try:
        result=browser.reconcile_final_url(context)
        assert signal in result["signals"]
        assert ats.data["submit"] == 0
    finally:
        wrapper.close(); ats.close()

def test_wrapper_redirect_loop_is_bounded_without_final_click(tmp_path):
    ats,wrapper,browser,context,_,_=_wrapper_transport(tmp_path,"loop")
    try:
        result=browser.reconcile_final_url(context)
        assert result == {"outcome":"SUBMISSION_FAILED","signals":["LOOP_DETECTED"]}
        assert ats.data["submit"] == 0
    finally:
        wrapper.close(); ats.close()

def test_external_redirect_is_intercepted_before_transmission(tmp_path):
    events=[]; ats,wrapper,browser,context,_,_=_wrapper_transport(tmp_path,"external_redirect",events)
    try:
        result=browser.reconcile_final_url(context)
        assert result == {"outcome":"SUBMISSION_FAILED","signals":["EXTERNAL_REDIRECT_BLOCKED"]}
        assert len(events) >= 1 and all(item["host"] == "untrusted.example.invalid" for item in events)
        assert any(item.get("observed") for item in events)
        assert not any(item.get("aborted") for item in events)
        assert ats.data["submit"] == 0
    finally:
        wrapper.close(); ats.close()

def test_external_client_navigation_is_aborted_by_transport_guard(tmp_path):
    events=[]; ats,wrapper,browser,context,_,_=_wrapper_transport(tmp_path,"external_client_navigation",events)
    try:
        result=browser.reconcile_final_url(context)
        assert result == {"outcome":"SUBMISSION_FAILED","signals":["EXTERNAL_REDIRECT_BLOCKED"]}
        assert any(item.get("aborted") for item in events)
        assert ats.data["submit"] == 0
    finally:
        wrapper.close(); ats.close()
