"""Real-Chromium end-to-end Greenhouse iframe application progression (Task 21.8C.3).

Proves Tasks 21.8C.1 (Page|Frame pipeline) and 21.8C.2 (trusted Greenhouse
iframe selection) work together through the existing execution orchestrator to
progress a synthetic multi-page Greenhouse application -- embedded as a real
cross-origin iframe -- up to (never through) final review.

Localhost/synthetic only. The final submit control is detected but this
module never clicks it, never calls the submission transport, and never
marks a tracker record APPLIED.
"""
from app.config import APPLICATION_AUTO_SUBMIT, APPLICATION_DRY_RUN, GMAIL_AUTO_SEND, GMAIL_DRY_RUN
from app.models.application_package import ApplicationPackage
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_execution_orchestrator import ApplicationExecutionOrchestrator
from helpers.local_ats_server import LocalATS, LocalGreenhouseIframeEmployer
from helpers.synthetic_answer_engine import SyntheticAnswerEngine


class _History:
    def __init__(self, record): self.record=dict(record)
    def get_record_by_id(self, identifier): return self.record if identifier == self.record["id"] else None


class _Packages:
    """Minimal package_service double satisfying ApplicationExecutionOrchestrator."""
    def __init__(self, record, package): self.history=_History(record); self.package=package
    def load(self, identifier): return self.package if identifier == self.package.tracker_id else None
    def _identity(self, record): return "identity"
    def _save(self, package): self.package=package; return package
    def ready(self): return [self.package]


def _pdf(path, label):
    path.write_bytes(("%PDF-1.4\nstream\n"+label+"\nendstream\n%%EOF\n").encode())
    return path


def _setup(tmp_path):
    ats=LocalATS(); ats_url=ats.start()
    employer=LocalGreenhouseIframeEmployer(ats_url); wrapper_url=employer.start()
    resume=_pdf(tmp_path/"Synthetic_Candidate_Resume.pdf", "Synthetic Resume")
    cover=_pdf(tmp_path/"Synthetic_Candidate_Cover_Letter.pdf", "Synthetic Cover Letter")
    record={"id":42,"job_fingerprint":"f","company":"Example","job_title":"Finance Manager","job_description":"finance",
            "decision":"AUTO_APPLY","remote_eligibility":"ELIGIBLE","status":"MANUAL_WEB_REQUIRED","application_status":"MANUAL_WEB_REQUIRED",
            "application_url":wrapper_url,"source_listing_url":wrapper_url}
    package=ApplicationPackage("pkg-iframe-e2e",42,company="Example",job_title="Finance Manager",market="united_kingdom",
                                application_url=wrapper_url,application_portal="GREENHOUSE",route_confidence="HIGH",
                                resume_path=str(resume),resume_status="READY",resume_vacancy_identity="identity",
                                cover_letter_path=str(cover),cover_letter_status="READY",answer_vault_status="ANSWER_VAULT_READY",
                                portal_capability="FULL_PREPARATION_SUPPORTED",readiness="READY_FOR_BROWSER_PREPARATION",vacancy_identity="identity")
    packages=_Packages(record, package)
    browser=ApplicationBrowserService(answer_engine=SyntheticAnswerEngine(), preview_folder=tmp_path/"previews")
    orchestrator=ApplicationExecutionOrchestrator(packages, browser, tmp_path/"executions")
    return ats, employer, packages, package, orchestrator, wrapper_url, resume, cover


def test_a_full_greenhouse_iframe_happy_path_reaches_final_review(tmp_path):
    ats, employer, packages, package, orchestrator, wrapper_url, _, _ = _setup(tmp_path)
    try:
        result = orchestrator.execute(42, "PROGRESS")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW"
        assert result.portal == "GREENHOUSE"
        assert result.pages_processed == 4  # Page 1, Page 2, Page 3, Review
        assert result.navigation_actions == 3  # Continue, Continue, Review
        assert result.fields_detected > 0 and result.fields_resolved > 0 and result.fields_filled > 0
        assert result.unknown_required_fields == 0 and result.manual_review_fields == 0
        assert result.resume_uploaded and result.cover_letter_uploaded
        assert result.final_submit_detected
        assert not result.captcha_detected and not result.auth_required and not result.mfa_required
        assert ats.data["visits"][:4] == ["1", "2", "3", "4"]
        assert ats.data["review_clicks"] == 1
    finally:
        employer.close(); ats.close()


