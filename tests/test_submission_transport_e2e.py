"""Deterministic transport contract fixtures; no network or real ATS is used."""
from types import SimpleNamespace
from app.models.submission import SubmissionContext
from app.services.application_browser_service import ApplicationBrowserService
from helpers.synthetic_answer_engine import SyntheticAnswerEngine

def test_submission_transport_requires_typed_context(tmp_path):
    service=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),preview_folder=tmp_path)
    result=service.submit_final_url(object())
    assert result == {"outcome":"SUBMISSION_FAILED","signals":["SUBMISSION_PORTAL_UNSUPPORTED"]}

def test_unsupported_portal_cannot_reach_final_click_transport(tmp_path):
    context=SubmissionContext("r",1,"p","e","WORKDAY","http://127.0.0.1/workday","f")
    result=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(),preview_folder=tmp_path).submit_final_url(context)
    assert result["outcome"]=="SUBMISSION_FAILED"
    assert result["signals"]==["SUBMISSION_PORTAL_UNSUPPORTED"]

def test_submission_identity_comparison_reports_only_safe_mismatch_names():
    context=SubmissionContext("r",1,"p","e","GREENHOUSE","http://127.0.0.1/app","fingerprint")
    package=SimpleNamespace(tracker_id=1,package_id="p",application_url=context.application_url,application_portal="GREENHOUSE")
    review=SimpleNamespace(tracker_id=1,package_id="p",execution_id="e",fingerprint="fingerprint",application_url=context.application_url,application_portal="GREENHOUSE")
    execution={"tracker_id":1,"package_id":"p","execution_id":"e","application_url":context.application_url,"portal":"GREENHOUSE"}

    assert ApplicationBrowserService._compare_submission_identity(context,package,review,execution)=={"matches":True,"mismatches":[]}

    package.package_id="other"
    assert "MISMATCH_PACKAGE_ID" in ApplicationBrowserService._compare_submission_identity(context,package,review,execution)["mismatches"]
    package.package_id="p"; review.execution_id="other"
    assert "MISMATCH_EXECUTION_ID" in ApplicationBrowserService._compare_submission_identity(context,package,review,execution)["mismatches"]
    review.execution_id="e"; review.fingerprint="other"
    assert "MISMATCH_FINGERPRINT" in ApplicationBrowserService._compare_submission_identity(context,package,review,execution)["mismatches"]
    review.fingerprint="fingerprint"; package.application_url="http://127.0.0.1/other"; review.application_portal="LEVER"
    mismatches=ApplicationBrowserService._compare_submission_identity(context,package,review,execution)["mismatches"]
    assert "MISMATCH_APPLICATION_URL" in mismatches
    assert "MISMATCH_REVIEW_PORTAL" in mismatches

def test_stale_rehydrated_identity_fails_closed_before_browser_launch(tmp_path):
    context=SubmissionContext("r",1,"p","e","GREENHOUSE","http://127.0.0.1/app","fingerprint")
    package=SimpleNamespace(tracker_id=1,package_id="p",application_url="http://127.0.0.1/changed",application_portal="GREENHOUSE")
    review=SimpleNamespace(tracker_id=1,package_id="p",execution_id="e",fingerprint="fingerprint",application_url=context.application_url,application_portal="GREENHOUSE")
    execution={"tracker_id":1,"package_id":"p","execution_id":"e","application_url":context.application_url,"portal":"GREENHOUSE"}

    class Packages:
        history=SimpleNamespace(close=lambda: None)
        def load(self, tracker_id): return package
    class Reviews:
        def show(self, review_id): return review
        def _execution(self, execution_id): return execution

    result=ApplicationBrowserService(package_service=Packages(),review_service=Reviews(),answer_engine=SyntheticAnswerEngine(),preview_folder=tmp_path).submit_final_url(context)
    assert result=={"outcome":"SUBMISSION_FAILED","signals":["APPLICATION_CHANGED_AFTER_REVIEW","MISMATCH_APPLICATION_URL"]}
