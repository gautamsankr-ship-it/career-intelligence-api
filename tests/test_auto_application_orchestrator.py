from pathlib import Path
from types import SimpleNamespace

from app.models.career_opportunity import CareerOpportunity
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity
from app.services.auto_application_orchestrator import (
    AutoApplicationOrchestrator,
    AutoApplyJobResult,
    AutoApplyRunSummary,
    format_run_results,
)


def job(identifier, description, url="https://example.com/jobs/1"):
    return CareerOpportunity(
        id=identifier,
        source="LinkedIn",
        job_url=url,
        company="Example Co",
        job_title="Financial Analyst",
        location="Remote",
        job_description=description,
    )


class FakeApplicationService:
    def __init__(self, scores, tmp_path):
        self.scores = scores
        self.tmp_path = Path(tmp_path)
        self.evaluate_calls = []
        self.document_calls = []

    def evaluate_job(self, description):
        self.evaluate_calls.append(description)
        score, decision = self.scores[description]
        return SimpleNamespace(
            profile={"candidate": {"full_name": "Test Candidate"}},
            job_analysis={},
            career_decision=SimpleNamespace(overall_score=score),
            ats_result={"ats_score": {"overall_score": 81.0}},
            screening_decision=decision,
        )

    def generate_application_documents(self, evaluation):
        self.document_calls.append(evaluation)
        resume_markdown = self.tmp_path / f"Resume-{len(self.document_calls)}.md"
        resume = self.tmp_path / f"Resume-{len(self.document_calls)}.docx"
        cover_markdown = self.tmp_path / f"Cover-{len(self.document_calls)}.md"
        cover = self.tmp_path / f"Cover-{len(self.document_calls)}.docx"
        resume_markdown.write_text("# Resume", encoding="utf-8")
        resume.write_bytes(b"resume")
        cover_markdown.write_text("# Cover Letter", encoding="utf-8")
        cover.write_bytes(b"cover")
        return SimpleNamespace(
            markdown_path=str(resume_markdown),
            docx_path=str(resume),
            cover_letter_markdown_path=str(cover_markdown),
            cover_letter_docx_path=str(cover),
        )


class FakeGmailService:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def create_draft_for_application(self, history, fingerprint, recipient, subject, body, attachments):
        self.calls.append((recipient, subject, body, attachments))
        if self.error:
            raise self.error
        history.update_record(fingerprint, status="DRAFTED", gmail_message_id="draft-123")
        return "draft-123"


class FakeDiscoveryService:
    def __init__(self, jobs):
        self.jobs = jobs
        self.requested_limit = "not called"

    def discover_jobs(self, limit=None):
        self.requested_limit = limit
        return self.jobs


def orchestrator(tmp_path, scores, gmail=None, discovery=None):
    history = ApplicationHistoryService(tmp_path / "history.db")
    app = FakeApplicationService(scores, tmp_path)
    return (
        AutoApplicationOrchestrator(
            discovery_service=discovery,
            application_service=app,
            history_service=history,
            gmail_service=gmail or FakeGmailService(),
        ),
        app,
        history,
    )


def cached_jobs_with_duplicates(history):
    jobs = [job(f"job-{index}", f"score {index}") for index in range(14)]
    for opportunity in jobs[:5]:
        history.claim_job(fingerprint_for_opportunity(opportunity), status="SKIPPED")
    scores = {opportunity.job_description: (69, "SKIP") for opportunity in jobs}
    return jobs, scores


def test_score_69_is_skipped_without_documents_or_gmail(tmp_path):
    opportunity = job("69", "low score")
    runner, app, history = orchestrator(tmp_path, {"low score": (69, "SKIP")})
    try:
        summary = runner.run([opportunity])
        record = history.get_record(fingerprint_for_opportunity(opportunity))
        assert summary.skipped == 1
        assert summary.results[0].status == "SKIPPED"
        assert summary.results[0].career_score == 69
        assert record["status"] == "SKIPPED"
        assert app.document_calls == []
        assert runner.gmail.calls == []
    finally:
        history.close()