def test_b_actual_field_values_were_entered_in_the_real_frame(tmp_path):
    ats, employer, packages, package, orchestrator, wrapper_url, _, _ = _setup(tmp_path)
    try:
        result = orchestrator.execute(42, "PROGRESS")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW"
        # Server-observed values from real HTTP requests issued by the real
        # browser inside the selected Frame -- not internal planning objects.
        assert ats.data["page1_values"] == {"first":"Test","last":"Candidate","email":"test.candidate@example.invalid","phone":"+447000000000"}
        assert ats.data["page2_values"] == {"notice":"1 month","authorization":"Yes","sponsorship":"No"}
    finally:
        employer.close(); ats.close()


def test_c_actual_resume_and_cover_letter_files_were_selected(tmp_path):
    ats, employer, packages, package, orchestrator, wrapper_url, resume, cover = _setup(tmp_path)
    try:
        result = orchestrator.execute(42, "PROGRESS")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW"
        assert ats.data["uploads"] == {"cv": resume.name, "cover": cover.name}
    finally:
        employer.close(); ats.close()


def test_d_wrapper_remains_the_stable_authoritative_route(tmp_path):
    ats, employer, packages, package, orchestrator, wrapper_url, _, _ = _setup(tmp_path)
    try:
        result = orchestrator.execute(42, "PROGRESS")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW"
        # The package's stable route is never replaced by a temporary iframe
        # or token URL -- it is untouched by progression.
        assert package.application_url == wrapper_url
        assert packages.history.record["application_url"] == wrapper_url
    finally:
        employer.close(); ats.close()


def test_e_unrelated_iframe_is_never_selected_filled_or_renavigated(tmp_path):
    ats, employer, packages, package, orchestrator, wrapper_url, _, _ = _setup(tmp_path)
    try:
        result = orchestrator.execute(42, "PROGRESS")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW"
        # The unrelated iframe is unavoidably loaded once when the wrapper
        # renders, and never touched again -- all progression happened on
        # the trusted Greenhouse iframe (visible via the ATS server's own
        # stage visits), not the unrelated one.
        assert employer.data["visits"].count("/ad") == 1
        assert employer.data["visits"].count("/careers") == 1
        assert ats.data["visits"][:4] == ["1", "2", "3", "4"]
    finally:
        employer.close(); ats.close()


def test_f_final_submit_control_is_never_clicked(tmp_path):
    ats, employer, packages, package, orchestrator, wrapper_url, _, _ = _setup(tmp_path)
    try:
        result = orchestrator.execute(42, "PROGRESS")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW"
        assert result.final_submit_detected is True
        # The synthetic ATS server's own submit counter -- incremented only
        # when the "success" stage is requested by a real submit click.
        assert ats.data["submit"] == 0
    finally:
        employer.close(); ats.close()


def test_g_tracker_record_is_never_marked_applied(tmp_path):
    ats, employer, packages, package, orchestrator, wrapper_url, _, _ = _setup(tmp_path)
    try:
        result = orchestrator.execute(42, "PROGRESS")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW"
        assert packages.history.record["status"] != "APPLIED"
        assert packages.history.record["application_status"] != "APPLIED"
    finally:
        employer.close(); ats.close()


def test_h_no_gmail_operation_occurs(tmp_path):
    assert GMAIL_DRY_RUN is True and GMAIL_AUTO_SEND is False
    ats, employer, packages, package, orchestrator, wrapper_url, _, _ = _setup(tmp_path)
    try:
        result = orchestrator.execute(42, "PROGRESS")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW"
        # No audit entry in this execution's own record references Gmail --
        # the orchestrator/browser path exercised here never touches it.
        assert not any("gmail" in str(entry).lower() for entry in result.audit)
        assert APPLICATION_DRY_RUN is True and APPLICATION_AUTO_SUBMIT is False
    finally:
        employer.close(); ats.close()
