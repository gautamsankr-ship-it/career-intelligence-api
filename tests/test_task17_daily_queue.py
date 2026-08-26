from datetime import datetime, timezone

from app.models.career_opportunity import CareerOpportunity
from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
from app.services.cache_service import CacheService
from app.services.remote_work_eligibility import RemoteWorkEligibilityClassifier
from job_tracker import format_arrangement_review, format_queue, is_ready_record, pipeline_text, queue_sections, today_text


def claim(history, suffix, *, status="MANUAL_WEB_REQUIRED", decision="AUTO_APPLY", eligibility="ELIGIBLE", score=82, method="WEB", posted_date=""):
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id=suffix)
    history.claim_job(
        fingerprint, status=status, decision=decision, career_score=score, ats_score=75,
        source="LinkedIn", external_job_id=suffix, company=f"Company {suffix}", job_title="Finance Manager",
        market="united_kingdom", work_arrangement="REMOTE", remote_eligibility=eligibility,
        application_method=method, application_url=f"https://example.test/apply/{suffix}", posted_date=posted_date,
        screened_at=datetime.now(timezone.utc).isoformat(), processed_at=datetime.now(timezone.utc).isoformat(),
    )
    return history.get_record(fingerprint)


def test_daily_queue_prioritizes_ready_then_eligibility_then_career_review(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        ready_low = claim(history, "ready-low", score=80)
        ready_high = claim(history, "ready-high", score=90)
        eligibility = claim(history, "eligibility", status="REMOTE_ELIGIBILITY_REVIEW", eligibility="MANUAL_REVIEW", score=95, method=None)
        review = claim(history, "review", status="REVIEW", decision="REVIEW", eligibility=None, score=99, method=None)
        claim(history, "skip", status="SKIPPED", decision="SKIP", eligibility=None, score=60, method=None)
        claim(history, "applied", status="APPLIED", score=100)

        sections = queue_sections(history.list_records())
        assert [record["id"] for record in sections["READY TO APPLY"]] == [ready_high["id"], ready_low["id"]]
        assert [record["id"] for record in sections["REMOTE ELIGIBILITY REVIEW"]] == [eligibility["id"]]
        assert [record["id"] for record in sections["MANUAL CAREER REVIEW"]] == [review["id"]]
        output = format_queue(history.list_records())
        assert output.index("READY TO APPLY") < output.index("REMOTE ELIGIBILITY REVIEW") < output.index("MANUAL CAREER REVIEW")
        assert f"python job_tracker.py applied {ready_high['id']}" in output
        assert not is_ready_record(history.get_record_by_id(eligibility["id"]))


def test_ready_excludes_historical_application_states_and_today_pipeline_use_persisted_dates(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        ready = claim(history, "ready")
        applied = claim(history, "applied", status="APPLIED")
        history.update_lifecycle(applied["id"], "APPLIED")
        interview = claim(history, "interview")
        history.update_lifecycle(interview["id"], "APPLIED")
        history.update_lifecycle(interview["id"], "INTERVIEW", interview_date=datetime.now(timezone.utc).date().isoformat())

        assert [record["id"] for record in history.list_ready_records()] == [ready["id"]]
        today = today_text(history.list_records())
        pipeline = pipeline_text(history.list_records())
        assert "Applied today: 2" in today
        assert "Interviews updated today: 1" in today
        assert "Ready / Manual Web: 1" in pipeline
        assert "Interview (historical): 1" in pipeline


def test_manual_eligibility_and_review_actions_preserve_career_decision_and_audit(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        remote_review = claim(history, "remote", status="REMOTE_ELIGIBILITY_REVIEW", eligibility="MANUAL_REVIEW", score=85, method=None)
        before = history.get_record_by_id(remote_review["id"])
        updated = history.set_manual_eligibility(remote_review["id"], "ELIGIBLE", "Employer confirmed Nepal-based workers are accepted")

        assert updated["remote_eligibility"] == "ELIGIBLE"
        assert updated["remote_eligibility_previous"] == "MANUAL_REVIEW"
        assert updated["remote_eligibility_source"] == "MANUAL"
        assert updated["remote_eligibility_override_note"]
        assert updated["decision"] == before["decision"] == "AUTO_APPLY"
        assert updated["career_score"] == before["career_score"]
        assert updated["status"] == "MANUAL_WEB_REQUIRED" and updated["application_method"] == "WEB"
        history.backfill_remote_eligibility(RemoteWorkEligibilityClassifier())
        assert history.get_record_by_id(remote_review["id"])["remote_eligibility_source"] == "MANUAL"

        review = claim(history, "career", status="REVIEW", decision="REVIEW", eligibility=None, score=75, method=None)
        proceeded = history.set_manual_review_action(review["id"], "PROCEED", "Relevant transferable finance experience")
        assert proceeded["decision"] == "REVIEW" and proceeded["status"] == "REVIEW"
        assert proceeded["manual_review_action"] == "PROCEED"
        skipped = history.set_manual_review_action(review["id"], "SKIP")
        assert skipped["status"] == "SKIPPED" and skipped["decision"] == "REVIEW"


def test_arrangement_review_is_diagnostic_and_does_not_create_tracker_records(tmp_path):
    cache = CacheService()
    cache.cache_dir = tmp_path
    cache.jobs_file = tmp_path / "raw_jobs.json"
    cache.arrangement_review_file = tmp_path / "arrangement_review_jobs.json"
    job = CareerOpportunity(source="LinkedIn", market="united_states", company="Example", job_title="Finance Manager", job_url="https://example.test/job", work_arrangement="UNKNOWN", metadata={"work_arrangement_evidence": "no reliable workplace evidence"})
    cache.save_arrangement_review_jobs([job])
    output = format_arrangement_review(cache.load_arrangement_review_jobs())
    assert "Finance Manager" in output and "UNKNOWN" in output
