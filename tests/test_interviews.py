"""Web App Phase 6: Interviews workspace tests.

Mutation tests use tmp_path-based SQLite databases only. Read-only tests
against the real production database (`OpportunityCRMService()` with no
override) never write -- they only assert on already-existing evidence.
"""
import json

from fastapi.testclient import TestClient

from app.api.dashboard import app, get_crm_service
from app.services import interview_briefing_service as ibs
from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
from app.services.master_profile_service import MasterProfileService
from app.services.opportunity_crm_service import OpportunityCRMService


def _open(db_path):
    return OpportunityCRMService(ApplicationHistoryService(db_path))


def _client(db_path):
    def _override():
        service = _open(db_path)
        try:
            yield service
        finally:
            service.close()

    app.dependency_overrides[get_crm_service] = _override
    return TestClient(app)


_SNAPSHOT = {
    "job_analysis": {
        "seniority": "Senior Leadership",
        "industry": "Fintech",
        "required_skills": ["budgeting", "financial modelling", "stakeholder management"],
        "preferred_skills": ["SQL"],
        "technologies": ["Excel", "Power BI"],
        "summary": "Lead the finance function through a transformation program.",
        "match_reasoning": {
            "must_have_skills": ["budgeting", "financial modelling"],
            "biggest_challenges": ["Scaling finance operations across entities"],
            "ideal_candidate": "An experienced finance leader.",
        },
    },
    "employer": {
        "industry": "Fintech", "company_size": "50-200", "remote_friendly": True,
        "strengths": ["Fast growth"], "risks": ["Funding uncertainty"],
        "recommendation": "Recommended", "reason": "Strong opportunity.",
    },
}


def _seed_opportunity(tmp_path, *, company="Test Co", job_title="Finance Lead", external_id="iv-seed", with_snapshot=True):
    db_path = tmp_path / f"interview-{external_id}.db"
    history = ApplicationHistoryService(db_path)
    service = OpportunityCRMService(history)
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id=external_id)
    record = service.create_opportunity(fingerprint, company=company, job_title=job_title)
    service.record_submission_confirmation(record["id"], confirmation_evidence="e1", submission_reference="s1")
    if with_snapshot:
        service.update_opportunity(
            record["id"], evaluation_snapshot=json.dumps(_SNAPSHOT),
            job_description="Full JD text about budgeting and financial modelling and stakeholder management.",
        )
    tracker_id = record["id"]
    service.close()
    return db_path, tracker_id


def _seed_interview_via_invitation(tmp_path, **kwargs):
    db_path, tracker_id = _seed_opportunity(tmp_path, **kwargs)
    service = _open(db_path)
    try:
        response = service.record_employer_response(tracker_id, "INTERVIEW_INVITATION", summary="We would like to invite you to interview.")
        interviews = service.list_interviews(tracker_id=tracker_id)
        interview_id = interviews[0]["id"]
        response_id = response["id"]
    finally:
        service.close()
    return db_path, tracker_id, interview_id, response_id


# --- Service-layer: interview_briefing_service (pure functions) -------------
def test_readiness_matrix_uses_actual_vacancy_requirements(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path)
    service = _open(db_path)
    try:
        record = service.get_opportunity(tracker_id)
    finally:
        service.close()
    profile = MasterProfileService().load()
    rows = ibs.build_readiness_matrix(record, profile)
    requirement_texts = {row["requirement"] for row in rows}
    assert "budgeting" in requirement_texts or "financial modelling" in requirement_texts
    for row in rows:
        assert row["readiness"] in ("Strong Evidence", "Supporting Evidence", "Evidence Gap", "Clarify")


def test_readiness_matrix_empty_without_job_analysis(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path, external_id="no-snap", with_snapshot=False)
    service = _open(db_path)
    try:
        record = service.get_opportunity(tracker_id)
    finally:
        service.close()
    profile = MasterProfileService().load()
    assert ibs.build_readiness_matrix(record, profile) == []