def test_result_formatting_uses_recorded_job_outcomes_only():
    summary = AutoApplyRunSummary(
        results=[
            AutoApplyJobResult(
                company="ABC Ltd",
                job_title="Finance Manager",
                career_score=82.0,
                ats_score=79.0,
                decision="AUTO_APPLY",
                application_method="WEB",
                status="MANUAL_WEB_REQUIRED",
                job_url="https://example.com/jobs/abc",
            ),
            AutoApplyJobResult(
                company="XYZ Ltd",
                job_title="Senior Accountant",
                career_score=74.0,
                ats_score=None,
                decision="REVIEW",
                application_method=None,
                status="REVIEW",
                job_url=None,
            ),
        ]
    )

    output = format_run_results(summary)

    assert "AUTO-APPLICATION RESULTS" in output
    assert "1. ABC Ltd | Finance Manager" in output
    assert "Career Score: 82" in output
    assert "ATS Score: 79" in output
    assert "Decision: AUTO_APPLY" in output
    assert "Route: WEB" in output
    assert "Status: MANUAL_WEB_REQUIRED" in output
    assert "URL: https://example.com/jobs/abc" in output
    assert "2. XYZ Ltd | Senior Accountant" in output
    assert "Decision: REVIEW" in output
    assert "Route: -" in output
    assert "ATS Score:" not in output.split("2. XYZ Ltd", 1)[1]


def test_score_75_is_review_without_gmail(tmp_path):
    opportunity = job("75", "review score")
    runner, app, history = orchestrator(tmp_path, {"review score": (75, "REVIEW")})
    try:
        summary = runner.run([opportunity])
        assert summary.review == 1
        assert history.get_record(fingerprint_for_opportunity(opportunity))["status"] == "REVIEW"
        assert app.document_calls == []
        assert runner.gmail.calls == []
    finally:
        history.close()


def test_explicit_email_creates_draft_and_persists_gmail_id(tmp_path):
    opportunity = job(
        "78",
        """Senior Finance Manager
Company: Example Test Company

Requirements:
- financial planning
- budgeting
- forecasting
- IFRS
- leadership

Application instruction:
Worldwide remote. Interested candidates should email their CV to test-recipient@example.com""",
    )
    opportunity.work_arrangement = "REMOTE"
    opportunity.remote_status = True
    runner, app, history = orchestrator(
        tmp_path, {opportunity.job_description: (78, "AUTO_APPLY")}
    )
    try:
        summary = runner.run([opportunity])
        record = history.get_record(fingerprint_for_opportunity(opportunity))
        assert summary.auto_apply_eligible == 1
        assert summary.remote_eligible == 1
        assert summary.gmail_drafts_created == 1
        assert len(app.document_calls) == 1
        assert runner.gmail.calls[0][0] == "test-recipient@example.com"
        attachments = runner.gmail.calls[0][3]
        assert len(attachments) == 2
        assert Path(attachments[0]).is_file()
        assert Path(attachments[1]).is_file()
        assert record["status"] == "DRAFTED"
        assert record["gmail_message_id"] == "draft-123"
        assert record["application_method"] == "EMAIL"
        assert record["recipient_email"] == "test-recipient@example.com"
    finally:
        history.close()


def test_web_only_eligible_role_requires_manual_web_without_gmail(tmp_path):
    opportunity = job("90-web", "Worldwide remote. Apply through our careers portal")
    opportunity.work_arrangement = "REMOTE"
    opportunity.remote_status = True
    opportunity.application_url = "https://example.com/apply/90-web"
    runner, app, history = orchestrator(
        tmp_path, {opportunity.job_description: (90, "AUTO_APPLY")}
    )
    try:
        summary = runner.run([opportunity])
        record = history.get_record(fingerprint_for_opportunity(opportunity))
        assert summary.manual_web_required == 1
        assert summary.remote_eligible == 1
        assert record["status"] == "MANUAL_WEB_REQUIRED"
        assert record["application_method"] == "WEB"
        rendered = format_run_results(summary)
        assert "Tracker ID:" in rendered
        assert "https://example.com/apply/90-web" in rendered
        assert "python job_tracker.py applied" in rendered
        assert app.document_calls == []
        assert runner.gmail.calls == []
    finally:
        history.close()


