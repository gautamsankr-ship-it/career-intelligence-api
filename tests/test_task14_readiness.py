from pathlib import Path
from types import SimpleNamespace

from app.models.career_opportunity import CareerOpportunity
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity, job_fingerprint
from app.services.auto_application_orchestrator import AutoApplicationOrchestrator, format_preview_results
from app.services.remote_work_eligibility import ELIGIBLE, INELIGIBLE, MANUAL_REVIEW
from job_tracker import summary_text


class FakeApplicationService:
    def __init__(self, scores):
        self.scores = scores
        self.documents = 0

    def evaluate_job(self, description):
        score, decision = self.scores[description]
        return SimpleNamespace(
            job_analysis={}, profile={"candidate": {"full_name": "Candidate"}},
            career_decision=SimpleNamespace(overall_score=score),
            ats_result={"ats_score": {"overall_score": 80}}, screening_decision=decision,
        )

    def generate_application_documents(self, _):
        self.documents += 1
        raise AssertionError("preview must not generate documents")


class FakeGmail:
    def __init__(self): self.calls = 0
    def create_draft_for_application(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("preview must not call Gmail")


def vacancy(identifier, description, *, url="https://example.test/apply"):
    return CareerOpportunity(
        id=identifier, source="Indeed", market="united_kingdom", company="Example Co",
        job_title="Finance Manager", job_url=url, application_url=url,
        job_description=description, work_arrangement="REMOTE", remote_status=True,
    )


def test_preview_is_non_mutating_and_produces_operational_readiness_actions(tmp_path):
    web = vacancy("web", "Worldwide remote. Apply through our careers portal.")
    email = vacancy("email", "Worldwide remote. Interested candidates should email their CV to jobs@example.com")
    restricted = vacancy("uk", "Fully remote. Must be based in the UK.")
    unclear = vacancy("unclear", "Fully remote position.")
    low = vacancy("low", "Worldwide remote. Low-score fixture.")
    review = vacancy("review", "Worldwide remote. Review-score fixture.")
    scores = {
        web.job_description: (85, "AUTO_APPLY"), email.job_description: (85, "AUTO_APPLY"),
        restricted.job_description: (90, "AUTO_APPLY"), unclear.job_description: (82, "AUTO_APPLY"),
        low.job_description: (68, "SKIP"), review.job_description: (75, "REVIEW"),
    }
    history = ApplicationHistoryService(tmp_path / "history.db")
    app, gmail = FakeApplicationService(scores), FakeGmail()
    runner = AutoApplicationOrchestrator(application_service=app, gmail_service=gmail, history_service=history)
    try:
        summary = runner.preview([web, email, restricted, unclear, low, review])
        assert [item.recommended_action for item in summary.results] == [
            "READY_FOR_WEB_APPLICATION", "READY_FOR_EMAIL_DRAFT", "BLOCKED_REMOTE_INELIGIBLE",
            "REVIEW_REMOTE_ELIGIBILITY", "SKIP", "MANUAL_CAREER_REVIEW",
        ]
        assert summary.new_jobs_evaluated == 6
        assert history.list_records() == []
        assert app.documents == 0 and gmail.calls == 0
        assert "READY_FOR_EMAIL_DRAFT" in format_preview_results(summary)
    finally:
        history.close()


def test_preview_skips_existing_duplicate_without_blocking_new_preview(tmp_path):
    duplicate = vacancy("duplicate", "Worldwide remote.")
    new = vacancy("new", "Worldwide remote.")
    scores = {duplicate.job_description: (85, "AUTO_APPLY")}
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        history.claim_job(fingerprint_for_opportunity(duplicate), status="MANUAL_WEB_REQUIRED")
        runner = AutoApplicationOrchestrator(application_service=FakeApplicationService(scores), gmail_service=FakeGmail(), history_service=history)
        summary = runner.preview([duplicate, new], limit=1)
        assert summary.duplicates_skipped == 1
        assert summary.new_jobs_evaluated == 1
        assert len(summary.results) == 1


def test_ready_queue_and_backfill_preserve_existing_history_data(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        ready = job_fingerprint(source="Indeed", external_job_id="ready")
        history.claim_job(ready, status="MANUAL_WEB_REQUIRED", decision="AUTO_APPLY", career_score=85,
                          ats_score=80, source="Indeed", external_job_id="ready", company="Ready Co",
                          job_title="Finance Manager", application_method="WEB", application_url="https://example.test/apply",
                          work_arrangement="REMOTE", remote_eligibility=ELIGIBLE)
        applied = job_fingerprint(source="Indeed", external_job_id="applied")
        history.claim_job(applied, status="APPLIED", decision="AUTO_APPLY", application_method="WEB",
                          work_arrangement="REMOTE", remote_eligibility=ELIGIBLE)
        legacy = job_fingerprint(source="LinkedIn", external_job_id="legacy")
        history.claim_job(legacy, status="MANUAL_WEB_REQUIRED", decision="AUTO_APPLY", career_score=82,
                          ats_score=77, source="LinkedIn", external_job_id="legacy", company="Legacy Co",
                          job_title="Financial Controller", application_method="WEB", work_arrangement="REMOTE",
                          job_description="Worldwide remote.")
        before = history.get_record(legacy)
        result = history.backfill_remote_eligibility(__import__("app.services.remote_work_eligibility", fromlist=["RemoteWorkEligibilityClassifier"]).RemoteWorkEligibilityClassifier())
        after = history.get_record(legacy)
        assert {record["job_fingerprint"] for record in history.list_ready_records()} == {ready, legacy}
        assert result["classified"] == 1
        assert after["id"] == before["id"]
        assert after["career_score"] == before["career_score"] and after["ats_score"] == before["ats_score"]
        assert after["decision"] == before["decision"] and after["status"] == before["status"]
        assert after["application_method"] == before["application_method"]
        assert after["remote_eligibility"] == ELIGIBLE
        assert "Application-ready: 2" in summary_text(history.list_records())


def test_backfill_leaves_legacy_record_without_text_unclassified_and_is_idempotent(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id="no-text")
        history.claim_job(fingerprint, status="MANUAL_WEB_REQUIRED", decision="AUTO_APPLY", work_arrangement="REMOTE")
        classifier = __import__("app.services.remote_work_eligibility", fromlist=["RemoteWorkEligibilityClassifier"]).RemoteWorkEligibilityClassifier()
        first = history.backfill_remote_eligibility(classifier)
        second = history.backfill_remote_eligibility(classifier)
        assert first["insufficient_evidence"] == 1 and second["insufficient_evidence"] == 1
        assert history.get_record(fingerprint)["remote_eligibility"] is None
