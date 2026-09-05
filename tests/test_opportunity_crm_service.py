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


# --- Web App Phase 1.1: corrected, evidence-based cumulative funnel --------
def test_cumulative_funnel_never_credits_a_stage_skip_as_an_unevidenced_milestone(tmp_path):
    """Phase 1's high-water-mark inference (reaching a later stage implies
    passing the earlier ones) was proven wrong against real production data:
    this CRM's ALLOWED_TRANSITIONS deliberately permits skip-ahead, and a
    stage reached is not proof an earlier named milestone actually happened.
    A record fast-tracked straight from DISCOVERED to SHORTLISTED (no
    eligibility ever assessed: no intelligence_priority, no ELIGIBLE event)
    must NOT be counted as having reached any evidenced milestone here --
    ELIGIBLE/SHORTLISTED aren't tracked in this evidence-based funnel at
    all, and none of applied_at/employer_responses/interviews/offers exist
    for it either."""
    _, service = crm(tmp_path)
    record = _create(service, external_id="skip-1")
    service.transition_stage(record["id"], "SHORTLISTED", reason="fast-tracked")

    cumulative = service.cumulative_funnel_counts()
    assert cumulative["APPLIED"] == 0
    assert cumulative["ACKNOWLEDGED"] == 0
    assert cumulative["MEANINGFUL_RESPONSE"] == 0
    assert cumulative["INTERVIEW"] == 0
    assert cumulative["OFFER"] == 0
    assert "ELIGIBLE" not in cumulative and "SHORTLISTED" not in cumulative


def test_cumulative_funnel_applied_uses_applied_at_not_a_stage_label(tmp_path):
    """A record migrated straight to APPLIED with real applied_at evidence
    IS counted -- unlike the stage-skip case above, this has direct,
    verifiable evidence (`record_submission_confirmation` sets applied_at)."""
    _, service = crm(tmp_path)
    record = _create(service, external_id="applied-1")
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")

    cumulative = service.cumulative_funnel_counts()
    assert cumulative["DISCOVERED"] == 1
    assert cumulative["APPLIED"] == 1


def test_cumulative_funnel_tracks_acknowledgement_interview_and_offer_by_direct_evidence(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service, external_id="full-1")
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    service.record_employer_response(record["id"], "ACKNOWLEDGEMENT")
    service.record_interview(record["id"], "SCREENING")
    service.record_offer(record["id"])

    cumulative = service.cumulative_funnel_counts()
    assert cumulative["ACKNOWLEDGED"] == 1
    assert cumulative["INTERVIEW"] == 1
    assert cumulative["OFFER"] == 1
    assert cumulative["MEANINGFUL_RESPONSE"] == 0  # an acknowledgement alone is never "meaningful"


def test_application_performance_rates_are_none_not_fabricated_zero_when_undefined(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service, external_id="rate-1")
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    rates = service.application_performance_rates()
    assert rates["applied_to_acknowledged"] == 0.0  # applied=1, acknowledged=0 -- a real, defined 0%
    assert rates["interview_to_offer"] is None  # interview=0 -- undefined, never a fabricated 0%


def test_application_performance_rates_reflect_real_conversions(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service, external_id="rate-2")
    service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    service.record_employer_response(record["id"], "ACKNOWLEDGEMENT")
    service.record_interview(record["id"], "SCREENING")
    service.record_offer(record["id"])
    rates = service.application_performance_rates()
    assert rates["applied_to_acknowledged"] == 1.0
    assert rates["applied_to_interview"] == 1.0
    assert rates["interview_to_offer"] == 1.0


# --- Web App Phase 1.1: pipeline grouping reconciles exactly ----------------
def test_pipeline_group_counts_cover_every_stage_exactly_once():
    from app.models.crm import PIPELINE_VIEW_STAGES

    seen = []
    for _, stages in OpportunityCRMService.PIPELINE_GROUPS:
        seen.extend(stages)
    assert sorted(seen) == sorted(PIPELINE_VIEW_STAGES)
    assert len(seen) == len(set(seen))  # no stage assigned to two groups