def test_contact_only_address_is_not_emailed(tmp_path):
    opportunity = job("contact", "For questions contact recruiter@example.com", url="")
    runner, app, history = orchestrator(
        tmp_path, {opportunity.job_description: (80, "AUTO_APPLY")}
    )
    try:
        summary = runner.run([opportunity])
        assert summary.review == 1
        assert history.get_record(fingerprint_for_opportunity(opportunity))["status"] == "REVIEW"
        assert app.document_calls == []
        assert runner.gmail.calls == []
    finally:
        history.close()


def test_duplicate_is_skipped_before_new_draft_on_second_execution(tmp_path):
    opportunity = job("duplicate", "Send your CV to jobs@example.com")
    runner, app, history = orchestrator(
        tmp_path, {opportunity.job_description: (78, "AUTO_APPLY")}
    )
    try:
        first = runner.run([opportunity])
        second = runner.run([opportunity])
        assert first.gmail_drafts_created == 1
        assert second.duplicates_skipped == 1
        assert len(app.document_calls) == 1
        assert len(runner.gmail.calls) == 1
    finally:
        history.close()


def test_gmail_failure_is_recorded_and_batch_continues(tmp_path):
    email_job = job("failure", "Email your resume to jobs@example.com")
    skip_job = job("after-failure", "low score")
    gmail = FakeGmailService(error=RuntimeError("mock Gmail failure"))
    runner, app, history = orchestrator(
        tmp_path,
        {email_job.job_description: (78, "AUTO_APPLY"), skip_job.job_description: (69, "SKIP")},
        gmail,
    )
    try:
        summary = runner.run([email_job, skip_job])
        failed = history.get_record(fingerprint_for_opportunity(email_job))
        assert summary.failed == 1
        assert summary.skipped == 1
        assert failed["status"] == "FAILED"
        assert "mock Gmail failure" in failed["error_message"]
        assert len(app.document_calls) == 1
    finally:
        history.close()


def test_limit_14_scans_past_five_duplicates_and_evaluates_remaining_nine(tmp_path):
    bootstrap = ApplicationHistoryService(tmp_path / "history.db")
    jobs, scores = cached_jobs_with_duplicates(bootstrap)
    bootstrap.close()
    discovery = FakeDiscoveryService(jobs)
    runner, app, history = orchestrator(tmp_path, scores, discovery=discovery)
    try:
        summary = runner.run(limit=14)
        assert discovery.requested_limit is None
        assert summary.cached_jobs_available == 14
        assert summary.jobs_scanned == 14
        assert summary.duplicates_skipped == 5
        assert summary.new_jobs_evaluated == 9
        assert summary.skipped == 9
        assert len(app.evaluate_calls) == 9
        assert app.document_calls == []
        assert runner.gmail.calls == []
    finally:
        history.close()


def test_limit_one_scans_past_duplicates_until_one_new_job_is_evaluated(tmp_path):
    bootstrap = ApplicationHistoryService(tmp_path / "history.db")
    jobs, scores = cached_jobs_with_duplicates(bootstrap)
    bootstrap.close()
    runner, app, history = orchestrator(tmp_path, scores)
    try:
        summary = runner.run(jobs, limit=1)
        assert summary.cached_jobs_available == 14
        assert summary.jobs_scanned == 6
        assert summary.duplicates_skipped == 5
        assert summary.new_jobs_evaluated == 1
        assert len(app.evaluate_calls) == 1
    finally:
        history.close()


def test_limit_five_evaluates_up_to_five_non_duplicate_jobs(tmp_path):
    bootstrap = ApplicationHistoryService(tmp_path / "history.db")
    jobs, scores = cached_jobs_with_duplicates(bootstrap)
    bootstrap.close()
    runner, app, history = orchestrator(tmp_path, scores)
    try:
        summary = runner.run(jobs, limit=5)
        assert summary.jobs_scanned == 10
        assert summary.duplicates_skipped == 5
        assert summary.new_jobs_evaluated == 5
        assert len(app.evaluate_calls) == 5
    finally:
        history.close()
