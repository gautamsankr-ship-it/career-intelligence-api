import pytest

from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
from app.services.opportunity_crm_service import OpportunityCRMService


def crm(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    return history, OpportunityCRMService(history)


def _create(service, external_id="job-1", **fields):
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id=external_id)
    return service.create_opportunity(fingerprint, company="Acme", job_title="Finance Manager", **fields)


def test_opportunity_creation_seeds_crm_stage_and_event(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    assert record["crm_stage"] == "DISCOVERED"
    timeline = service.get_timeline(record["id"])
    assert any(e["detail"]["event_type"] == "OPPORTUNITY_CREATED" for e in timeline)


def test_deduplication_returns_none_for_existing_fingerprint(tmp_path):
    _, service = crm(tmp_path)
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id="dup-1")
    first = service.create_opportunity(fingerprint, company="Acme", job_title="Finance Manager")
    second = service.create_opportunity(fingerprint, company="Acme", job_title="Finance Manager")
    assert first is not None
    assert second is None


def test_valid_lifecycle_transitions_advance_stage(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    tracker_id = record["id"]
    for stage in ("VERIFIED", "ELIGIBILITY_REVIEW", "ELIGIBLE", "SCORED", "SHORTLISTED"):
        updated = service.transition_stage(tracker_id, stage, reason="progressing")
        assert updated["crm_stage"] == stage


def test_skip_ahead_transition_is_allowed(tmp_path):
    """Do not force every opportunity through every stage."""
    _, service = crm(tmp_path)
    record = _create(service)
    updated = service.transition_stage(record["id"], "SHORTLISTED", reason="fast-tracked")
    assert updated["crm_stage"] == "SHORTLISTED"


def test_invalid_backward_transition_is_rejected(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.transition_stage(record["id"], "SHORTLISTED")
    with pytest.raises(ValueError):
        service.transition_stage(record["id"], "DISCOVERED")


def test_terminal_stage_has_no_outgoing_transition(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    service.record_rejection(record["id"], rejection_reason="Not a fit")
    with pytest.raises(ValueError):
        service.transition_stage(record["id"], "SHORTLISTED")


def test_hired_cannot_precede_offer_or_accepted(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    with pytest.raises(ValueError):
        service.record_hire(record["id"])
    service.transition_stage(record["id"], "SHORTLISTED")
    with pytest.raises(ValueError):
        service.transition_stage(record["id"], "HIRED")


def test_event_history_is_immutable_and_accumulates(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.transition_stage(record["id"], "VERIFIED")
    service.transition_stage(record["id"], "ELIGIBLE")
    events = [e for e in service.get_timeline(record["id"]) if e["kind"] == "EVENT"]
    stages_seen = [e["detail"]["new_stage"] for e in events]
    assert stages_seen == ["DISCOVERED", "VERIFIED", "ELIGIBLE"]
    # Every event keeps its own row -- an earlier event is never rewritten.
    assert events[0]["detail"]["new_stage"] == "DISCOVERED"


def test_blocker_creation_and_resolution(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    blocker = service.record_human_blocker(record["id"], "HUMAN_SALARY_REVIEW_REQUIRED", detail="Confirm min rate")
    assert blocker["status"] == "OPEN"
    assert service.list_open_blockers(record["id"]) == [blocker]

    resolved = service.resolve_human_blocker(blocker["id"], resolution_note="Confirmed $30/hr", resolved_by="candidate")
    assert resolved["status"] == "RESOLVED"
    assert service.list_open_blockers(record["id"]) == []


def test_duplicate_open_blocker_of_same_type_is_idempotent(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    first = service.record_human_blocker(record["id"], "HUMAN_CAPTCHA_REQUIRED")
    second = service.record_human_blocker(record["id"], "HUMAN_CAPTCHA_REQUIRED")
    assert first["id"] == second["id"]


def test_unknown_blocker_type_is_rejected(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    with pytest.raises(ValueError):
        service.record_human_blocker(record["id"], "NOT_A_REAL_BLOCKER")


def test_package_recording_advances_to_prepared(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.transition_stage(record["id"], "SHORTLISTED")
    updated = service.record_application_package(
        record["id"], "pkg-1", resume_path="resume.docx", resume_pdf_path="resume.pdf",
    )
    assert updated["crm_stage"] == "PREPARED"
    assert updated["package_id"] == "pkg-1"
    assert updated["resume_pdf_path"] == "resume.pdf"


def test_applied_requires_confirmed_submission_evidence(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    with pytest.raises(ValueError):
        service.transition_stage(record["id"], "APPLIED")
    with pytest.raises(ValueError):
        service.record_submission_confirmation(record["id"], confirmation_evidence="")


def test_submission_confirmation_sets_applied_and_syncs_legacy_status(tmp_path):
    history, service = crm(tmp_path)
    record = _create(service)
    updated = service.record_submission_confirmation(
        record["id"], confirmation_source="LINKEDIN_JOB_TRACKER", confirmation_evidence="Applied tab shows submission",
        submission_reference="sub-1",
    )
    assert updated["crm_stage"] == "APPLIED"
    assert updated["applied_at"]
    assert updated["status"] == "APPLIED"
    assert updated["application_status"] == "APPLIED"


def test_submission_confirmation_is_idempotent(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    first = service.record_submission_confirmation(
        record["id"], confirmation_evidence="Applied tab confirms it", submission_reference="sub-1",
    )
    events_after_first = len(service.get_timeline(record["id"]))
    second = service.record_submission_confirmation(
        record["id"], confirmation_evidence="Applied tab confirms it", submission_reference="sub-1",
    )
    events_after_second = len(service.get_timeline(record["id"]))
    assert first["applied_at"] == second["applied_at"]
    assert events_after_first == events_after_second


def test_rejection_lifecycle(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    updated = service.record_rejection(record["id"], rejection_reason="Went with an internal candidate")
    assert updated["crm_stage"] == "REJECTED"
    assert updated["rejection_reason"] == "Went with an internal candidate"
    # Idempotent: rejecting an already-rejected opportunity is a safe no-op.
    again = service.record_rejection(record["id"], rejection_reason="duplicate call")
    assert again["crm_stage"] == "REJECTED"
    assert again["rejection_reason"] == "Went with an internal candidate"


def test_interview_lifecycle(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    interview = service.record_interview(record["id"], "SCREENING", scheduled_at="2026-09-10")
    assert interview["stage"] == "SCREENING"
    opportunity = service.get_opportunity(record["id"])
    assert opportunity["crm_stage"] == "SCREENING"

    completed = service.update_interview_outcome(interview["id"], "PASSED", completed_at="2026-09-11")
    assert completed["outcome"] == "PASSED"

    interview_2 = service.record_interview(record["id"], "INTERVIEW_1")
    assert service.get_opportunity(record["id"])["crm_stage"] == "INTERVIEW_1"
    assert interview_2["id"] != interview["id"]


def test_offer_lifecycle_accepted(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    offer = service.record_offer(record["id"], details_reference="Offer letter v1")
    assert offer["status"] == "PENDING"
    assert service.get_opportunity(record["id"])["crm_stage"] == "OFFER"

    decided = service.record_offer_decision(offer["id"], "ACCEPTED")
    assert decided["status"] == "ACCEPTED"
    assert service.get_opportunity(record["id"])["crm_stage"] == "ACCEPTED"


def test_offer_lifecycle_declined(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    offer = service.record_offer(record["id"])
    service.record_offer_decision(offer["id"], "DECLINED")
    assert service.get_opportunity(record["id"])["crm_stage"] == "DECLINED_OFFER"


def test_hired_lifecycle_requires_accepted_offer_first(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    offer = service.record_offer(record["id"])
    service.record_offer_decision(offer["id"], "ACCEPTED")
    hired = service.record_hire(record["id"], hired_at="2026-10-01")
    assert hired["crm_stage"] == "HIRED"
    assert hired["hired_at"] == "2026-10-01"


def test_employer_response_acknowledgement_and_rejection(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    service.record_employer_response(record["id"], "ACKNOWLEDGEMENT", summary="Thanks for applying")
    assert service.get_opportunity(record["id"])["crm_stage"] == "ACKNOWLEDGED"

    service.record_employer_response(record["id"], "REJECTION", summary="Not moving forward")
    assert service.get_opportunity(record["id"])["crm_stage"] == "REJECTED"


def test_recruiter_contact_recording_and_update(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    contact = service.record_recruiter_contact(record["id"], name="Jamie Recruiter", role="RECRUITER", contact_reference="linkedin.com/in/jamie")
    updated = service.update_recruiter_contact(contact["id"], outreach_status="CONTACTED", outreach_date="2026-09-05", outreach_channel="LinkedIn InMail")
    assert updated["outreach_status"] == "CONTACTED"
    with pytest.raises(ValueError):
        service.update_recruiter_contact(contact["id"], name="Not allowed via update")


def test_migration_reconciles_applied_records_with_first_party_evidence(tmp_path):
    history, service = crm(tmp_path)
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id="tracker-61-like")
    history.claim_job(
        fingerprint, status="APPLIED", company="Jobgether", job_title="Head of Finance",
        applied_at="2026-08-31T17:47:02+00:00",
        notes="LinkedIn Job Tracker (Applied tab) confirms this application.",
    )
    record = history.get_record(fingerprint)
    assert record.get("crm_stage") is None

    summary = service.migrate_legacy_records()
    assert summary["migrated"] == 1
    assert summary["submission_confirmed_backfilled"] == 1

    migrated = service.get_opportunity(record["id"])
    assert migrated["crm_stage"] == "APPLIED"
    assert migrated["submission_confirmation_source"] == "LINKEDIN_JOB_TRACKER_HUMAN_CONFIRMED"
    assert migrated["submission_confirmation_reference"] == "LEGACY_NOTES_EVIDENCE"

    timeline = service.get_timeline(record["id"])
    assert any(e["detail"]["event_type"] == "SUBMISSION_CONFIRMED" for e in timeline)

    # Idempotent: running the migration again changes nothing further.
    second_summary = service.migrate_legacy_records()
    assert second_summary["migrated"] == 0
    assert second_summary["already_migrated"] == 1


def test_migration_never_fabricates_missing_evidence(tmp_path):
    history, service = crm(tmp_path)
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id="unconfirmed-applied")
    history.claim_job(fingerprint, status="APPLIED", company="X", job_title="Y")
    service.migrate_legacy_records()
    record = history.get_record(fingerprint)
    assert record["crm_stage"] == "APPLIED"
    assert record.get("submission_confirmation_reference") in (None, "")


def test_dashboard_funnel_and_conversion_rates(tmp_path):
    _, service = crm(tmp_path)
    a = _create(service, external_id="a")
    b = _create(service, external_id="b")
    c = _create(service, external_id="c")

    service.transition_stage(a["id"], "ELIGIBLE")
    service.transition_stage(a["id"], "SHORTLISTED")
    service.record_submission_confirmation(a["id"], confirmation_evidence="confirmed", submission_reference="a1")
    service.record_employer_response(a["id"], "ACKNOWLEDGEMENT")
    service.record_interview(a["id"], "SCREENING")
    offer = service.record_offer(a["id"])
    service.record_offer_decision(offer["id"], "ACCEPTED")
    service.record_hire(a["id"])

    service.transition_stage(b["id"], "ELIGIBLE")

    counts = service.funnel_counts()
    assert counts["discovered"] == 3
    assert counts["eligible"] == 2
    assert counts["shortlisted"] == 1
    assert counts["applied"] == 1
    assert counts["acknowledged"] == 1
    assert counts["responses"] == 1
    assert counts["interviews"] == 1
    assert counts["offers"] == 1
    assert counts["hired"] == 1

    rates = service.conversion_rates(counts)
    assert rates["discovery_to_eligible"] == round(2 / 3, 4)
    assert rates["offer_to_hired"] == 1.0

    breakdown = service.breakdown_by("company")
    assert {"value": "Acme", "count": 3} in breakdown


def test_breakdown_by_rejects_unknown_field(tmp_path):
    _, service = crm(tmp_path)
    with pytest.raises(ValueError):
        service.breakdown_by("job_fingerprint; DROP TABLE application_history;")


def test_update_opportunity_rejects_protected_fields(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service)
    with pytest.raises(ValueError):
        service.update_opportunity(record["id"], crm_stage="HIRED")
    updated = service.update_opportunity(record["id"], notes="candidate-authored note")
    assert updated["notes"] == "candidate-authored note"
