from app.services.application_history_service import (
    ApplicationHistoryService,
    job_fingerprint,
)


def test_external_job_id_fingerprint_is_stable():
    first = job_fingerprint(source="LinkedIn", external_job_id="ABC-123")
    second = job_fingerprint(source="linkedin", external_job_id=" abc-123 ")
    assert first == second


def test_normalized_url_fingerprint_is_stable():
    first = job_fingerprint(job_url="HTTPS://Example.com/jobs/123/?b=2&a=1#details")
    second = job_fingerprint(job_url="https://example.com/jobs/123?a=1&b=2")
    assert first == second


def test_fallback_fingerprint_is_stable():
    first = job_fingerprint(
        company="Example Co",
        job_title="Financial Analyst",
        location="Sydney",
        description="Analyse reports and support forecasting.",
    )
    second = job_fingerprint(
        company=" example  co ",
        job_title="Financial   Analyst",
        location="Sydney",
        description="  Analyse reports and support forecasting.  ",
    )
    assert first == second


def test_first_claim_is_accepted_and_second_is_duplicate(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id="1")
        assert history.claim_job(fingerprint, status="REVIEW") is True
        assert history.claim_job(fingerprint, status="ELIGIBLE") is False
        assert history.is_duplicate(fingerprint) is True


def test_screening_statuses_are_persisted(tmp_path):
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        for index, status in enumerate(("SKIPPED", "REVIEW", "ELIGIBLE")):
            fingerprint = job_fingerprint(
                source="LinkedIn", external_job_id=f"job-{index}"
            )
            assert history.claim_job(
                fingerprint,
                status=status,
                decision={
                    "SKIPPED": "SKIP",
                    "REVIEW": "REVIEW",
                    "ELIGIBLE": "AUTO_APPLY",
                }[status],
            ) is True
            assert history.get_record(fingerprint)["status"] == status


def test_intelligence_rejected_is_a_valid_status(tmp_path):
    """Task 21.14E: a new, distinct status for a JobIntelligence
    Priority.REJECT outcome not already covered by REMOTE_INELIGIBLE (e.g.
    an invalid/stale vacancy or a proven hard requirement gap) -- kept
    separate from the existing post-application "REJECTED" outcome status."""
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id="rejected-1")
        assert history.claim_job(fingerprint, status="INTELLIGENCE_REJECTED") is True
        assert history.get_record(fingerprint)["status"] == "INTELLIGENCE_REJECTED"


def test_list_ready_records_prefers_intelligence_priority_when_present(tmp_path):
    """Task 21.14E: intelligence_priority, once persisted, is authoritative
    for readiness -- overriding what the legacy decision/remote_eligibility
    fields alone would have implied, in both directions."""
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        ready_fp = job_fingerprint(source="LinkedIn", external_job_id="ready-1")
        history.claim_job(
            ready_fp, status="ELIGIBLE", decision="SKIP", remote_eligibility="INELIGIBLE",
            application_method="WEB",
        )
        history.update_record(ready_fp, intelligence_priority="A")

        not_ready_fp = job_fingerprint(source="LinkedIn", external_job_id="not-ready-1")
        history.claim_job(
            not_ready_fp, status="ELIGIBLE", decision="AUTO_APPLY", remote_eligibility="ELIGIBLE",
            application_method="WEB",
        )
        history.update_record(not_ready_fp, intelligence_priority="C")

        ready_ids = {record["job_fingerprint"] for record in history.list_ready_records()}
        assert ready_fp in ready_ids
        assert not_ready_fp not in ready_ids


def test_list_ready_records_falls_back_to_legacy_fields_when_intelligence_priority_absent(tmp_path):
    """Records persisted before Task 21.14E (no intelligence_priority at
    all) keep working via the original decision/remote_eligibility check --
    including the Task 21.14B NOT_APPLICABLE fix."""
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id="legacy-1")
        history.claim_job(
            fingerprint, status="ELIGIBLE", decision="AUTO_APPLY", remote_eligibility="NOT_APPLICABLE",
            application_method="EMAIL",
        )
        ready_ids = {record["job_fingerprint"] for record in history.list_ready_records()}
        assert fingerprint in ready_ids


def test_history_survives_close_and_reopen(tmp_path):
    database = tmp_path / "history.db"
    fingerprint = job_fingerprint(job_url="https://example.com/jobs/1")

    history = ApplicationHistoryService(database)
    history.claim_job(fingerprint, status="SKIPPED", career_score=69.0)
    history.close()

    reopened = ApplicationHistoryService(database)
    try:
        record = reopened.get_record(fingerprint)
        assert record["status"] == "SKIPPED"
        assert record["career_score"] == 69.0
        assert reopened.is_duplicate(fingerprint) is True
    finally:
        reopened.close()