def test_pipeline_group_counts_sum_exactly_to_total_opportunities(tmp_path):
    _, service = crm(tmp_path)
    a = _create(service, external_id="grp-a")
    service.transition_stage(a["id"], "SHORTLISTED")
    b = _create(service, external_id="grp-b")
    service.record_submission_confirmation(b["id"], confirmation_evidence="confirmed", submission_reference="s1")
    c = _create(service, external_id="grp-c")  # stays DISCOVERED

    groups = service.pipeline_group_counts()
    total = service.connection.execute("SELECT COUNT(*) FROM application_history").fetchone()[0]
    assert sum(group["count"] for group in groups) == total == 3
    by_label = {group["label"]: group["count"] for group in groups}
    assert by_label["Shortlisted"] == 1
    assert by_label["Applied"] == 1
    assert by_label["Screening & Eligibility"] == 1


# --- Web App Phase 1.1: priority mix includes an explicit UNSCORED bucket --
def test_priority_mix_counts_includes_unscored_records(tmp_path):
    _, service = crm(tmp_path)
    a = _create(service, external_id="mix-a")
    service.update_opportunity(a["id"], intelligence_priority="A")
    _create(service, external_id="mix-b")  # no intelligence_priority set
    mix = service.priority_mix_counts()
    assert mix["A"] == 1
    assert mix["UNSCORED"] == 1
    assert sum(mix.values()) == 2


# --- Web App Phase 1.1: plain-language, priority-ordered attention queue ---
def test_describe_attention_reason_translates_stage_and_blocker_codes():
    describe = OpportunityCRMService.describe_attention_reason
    assert describe("Stage awaiting human action: READY_FOR_HUMAN_SUBMIT") == "Ready for you to submit"
    assert describe("Open blocker: HUMAN_CAPTCHA_REQUIRED") == "CAPTCHA needs solving"
    assert describe("Open blocker: HUMAN_SALARY_REVIEW_REQUIRED -- Confirm min rate") == "Salary needs your review: Confirm min rate"
    assert describe("Open blocker: OTHER -- something unusual") == "Needs your review: something unusual"


def test_attention_queue_orders_captcha_before_routine_ready_for_submit(tmp_path):
    _, service = crm(tmp_path)
    routine = _create(service, external_id="routine-1")
    service.update_opportunity(routine["id"], intelligence_priority="A")
    service.transition_stage(routine["id"], "READY_FOR_HUMAN_SUBMIT")
    urgent = _create(service, external_id="urgent-1")
    service.update_opportunity(urgent["id"], intelligence_priority="E")
    service.transition_stage(urgent["id"], "READY_FOR_HUMAN_SUBMIT")
    service.record_human_blocker(urgent["id"], "HUMAN_CAPTCHA_REQUIRED", detail="Solve to continue")

    queue = service.attention_queue()
    assert queue[0]["tracker_id"] == urgent["id"]  # CAPTCHA outranks a routine wait, even at lower priority
    assert any("CAPTCHA needs solving" in reason for reason in queue[0]["plain_reasons"])


def test_attention_queue_filters_by_priority_and_limits_results(tmp_path):
    _, service = crm(tmp_path)
    for i in range(3):
        record = _create(service, external_id=f"prio-a-{i}")
        service.update_opportunity(record["id"], intelligence_priority="A")
        service.transition_stage(record["id"], "READY_FOR_HUMAN_SUBMIT")
    b_record = _create(service, external_id="prio-b")
    service.update_opportunity(b_record["id"], intelligence_priority="B")
    service.transition_stage(b_record["id"], "READY_FOR_HUMAN_SUBMIT")

    filtered = service.attention_queue(priority="B")
    assert len(filtered) == 1
    assert filtered[0]["tracker_id"] == b_record["id"]

    limited = service.attention_queue(limit=2)
    assert len(limited) == 2
    assert len(service.attention_queue()) == 4  # limit never affects the true total


