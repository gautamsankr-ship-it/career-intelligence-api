import json
import pytest
from app.models.submission import SubmissionContext
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_submission_service import ApplicationSubmissionService
from test_final_review_service import setup
from helpers.local_ats_server import LocalATS
from helpers.synthetic_answer_engine import SyntheticAnswerEngine

def _synthetic_pdf(path, label):
    path.write_bytes(("%PDF-1.4\n1 0 obj<<>>endobj\nstream\n"+label+"\nendstream\nendobj\ntrailer<<>>\n%%EOF\n").encode())
    return path

def _prepared_local_transport(tmp_path, server):
    reviews,_,pkg,execution=setup(tmp_path)
    pkg.application_url=server.start(); pkg.application_portal="GREENHOUSE"
    resume=_synthetic_pdf(tmp_path/"Test_Candidate_Resume.pdf", "Synthetic Resume")
    cover=_synthetic_pdf(tmp_path/"Test_Candidate_Cover_Letter.pdf", "Synthetic Cover Letter")
    pkg.resume_path=str(resume); pkg.cover_letter_path=str(cover)
    execution.update({"package_id":pkg.package_id,"application_url":pkg.application_url,"portal":"GREENHOUSE"})
    (tmp_path / "executions" / "exec.json").write_text(json.dumps(execution))
    review=reviews.create(42); review.review_status="APPROVED_FOR_SUBMISSION"; reviews._save(review)
    browser=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),package_service=reviews.package_service,review_service=reviews,preview_folder=tmp_path)
    context=SubmissionContext(review.review_id,42,pkg.package_id,"exec","GREENHOUSE",pkg.application_url,review.fingerprint)
    return browser,context,review,server

def test_local_greenhouse_pages_use_existing_high_confidence_evidence(tmp_path):
    browser=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),preview_folder=tmp_path)
    url="http://127.0.0.1:9999/greenhouse?stage="
    for stage in ("1","2","3","4"):
        assert browser.detect_portal(url+stage, LocalATS.page_html(stage)) == "GREENHOUSE"
    assert browser.detect_portal("http://127.0.0.1:9999/plain", "<form><input></form>") != "GREENHOUSE"

def test_synthetic_engine_resolves_only_approved_page_one_facts(tmp_path, monkeypatch):
    engine=SyntheticAnswerEngine()
    assert engine.resolve("First name").answer == "Test"
    assert engine.resolve("Last name").answer == "Candidate"
    assert engine.resolve("Email address").answer == "test.candidate@example.invalid"
    assert engine.resolve("Phone").answer == "+447000000000"
    assert engine.resolve("Unapproved required question").manual_review is True
    # Prove the *true* default (no answer_engine override) is a real,
    # non-synthetic ApplicationAnswerEngine -- without ever constructing it
    # against the production vault. ApplicationAnswerVault's default `path`
    # argument is bound at class-definition time, so the module-level
    # DEFAULT_PATH constant can't be monkeypatched after the fact; instead
    # the bound default on __init__ itself is replaced for this test only.
    monkeypatch.setattr(ApplicationAnswerVault.__init__, "__defaults__", (tmp_path / "default_vault.json",))
    assert not isinstance(ApplicationBrowserService(preview_folder=tmp_path).answer_engine, SyntheticAnswerEngine)

def test_synthetic_page_one_and_two_plans_resolve_only_approved_fields(tmp_path):
    browser=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),preview_folder=tmp_path)
    url="http://127.0.0.1:9999/greenhouse?stage="
    first=browser.preview_html(LocalATS.page_html("1"),url+"1",{"market":"UK"},persist=False)
    assert first.page_purpose == "APPLICATION_FORM"
    assert len(first.fields) == 4
    assert all(field.action == "FILL" for field in first.fields)
    second=browser.preview_html(LocalATS.page_html("2"),url+"2",{"market":"UK"},persist=False)
    assert second.page_purpose == "APPLICATION_FORM"
    assert len(second.fields) == 3
    assert all(field.action == "FILL" for field in second.fields)
    assert [(field.concept,field.answer) for field in second.fields] == [("NOTICE_PERIOD","1 month"),("WORK_AUTHORIZATION_UK","Yes"),("SPONSORSHIP_UK","No")]

