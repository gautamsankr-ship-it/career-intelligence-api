from types import SimpleNamespace

from app.models.career_opportunity import CareerOpportunity
from app.models.decision_model import CareerDecision
from app.models.employer import Employer
from app.models.recruiter_decision import RecruiterDecision
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity
from app.services.application_service import JobEvaluation
from app.services.auto_application_orchestrator import AutoApplicationOrchestrator
from app.services.preview_evaluation_snapshot import PreviewEvaluationSnapshotStore


def evaluation(score, ats, decision):
    return JobEvaluation(
        profile={"candidate": {"full_name": "Candidate"}, "version": "fixture"},
        job_analysis={"company": "Example Co", "job_title": "Finance Manager", "required_skills": []},
        employer=Employer("Example Co", "Accounting", "Mid", True, 8, 8, 8, 8, 8, [], [], "Good", "Fixture"),
        career_decision=CareerDecision(score, 90, decision, "HIGH", "FULL", [], [], {"improve": []}, {}, {}),
        ats_result={"ats_score": {"overall_score": ats}, "keyword_summary": {"coverage": 1}},
        screening_decision=decision,
        recruiter=RecruiterDecision(),
    )


def vacancy(identifier, description, url="https://example.test/apply"):
    return CareerOpportunity(
        id=identifier, source="Indeed", company="Example Co", job_title="Finance Manager",
        job_url=url, application_url=url, market="united_kingdom", job_description=description,
        work_arrangement="REMOTE", remote_status=True,
    )


class ChangingApplicationService:
    def __init__(self, evaluations):
        self.evaluations = list(evaluations)
        self.calls = 0
        self.documents = 0

    def evaluate_job(self, _description):
        item = self.evaluations[min(self.calls, len(self.evaluations) - 1)]
        self.calls += 1
        return item

    def generate_application_documents(self, _evaluation):
        self.documents += 1
        return SimpleNamespace(docx_path="Resume.docx", cover_letter_docx_path="CoverLetter.docx")


class FakeGmail:
    def __init__(self): self.calls = 0
    def create_draft_for_application(self, history, fingerprint, *_args, **_kwargs):
        self.calls += 1
        history.update_record(fingerprint, status="DRAFTED", gmail_message_id="draft-fixture")
        return "draft-fixture"


def runner(tmp_path, service, gmail=None):
    history = ApplicationHistoryService(tmp_path / "history.db")
    snapshots = PreviewEvaluationSnapshotStore(tmp_path / "preview.json")
    return AutoApplicationOrchestrator(
        application_service=service, history_service=history, gmail_service=gmail or FakeGmail(),
        preview_snapshot_store=snapshots,
    ), history


def test_preview_snapshot_reuses_score_ats_and_threshold_crossing_decision(tmp_path):
    job = vacancy("threshold", "Worldwide remote. Apply through our careers portal.")
    service = ChangingApplicationService([evaluation(80.7, 88, "AUTO_APPLY"), evaluation(74, 60, "REVIEW")])
    worker, history = runner(tmp_path, service)
    try:
        preview = worker.preview([job])
        processed = worker.run([job])
        record = history.get_record(fingerprint_for_opportunity(job))
        assert preview.snapshots_saved == 1
        assert service.calls == 1
        assert processed.preview_snapshots_reused == 1
        assert processed.results[0].evaluation_source == "PREVIEW_SNAPSHOT"
        assert record["career_score"] == 80.7 and record["ats_score"] == 88
        assert record["decision"] == "AUTO_APPLY" and record["status"] == "MANUAL_WEB_REQUIRED"
        assert worker.preview_snapshots.get(job) is None
    finally:
        history.close()


def test_skip_snapshot_is_reused_and_normal_processing_without_snapshot_is_fresh(tmp_path):
    job = vacancy("skip", "Worldwide remote.")
    service = ChangingApplicationService([evaluation(63, 50, "SKIP"), evaluation(90, 90, "AUTO_APPLY")])
    worker, history = runner(tmp_path, service)
    try:
        worker.preview([job])
        summary = worker.run([job])
        assert service.calls == 1 and summary.results[0].career_score == 63
        fresh = vacancy("fresh", "Worldwide remote. Another role.")
        fresh_summary = worker.run([fresh])
        assert service.calls == 2 and fresh_summary.results[0].evaluation_source == "FRESH"
    finally:
        history.close()


def test_changed_description_rejects_snapshot_and_duplicate_history_wins(tmp_path):
    job = vacancy("changed", "Worldwide remote. Apply through our careers portal.")
    service = ChangingApplicationService([evaluation(81.5, 88, "AUTO_APPLY"), evaluation(72, 60, "REVIEW")])
    worker, history = runner(tmp_path, service)
    try:
        worker.preview([job])
        job.job_description = "Worldwide remote. Materially revised responsibilities."
        result = worker.run([job])
        assert service.calls == 2 and result.results[0].evaluation_source == "FRESH"

        duplicate = vacancy("duplicate", "Worldwide remote. Apply through our careers portal.")
        worker.preview([duplicate])
        history.claim_job(fingerprint_for_opportunity(duplicate), status="MANUAL_WEB_REQUIRED")
        before = service.calls
        duplicate_result = worker.run([duplicate])
        assert duplicate_result.duplicates_skipped == 1 and service.calls == before
        assert service.documents == 0 and worker.gmail.calls == 0
    finally:
        history.close()


def test_email_snapshot_reuses_evaluation_and_drafts_once_with_ats_diagnostic_only(tmp_path):
    job = vacancy("email", "Worldwide remote. Interested candidates should email their CV to jobs@example.com")
    service = ChangingApplicationService([evaluation(81.5, 0, "AUTO_APPLY"), evaluation(72, 99, "REVIEW")])
    gmail = FakeGmail()
    worker, history = runner(tmp_path, service, gmail)
    try:
        worker.preview([job])
        summary = worker.run([job])
        record = history.get_record(fingerprint_for_opportunity(job))
        assert service.calls == 1
        assert service.documents == 1 and gmail.calls == 1
        assert summary.gmail_drafts_created == 1
        assert record["decision"] == "AUTO_APPLY" and record["ats_score"] == 0
        assert record["status"] == "DRAFTED"
    finally:
        history.close()