def test_attention_priority_distribution_buckets_unscored_separately(tmp_path):
    _, service = crm(tmp_path)
    scored = _create(service, external_id="attn-scored")
    service.update_opportunity(scored["id"], intelligence_priority="C")
    service.transition_stage(scored["id"], "ELIGIBILITY_REVIEW")
    unscored = _create(service, external_id="attn-unscored")
    service.transition_stage(unscored["id"], "READY_FOR_HUMAN_SUBMIT")

    distribution = service.attention_priority_distribution()
    assert distribution["C"] == 1
    assert distribution["UNSCORED"] == 1


def test_response_quality_never_counts_an_acknowledgement_as_meaningful(tmp_path):
    _, service = crm(tmp_path)
    acknowledged_only = _create(service, external_id="ack-1")
    service.record_submission_confirmation(acknowledged_only["id"], confirmation_evidence="confirmed", submission_reference="s1")
    service.record_employer_response(acknowledged_only["id"], "ACKNOWLEDGEMENT")

    recruiter_reply = _create(service, external_id="recruiter-1")
    service.record_submission_confirmation(recruiter_reply["id"], confirmation_evidence="confirmed", submission_reference="s2")
    service.record_employer_response(recruiter_reply["id"], "RECRUITER_CONTACT")

    quality = service.response_quality_counts()
    assert quality["acknowledgements"] == 1
    assert quality["meaningful_responses"] == 1


def test_response_quality_counts_rejection_and_offer_as_meaningful(tmp_path):
    _, service = crm(tmp_path)
    rejected = _create(service, external_id="rej-1")
    service.record_submission_confirmation(rejected["id"], confirmation_evidence="confirmed", submission_reference="s1")
    service.record_employer_response(rejected["id"], "REJECTION")

    offered = _create(service, external_id="offer-1")
    service.record_submission_confirmation(offered["id"], confirmation_evidence="confirmed", submission_reference="s2")
    service.record_employer_response(offered["id"], "OFFER")

    quality = service.response_quality_counts()
    assert quality["meaningful_responses"] == 2
    assert quality["acknowledgements"] == 0


def test_recent_activity_orders_newest_first_and_respects_limit(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service, external_id="activity-1")
    service.transition_stage(record["id"], "VERIFIED")
    service.transition_stage(record["id"], "ELIGIBLE")

    activity = service.recent_activity(limit=2)
    assert len(activity) == 2
    assert activity[0]["new_stage"] == "ELIGIBLE"  # most recent first
    assert activity[0]["company"] == "Acme"


def test_recent_activity_scoped_to_tracker_ids_excludes_others(tmp_path):
    _, service = crm(tmp_path)
    a = _create(service, external_id="scope-a")  # default company "Acme"
    b = service.create_opportunity(
        job_fingerprint(source="LinkedIn", external_job_id="scope-b"), company="Beta", job_title="Finance Manager",
    )
    service.transition_stage(b["id"], "VERIFIED")

    scoped = service.recent_activity(limit=15, tracker_ids=[a["id"]])
    assert all(event["tracker_id"] == a["id"] for event in scoped)
    assert not any(event["company"] == "Beta" for event in scoped)

    empty_scope = service.recent_activity(limit=15, tracker_ids=[])
    assert empty_scope == []


# --- Web App Phase 2: user decisions (controlled, separate from priority) --
def test_record_user_decision_never_touches_intelligence_priority_or_crm_stage(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service, external_id="dec-1")
    service.update_opportunity(record["id"], intelligence_priority="C")
    before = service.get_opportunity(record["id"])

    service.record_user_decision(record["id"], "APPLY", reason_code="CAREER_VALUE", note="Great growth path")

    after = service.get_opportunity(record["id"])
    assert after["intelligence_priority"] == before["intelligence_priority"] == "C"
    assert after["crm_stage"] == before["crm_stage"]


def test_record_user_decision_is_append_only_and_auditable(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service, external_id="dec-2")

    service.record_user_decision(record["id"], "WATCH", reason_code="LOCATION")
    service.record_user_decision(record["id"], "APPLY", note="Changed my mind after research")

    history = service.list_user_decisions(record["id"])
    assert len(history) == 2  # a changed mind is a new row, never an edit
    assert history[0]["decision"] == "APPLY"  # most recent first
    assert history[1]["decision"] == "WATCH"
    latest = service.get_latest_user_decision(record["id"])
    assert latest["decision"] == "APPLY"
    assert latest["note"] == "Changed my mind after research"

    events = [e["detail"] for e in service.get_timeline(record["id"]) if e["detail"].get("event_type") == "USER_DECISION_RECORDED"]
    assert len(events) == 2