def test_missing_required_synthetic_notice_period_and_unknown_question_remain_manual(tmp_path):
    browser=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(include_notice_period=False),preview_folder=tmp_path)
    page=browser.preview_html(LocalATS.page_html("2"),"http://127.0.0.1:9999/greenhouse?stage=2",{"market":"UK"},persist=False)
    assert next(field for field in page.fields if field.concept == "UNKNOWN" or field.label == "Notice period").action == "REVIEW"
    assert browser.answer_engine.resolve("Are you willing to relocate?").manual_review is True

def test_page_three_document_planning_and_optional_cover_letter_behavior(tmp_path):
    resume=_synthetic_pdf(tmp_path/"Test_Candidate_Resume.pdf", "Synthetic Resume")
    cover=_synthetic_pdf(tmp_path/"Test_Candidate_Cover_Letter.pdf", "Synthetic Cover Letter")
    browser=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),preview_folder=tmp_path)
    vacancy={"resume_path":str(resume),"cover_letter_path":str(cover)}
    plan=browser.preview_html(LocalATS.page_html("3"),"http://127.0.0.1:9999/greenhouse?stage=3",vacancy,persist=False)
    assert [(item["kind"],item["required"],item["action"]) for item in plan.document_requirements] == [("RESUME",True,"READY_FOR_UPLOAD"),("COVER_LETTER",False,"READY_FOR_UPLOAD")]
    optional=browser.preview_html(LocalATS.page_html("3"),"http://127.0.0.1:9999/greenhouse?stage=3",{"resume_path":str(resume)},persist=False)
    assert optional.document_requirements[0]["action"] == "READY_FOR_UPLOAD"
    assert optional.document_requirements[1]["action"] == "SKIP"
    missing=browser.preview_html(LocalATS.page_html("3"),"http://127.0.0.1:9999/greenhouse?stage=3",{},persist=False)
    assert missing.document_requirements[0]["action"] == "DOCUMENT_NOT_READY"
    extra=browser.preview_html('<main data-portal="greenhouse"><form><label for="other">Supporting document</label><input id="other" type="file"></form></main>',"http://127.0.0.1:9999/greenhouse",vacancy,persist=False)
    assert extra.document_requirements[0]["kind"] == "SUPPORTING_DOCUMENT"
    assert extra.document_requirements[0]["action"] == "SKIP"

def test_stale_resume_identity_fails_closed_before_browser_upload(tmp_path):
    reviews,_,pkg,execution=setup(tmp_path)
    execution.update({"package_id":pkg.package_id,"application_url":pkg.application_url,"portal":"GREENHOUSE"})
    (tmp_path / "executions" / "exec.json").write_text(json.dumps(execution))
    pkg.resume_vacancy_identity="different-vacancy"
    review=reviews.create(42); review.review_status="APPROVED_FOR_SUBMISSION"; reviews._save(review)
    context=SubmissionContext(review.review_id,42,pkg.package_id,"exec","GREENHOUSE",pkg.application_url,review.fingerprint)
    result=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),package_service=reviews.package_service,review_service=reviews,preview_folder=tmp_path).submit_final_url(context)
    assert result == {"outcome":"SUBMISSION_FAILED","signals":["DOCUMENT_NOT_READY"]}

@pytest.mark.parametrize(("variant","signal"),[("screening_mismatch","MISMATCH_REVIEW_ANSWER"),("document_mismatch","MISMATCH_REVIEW_DOCUMENT"),("ambiguous","FINAL_SUBMIT_AMBIGUOUS"),("missing","FINAL_SUBMIT_NOT_FOUND"),("ordinary","NAVIGATION_UNCERTAIN"),("captcha","CAPTCHA"),("login","LOGIN"),("mfa","MFA"),("account","ACCOUNT_CREATION"),("unexpected_success","UNEXPECTED_APPLICATION_SUCCESS")])
def test_final_review_variants_fail_closed_without_final_click(tmp_path, variant, signal):
    server=LocalATS(variant)
    try:
        browser,context,_,server=_prepared_local_transport(tmp_path,server)
        result=browser.reconcile_final_url(context)
        assert signal in result["signals"]
        assert server.data["submit"] == 0
    finally:
        server.close()

def test_outstanding_manual_confirmation_cannot_become_final_ready(tmp_path):
    server=LocalATS()
    try:
        browser,context,review,server=_prepared_local_transport(tmp_path,server)
        review.pending_manual_actions=["Accuracy certification"]; browser.review_service._save(review)
        result=browser.reconcile_final_url(context)
        assert result["outcome"] == "SUBMISSION_FAILED"
        assert "MANUAL_REVIEW_REQUIRED" in result["signals"]
        assert server.data["submit"] == 0
    finally:
        server.close()