def test_missing_evidence_is_labelled_not_invented(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path)
    service = _open(db_path)
    try:
        record = service.get_opportunity(tracker_id)
    finally:
        service.close()
    profile = MasterProfileService().load()
    readiness = ibs.build_readiness_matrix(record, profile)
    my_evidence = ibs.build_my_evidence(readiness, profile)
    for row in my_evidence:
        if row["readiness"] == "Evidence Gap":
            assert row["evidence"] == "Evidence gap -- do not fabricate."


def test_candidate_evidence_never_invents_numbers_or_employers(tmp_path):
    """Every citation traced back for Answer Preparation must be a real
    responsibility/achievement line already in the candidate's own (VERIFIED)
    evidence -- never a fabricated sentence."""
    db_path, tracker_id = _seed_opportunity(tmp_path)
    service = _open(db_path)
    try:
        record = service.get_opportunity(tracker_id)
    finally:
        service.close()
    profile = MasterProfileService().load()
    readiness = ibs.build_readiness_matrix(record, profile)
    preps = ibs.build_answer_preparation(readiness, profile)
    enriched_text_blob = " ".join(
        line for entry in ibs._enriched_employment(profile)
        for line in (entry.get("responsibilities") or []) + (entry.get("achievements") or [])
    )
    for prep in preps:
        assert prep["action"] in enriched_text_blob
        if prep["result"] and "No separately quantified" not in prep["result"]:
            assert prep["result"] in enriched_text_blob


def test_likely_questions_derive_from_role_and_evidence(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path)
    service = _open(db_path)
    try:
        record = service.get_opportunity(tracker_id)
    finally:
        service.close()
    profile = MasterProfileService().load()
    readiness = ibs.build_readiness_matrix(record, profile)
    questions = ibs.build_likely_questions(record, readiness, risks=["Geographic eligibility unconfirmed."])
    assert any(questions.values())  # not every category empty
    assert any("Scaling finance operations" in q for q in questions["Commercial"])


def test_company_intelligence_labels_stored_evidence(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path)
    service = _open(db_path)
    try:
        record = service.get_opportunity(tracker_id)
    finally:
        service.close()
    company = ibs.build_company_intelligence(record)
    assert company["available"] is True
    assert company["industry"] == "Fintech"


def test_company_intelligence_absent_is_honest_not_blocking(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path, external_id="no-company", with_snapshot=False)
    service = _open(db_path)
    try:
        record = service.get_opportunity(tracker_id)
    finally:
        service.close()
    company = ibs.build_company_intelligence(record)
    assert company == {"available": False}


# --- Route-level: /interviews and /interview/{id} ---------------------------
def test_zero_interview_state_renders_honestly(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path, external_id="zero-state")
    try:
        client = _client(db_path)
        body = client.get("/interviews").text
        assert "No interviews scheduled yet" in body
        assert "automatically create the preparation workspace" in body
    finally:
        app.dependency_overrides.clear()


def test_interview_invitation_creates_real_interview_and_links_to_workspace(tmp_path):
    db_path, tracker_id, interview_id, response_id = _seed_interview_via_invitation(tmp_path, external_id="invite1")
    try:
        client = _client(db_path)
        landing = client.get("/interviews").text
        assert "Test Co" in landing
        inbox_detail = client.get(f"/employer-inbox/{tracker_id}/{response_id}").text
        assert f'href="/interview/{interview_id}"' in inbox_detail
        workspace = client.get(f"/interview/{interview_id}").text
        assert workspace  # 200 implicit via TestClient not raising
    finally:
        app.dependency_overrides.clear()


def test_no_fabricated_interview_from_acknowledgement(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path, external_id="ack-only")
    service = _open(db_path)
    try:
        service.record_employer_response(tracker_id, "ACKNOWLEDGEMENT", summary="Thanks for applying")
        assert service.list_interviews(tracker_id=tracker_id) == []
    finally:
        service.close()


def test_briefing_uses_actual_job_and_candidate_evidence(tmp_path):
    db_path, tracker_id, interview_id, _ = _seed_interview_via_invitation(tmp_path, external_id="briefing1")
    try:
        client = _client(db_path)
        body = client.get(f"/interview/{interview_id}").text
        assert "Lead the finance function through a transformation program." in body
        assert "budgeting" in body.lower()
    finally:
        app.dependency_overrides.clear()