def test_record_user_decision_rejects_unknown_decision_and_reason_code(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service, external_id="dec-3")
    with pytest.raises(ValueError):
        service.record_user_decision(record["id"], "MAYBE")
    with pytest.raises(ValueError):
        service.record_user_decision(record["id"], "APPLY", reason_code="NOT_A_REAL_CODE")


def test_get_latest_user_decision_is_none_when_no_decision_recorded(tmp_path):
    _, service = crm(tmp_path)
    record = _create(service, external_id="dec-4")
    assert service.get_latest_user_decision(record["id"]) is None


# --- Web App Phase 2: Opportunities workspace search/pagination -----------
def test_search_opportunities_filters_by_text_and_priority(tmp_path):
    _, service = crm(tmp_path)
    a = service.create_opportunity(
        job_fingerprint(source="LinkedIn", external_job_id="search-a"), company="Acme Robotics", job_title="Finance Manager",
    )
    service.update_opportunity(a["id"], intelligence_priority="A")
    b = service.create_opportunity(
        job_fingerprint(source="LinkedIn", external_job_id="search-b"), company="Beta Corp", job_title="Controller",
    )
    service.update_opportunity(b["id"], intelligence_priority="C")

    by_text = service.search_opportunities(search="Acme")
    assert [r["id"] for r in by_text["results"]] == [a["id"]]

    by_priority = service.search_opportunities(intelligence_priority="C")
    assert [r["id"] for r in by_priority["results"]] == [b["id"]]

    unscored = service.search_opportunities(intelligence_priority="UNSCORED")
    assert unscored["total"] == 0  # both records here are scored


def test_search_opportunities_orders_by_priority_rank_before_tracker_id(tmp_path):
    _, service = crm(tmp_path)
    low = _create(service, external_id="rank-low")
    service.update_opportunity(low["id"], intelligence_priority="E")
    high = _create(service, external_id="rank-high")
    service.update_opportunity(high["id"], intelligence_priority="A")

    result = service.search_opportunities()
    # 'high' (A) has a LOWER tracker id than 'low' (E) is not guaranteed by
    # creation order alone here, so assert on the actual rank, not position.
    ids_in_order = [r["id"] for r in result["results"]]
    assert ids_in_order.index(high["id"]) < ids_in_order.index(low["id"])


def test_search_opportunities_paginates(tmp_path):
    _, service = crm(tmp_path)
    for i in range(5):
        _create(service, external_id=f"page-{i}")

    page1 = service.search_opportunities(page=1, page_size=2)
    page2 = service.search_opportunities(page=2, page_size=2)
    assert page1["total"] == 5
    assert page1["total_pages"] == 3
    assert len(page1["results"]) == 2
    assert len(page2["results"]) == 2
    assert {r["id"] for r in page1["results"]}.isdisjoint({r["id"] for r in page2["results"]})


def test_search_opportunities_score_range_filter(tmp_path):
    _, service = crm(tmp_path)
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id="score-1")
    service.create_opportunity(fingerprint, company="Acme", job_title="Role", career_score=90.0)
    fingerprint2 = job_fingerprint(source="LinkedIn", external_job_id="score-2")
    service.create_opportunity(fingerprint2, company="Acme", job_title="Role", career_score=40.0)

    high_only = service.search_opportunities(min_score=70)
    assert high_only["total"] == 1
    assert high_only["results"][0]["career_score"] == 90.0


def test_opportunity_filter_options_reflects_real_distinct_values(tmp_path):
    _, service = crm(tmp_path)
    _create(service, external_id="opt-1", market="united_kingdom", work_arrangement="REMOTE", source="LinkedIn")
    options = service.opportunity_filter_options()
    assert "united_kingdom" in options["market"]
    assert "REMOTE" in options["work_arrangement"]
    assert "LinkedIn" in options["source"]
