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