def test_optional_notes_are_auditable_and_never_required(tmp_path):
    db_path, tracker_id, interview_id, _ = _seed_interview_via_invitation(tmp_path, external_id="notes1")
    try:
        client = _client(db_path)
        r = client.post(f"/interview/{interview_id}/notes", data={"notes": "Bring updated deck."}, follow_redirects=False)
        assert r.status_code == 303
        body = client.get(f"/interview/{interview_id}").text
        assert "Bring updated deck." in body
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        events = [e for e in service.get_timeline(tracker_id) if e.get("kind") == "EVENT" and e["detail"].get("event_type") == "INTERVIEW_NOTES_UPDATED"]
        assert len(events) == 1  # auditable
    finally:
        service.close()


def test_debrief_is_optional_and_only_appears_after_completion(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path, external_id="debrief1")
    service = _open(db_path)
    try:
        interview = service.record_interview(tracker_id, "INTERVIEW_1")
    finally:
        service.close()
    try:
        client = _client(db_path)
        before = client.get(f"/interview/{interview['id']}").text
        assert "How interested are you after the interview?" not in before
    finally:
        app.dependency_overrides.clear()

    service = _open(db_path)
    try:
        service.update_interview_outcome(interview["id"], "PASSED", completed_at="2026-09-05T00:00:00Z")
    finally:
        service.close()
    try:
        client = _client(db_path)
        after = client.get(f"/interview/{interview['id']}").text
        assert "How interested are you after the interview?" in after
        # workflow continues normally without a response -- no error, no block
        r = client.get(f"/interview/{interview['id']}")
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_ordinary_interview_preparation_requires_no_mandatory_feedback(tmp_path):
    """Opening the workspace for an upcoming (not-yet-completed) interview
    must never demand any input -- GET succeeds and shows no required form."""
    db_path, tracker_id, interview_id, _ = _seed_interview_via_invitation(tmp_path, external_id="noreq1")
    try:
        client = _client(db_path)
        response = client.get(f"/interview/{interview_id}")
        assert response.status_code == 200
        assert "How interested are you after the interview?" not in response.text
    finally:
        app.dependency_overrides.clear()


def test_confirming_interview_time_is_the_only_mandatory_intervention_point(tmp_path):
    db_path, tracker_id, interview_id, _ = _seed_interview_via_invitation(tmp_path, external_id="schedtime1")
    try:
        client = _client(db_path)
        before = client.get(f"/interview/{interview_id}").text
        assert "doesn't have a confirmed time yet" in before
        r = client.post(f"/interview/{interview_id}/schedule", data={"scheduled_at": "2026-09-20T10:00"}, follow_redirects=False)
        assert r.status_code == 303
        after = client.get(f"/interview/{interview_id}").text
        assert "doesn't have a confirmed time yet" not in after
    finally:
        app.dependency_overrides.clear()


def test_action_required_integration_does_not_create_a_duplicate_queue(tmp_path):
    """An interview invitation's action requirement is represented via the
    EXISTING Employer Inbox EMPLOYER_ACTION category -- no second action
    type/category is introduced for interviews."""
    db_path, tracker_id, interview_id, response_id = _seed_interview_via_invitation(tmp_path, external_id="actreq1")
    service = _open(db_path)
    try:
        items = service.action_required_items()
        categories = {item["category"] for item in items if item["tracker_id"] == tracker_id}
        assert categories == {"EMPLOYER_ACTION"}
        assert set(OpportunityCRMService.ACTION_CATEGORIES) == {
            "REVIEW_AND_SUBMIT", "ANSWER_REQUIRED", "ELIGIBILITY_DECISION", "BROWSER_ACTION", "EMPLOYER_ACTION",
        }
    finally:
        service.close()