@pytest.mark.parametrize(("variant","expected"),[("matching","SUBMISSION_CONFIRMED"),("failure","SUBMISSION_FAILED"),("uncertain","SUBMISSION_OUTCOME_UNCERTAIN")])
def test_localhost_submission_service_classifies_post_click_outcomes_once(tmp_path, variant, expected):
    server=LocalATS(variant)
    try:
        browser,context,review,server=_prepared_local_transport(tmp_path,server)
        service=ApplicationSubmissionService(review_service=browser.review_service,browser=browser,receipt_dir=tmp_path/"receipts",lock_dir=tmp_path/"locks")
        cancelled=service.submit(review.review_id,"not the exact confirmation")
        assert cancelled.outcome == "SUBMISSION_CANCELLED"
        assert server.data["submit"] == 0
        receipt=service.submit(review.review_id,f"SUBMIT {review.review_id}")
        assert receipt.outcome == expected
        assert server.data["submit"] == 1
        if expected == "SUBMISSION_CONFIRMED":
            assert service.history.get_record_by_id(42)["status"] == "APPLIED"
            second=service.submit(review.review_id,f"SUBMIT {review.review_id}")
            assert second.outcome == "ALREADY_SUBMITTED"
        else:
            assert service.history.get_record_by_id(42)["status"] != "APPLIED"
            if expected == "SUBMISSION_OUTCOME_UNCERTAIN":
                second=service.submit(review.review_id,f"SUBMIT {review.review_id}")
                assert second.outcome == "SUBMISSION_OUTCOME_UNCERTAIN"
        assert server.data["submit"] == 1
        persisted=next((tmp_path/"receipts").glob("*.json")).read_text().lower()
        assert all(word not in persisted for word in ("cookie","csrf","otp","password","validitytoken"))
    finally:
        server.close()

def test_local_greenhouse_transport_rehydrates_injected_stores(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.application_browser_service.APPLICATION_BROWSER_TIMEOUT_MS", 1000)
    server=LocalATS(); url=server.start()
    try:
        reviews,record,pkg,execution=setup(tmp_path)
        pkg.application_url=url; pkg.application_portal="GREENHOUSE"
        resume=_synthetic_pdf(tmp_path/"Test_Candidate_Resume.pdf", "Synthetic Resume")
        cover=_synthetic_pdf(tmp_path/"Test_Candidate_Cover_Letter.pdf", "Synthetic Cover Letter")
        pkg.resume_path=str(resume); pkg.cover_letter_path=str(cover)
        execution["package_id"]=pkg.package_id; execution["application_url"]=url; execution["portal"]="GREENHOUSE"
        (tmp_path / "executions" / "exec.json").write_text(json.dumps(execution))
        # Recreate the final review only after every route-bearing object has
        # its stable localhost identity.
        review=reviews.create(42)
        review.review_status="APPROVED_FOR_SUBMISSION"; reviews._save(review)
        browser=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),package_service=reviews.package_service, review_service=reviews,preview_folder=tmp_path)
        context=SubmissionContext(review.review_id,42,pkg.package_id,"exec","GREENHOUSE",url,review.fingerprint)
        assert (pkg.package_id, review.package_id) == (context.package_id, context.package_id)
        assert (review.tracker_id, review.execution_id, review.fingerprint, review.application_url, review.application_portal) == (context.tracker_id, context.execution_id, context.authorized_fingerprint, context.application_url, context.portal)
        result=browser.reconcile_final_url(context)
        assert result == {"outcome":"FINAL_REVIEW_READY","final_submit_detected":True,"final_submit_clicked":False,"signals":["FINAL_REVIEW_RECONCILED","FINAL_CONTROL_VERIFIED"]}, server.data
        assert server.data["page1_values"] == {"first":"Test","last":"Candidate","email":"test.candidate@example.invalid","phone":"+447000000000"}
        assert server.data["page2_values"] == {"notice":"1 month","authorization":"Yes","sponsorship":"No"}
        assert server.data["visits"][:4] == ["1","2","3","4"]
        assert server.data["uploads"] == {"cv":"Test_Candidate_Resume.pdf","cover":"Test_Candidate_Cover_Letter.pdf"}
        assert server.data["review_clicks"] == 1
        assert server.data["submit"] == 0
        assert server.data["visits"]
    finally: server.close()
