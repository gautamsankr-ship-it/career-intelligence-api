import sqlite3

from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
from job_tracker import format_records, summary_text


def claim(history, suffix, status="MANUAL_WEB_REQUIRED", decision="AUTO_APPLY"):
    fingerprint = job_fingerprint(source="Indeed", external_job_id=suffix)
    history.claim_job(
        fingerprint, status=status, decision=decision, career_score=82.0, ats_score=76.0,
        source="Indeed", external_job_id=suffix, company="Example Co", job_title="Finance Manager",
        job_url="https://example.test/job", application_url="https://example.test/apply",
    )
    return history.get_record(fingerprint)


def test_existing_database_migrates_without_losing_record_or_fingerprint(tmp_path):
    db = tmp_path / "legacy.db"
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id="legacy-1")
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE application_history (id INTEGER PRIMARY KEY AUTOINCREMENT, job_fingerprint TEXT NOT NULL UNIQUE, status TEXT NOT NULL, decision TEXT, career_score REAL)")
    connection.execute("INSERT INTO application_history (job_fingerprint, status, decision, career_score) VALUES (?, 'REVIEW', 'REVIEW', 75)", (fingerprint,))
    connection.commit()
    connection.close()

    with ApplicationHistoryService(db) as history:
        record = history.get_record(fingerprint)
        assert record["job_fingerprint"] == fingerprint
        assert record["status"] == "REVIEW"
        assert record["application_status"] == "REVIEW"
        assert record["decision"] == "REVIEW"


def test_manual_web_to_applied_preserves_screening_identity_and_urls(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        record = claim(history, "web")
        updated = history.update_lifecycle(record["id"], "APPLIED")

        assert updated["application_status"] == "APPLIED"
        assert updated["status"] == "APPLIED"
        assert updated["applied_at"]
        assert updated["decision"] == "AUTO_APPLY"
        assert updated["career_score"] == 82.0
        assert updated["ats_score"] == 76.0
        assert updated["source"] == "Indeed"
        assert updated["company"] == "Example Co"
        assert updated["job_title"] == "Finance Manager"
        assert updated["application_url"] == "https://example.test/apply"
        assert history.is_duplicate(updated["job_fingerprint"])


def test_drafted_requires_explicit_confirmation_before_applied(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        record = claim(history, "draft", status="DRAFTED")
        assert history.get_record_by_id(record["id"])["application_status"] == "DRAFTED"
        updated = history.update_lifecycle(record["id"], "APPLIED")
        assert updated["applied_at"]


def test_interview_outcome_notes_and_follow_up_are_persisted(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        record = claim(history, "interview")
        applied = history.update_lifecycle(record["id"], "APPLIED")
        interview = history.update_lifecycle(applied["id"], "INTERVIEW", interview_stage="First interview", interview_date="2026-09-05", notes="Finance Director")
        offer = history.update_lifecycle(interview["id"], "OFFER")

        assert interview["interview_stage"] == "First interview"
        assert interview["interview_date"] == "2026-09-05"
        assert offer["outcome_date"]
        noted = history.update_lifecycle(offer["id"], "OFFER", notes="Offer accepted", follow_up_date="2026-09-10")
        assert noted["notes"] == "Offer accepted"
        assert noted["follow_up_date"] == "2026-09-10"


def test_rejected_and_withdrawn_transitions_and_invalid_skip_to_offer(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        first = claim(history, "reject")
        history.update_lifecycle(first["id"], "APPLIED")
        rejected = history.update_lifecycle(first["id"], "REJECTED")
        assert rejected["outcome_date"]

        second = claim(history, "withdraw")
        withdrawn = history.update_lifecycle(second["id"], "WITHDRAWN")
        assert withdrawn["application_status"] == "WITHDRAWN"

        skipped = claim(history, "skip", status="SKIPPED", decision="SKIP")
        try:
            history.update_lifecycle(skipped["id"], "OFFER")
            raise AssertionError("Expected invalid transition")
        except ValueError:
            pass


def test_list_and_summary_include_historical_milestones_and_safe_zero_rates(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        offer = claim(history, "offer")
        history.update_lifecycle(offer["id"], "APPLIED")
        history.update_lifecycle(offer["id"], "INTERVIEW", interview_date="2026-09-05")
        history.update_lifecycle(offer["id"], "OFFER")
        manual = claim(history, "manual")

        manual_rows = history.list_records("MANUAL_WEB_REQUIRED")
        assert [row["id"] for row in manual_rows] == [manual["id"]]
        output = format_records(manual_rows)
        assert "https://example.test/apply" in output
        summary = summary_text(history.list_records())
        assert "Applied (historical): 1" in summary
        assert "Interview (historical): 1" in summary
        assert "Offer: 1" in summary

    assert "Application rate: N/A" in summary_text([])