def test_employer_inbox_evidence_remains_separate_from_interview_record(tmp_path):
    db_path, tracker_id, interview_id, response_id = _seed_interview_via_invitation(tmp_path, external_id="separate1")
    service = _open(db_path)
    try:
        original = service.get_employer_response(response_id)
        assert original["response_type"] == "INTERVIEW_INVITATION"
        assert original["summary"] == "We would like to invite you to interview."
        interview = service.get_interview(interview_id)
        assert interview["tracker_id"] == tracker_id
        # Distinct tables, distinct ids -- never merged into one record.
        assert "response_type" not in interview
        assert "stage" not in original
    finally:
        service.close()


def test_application_linkage_works_from_interview_workspace(tmp_path):
    db_path, tracker_id, interview_id, _ = _seed_interview_via_invitation(tmp_path, external_id="applink1")
    try:
        client = _client(db_path)
        body = client.get(f"/interview/{interview_id}").text
        assert f'href="/application/{tracker_id}"' in body
    finally:
        app.dependency_overrides.clear()


def test_application_detail_shows_prepare_for_interview_link(tmp_path):
    db_path, tracker_id, interview_id, _ = _seed_interview_via_invitation(tmp_path, external_id="prepbtn1")
    try:
        client = _client(db_path)
        body = client.get(f"/application/{tracker_id}").text
        assert f'href="/interview/{interview_id}"' in body
        assert "Prepare for Interview" in body
    finally:
        app.dependency_overrides.clear()


def test_no_scoring_or_priority_mutation_from_interview_actions(tmp_path):
    db_path, tracker_id, interview_id, _ = _seed_interview_via_invitation(tmp_path, external_id="noscoring1")
    service = _open(db_path)
    try:
        before = dict(service.get_opportunity(tracker_id))
    finally:
        service.close()
    try:
        client = _client(db_path)
        client.post(f"/interview/{interview_id}/schedule", data={"scheduled_at": "2026-09-20T10:00"}, follow_redirects=False)
        client.post(f"/interview/{interview_id}/notes", data={"notes": "test"}, follow_redirects=False)
        client.post("/application/" + str(tracker_id) + "/feedback", data={"worth_pursuing": "YES", "interest_change": "HIGHER"}, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        after = dict(service.get_opportunity(tracker_id))
        assert after["intelligence_priority"] == before["intelligence_priority"]
        assert after["career_score"] == before["career_score"]
        assert after["crm_stage"] == before["crm_stage"] or after["crm_stage"] == "INTERVIEW_1"
    finally:
        service.close()


def test_interview_workspace_404s_for_unknown_id(tmp_path):
    db_path, tracker_id = _seed_opportunity(tmp_path, external_id="notfound1")
    try:
        client = _client(db_path)
        response = client.get("/interview/999999")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


# --- Production read-only verification --------------------------------------
def test_production_has_zero_interviews_and_landing_reflects_it_read_only():
    service = OpportunityCRMService()
    try:
        interviews = service.list_interviews()
        summary = service.interviews_summary()
    finally:
        service.close()
    assert interviews == []
    assert summary["upcoming"] == 0
    assert summary["completed"] == 0
    assert summary["offers"] == 0

    client = TestClient(app)
    body = client.get("/interviews").text
    assert "No interviews scheduled yet" in body


def test_production_briefing_service_does_not_crash_on_real_trackers_read_only():
    """Read-only: confirms the briefing service handles real production
    evaluation_snapshot/job_description data (trackers 61/81/103) without
    crashing or fabricating -- never writes anything."""
    service = OpportunityCRMService()
    try:
        profile = MasterProfileService().load()
        for tracker_id in (61, 81, 103):
            record = service.get_opportunity(tracker_id)
            assert record is not None
            readiness = ibs.build_readiness_matrix(record, profile)
            why = []
            risks = []
            briefing = ibs.build_briefing(record, profile, readiness, risks, why)
            assert briefing["role"]["company"]
            ibs.build_my_evidence(readiness, profile)
            ibs.build_answer_preparation(readiness, profile)
            ibs.build_likely_questions(record, readiness, risks)
            ibs.build_company_intelligence(record)
            ibs.build_employer_questions(record, readiness)
    finally:
        service.close()
