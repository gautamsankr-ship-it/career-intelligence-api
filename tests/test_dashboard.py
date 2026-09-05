import pytest
from fastapi.testclient import TestClient

from app.api.dashboard import app, get_crm_service
from app.config import APPLICATION_HISTORY_DB
from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
from app.services.opportunity_crm_service import OpportunityCRMService


def _seed(tmp_path):
    """Seed a hermetic tmp sqlite file and return (db_path, ids) -- the
    connection used to seed it is fully closed before returning, since
    TestClient exercises the FastAPI route in a separate worker thread and a
    sqlite3 connection cannot cross threads."""
    db_path = tmp_path / "history.db"
    history = ApplicationHistoryService(db_path)
    service = OpportunityCRMService(history)

    def _create(external_id, **fields):
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id=external_id)
        return service.create_opportunity(fingerprint, **fields)

    a = _create("a", company="Acme", job_title="Finance Manager", market="united_kingdom", source="LinkedIn", intelligence_priority="A")
    service.transition_stage(a["id"], "ELIGIBLE")
    service.transition_stage(a["id"], "SHORTLISTED")
    service.record_submission_confirmation(a["id"], confirmation_evidence="Applied tab confirms it", submission_reference="a1")
    service.record_employer_response(a["id"], "ACKNOWLEDGEMENT")
    service.record_interview(a["id"], "SCREENING")
    offer = service.record_offer(a["id"])
    service.record_offer_decision(offer["id"], "ACCEPTED")
    service.record_hire(a["id"])

    b = _create("b", company="Beta Co", job_title="Head of Finance", market="united_states", source="LinkedIn")
    service.transition_stage(b["id"], "ELIGIBILITY_REVIEW")
    service.record_human_blocker(b["id"], "HUMAN_SALARY_REVIEW_REQUIRED", detail="Confirm min rate")

    c = _create("c", company="Gamma Ltd", job_title="Financial Controller", market="australia", source="Indeed")

    ids = {"a": a["id"], "b": b["id"], "c": c["id"]}
    service.close()
    return db_path, ids


def _open(db_path):
    return OpportunityCRMService(ApplicationHistoryService(db_path))


def _client(db_path):
    """Override the dashboard's dependency with one that opens a fresh
    connection per call (matching how the real, un-overridden dependency
    behaves per-request) -- never sharing one sqlite3 connection across the
    TestClient's worker thread and the test's own thread."""
    def _override():
        service = _open(db_path)
        try:
            yield service
        finally:
            service.close()

    app.dependency_overrides[get_crm_service] = _override
    return TestClient(app)


def test_home_shows_stage_counts_and_conversion_rates(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/")
        assert response.status_code == 200
        body = response.text
        assert "Total Opportunities" in body
        # 3 discovered, 1 reached eligible/shortlisted/applied, 1 hired.
        assert "<div class=\"n\">3</div>" in body
        assert "100.0%" in body  # offer -> hired is 1/1
    finally:
        app.dependency_overrides.clear()


def test_home_filters_opportunities_by_stage(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/?crm_stage=ELIGIBILITY_REVIEW")
        assert response.status_code == 200
        body = response.text
        assert "Beta Co" in body
        assert "Acme" not in body
        assert "Gamma Ltd" not in body
    finally:
        app.dependency_overrides.clear()


def test_home_filters_by_market_and_source_combined(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/?market=australia&source=Indeed")
        assert response.status_code == 200
        body = response.text
        assert "Gamma Ltd" in body
        assert "Acme" not in body
    finally:
        app.dependency_overrides.clear()


def test_home_empty_filter_result_shows_empty_state(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/?market=antarctica")
        assert response.status_code == 200
        assert "No opportunities match this filter." in response.text
    finally:
        app.dependency_overrides.clear()


def test_needs_attention_surfaces_blocker_and_attention_stage_but_not_hired(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/")
        assert response.status_code == 200
        body = response.text
        assert f"#{ids['b']}" in body  # ELIGIBILITY_REVIEW + open blocker
        assert "HUMAN_SALARY_REVIEW_REQUIRED" in body
        # The hired opportunity (a) is not "needing attention".
        attention_section = body.split("Needs My Attention")[1].split("Opportunities (")[0]
        assert "Acme" not in attention_section
    finally:
        app.dependency_overrides.clear()


def test_needs_attention_never_shows_stale_execution_flags_without_a_real_blocker(tmp_path):
    """A CAPTCHA/MFA flag from a past, already-ended browser execution
    session must never be treated as a live blocker unless the CRM itself
    still has an OPEN human_blockers row for it."""
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        attention = service.needs_attention()
        reasons = [reason for entry in attention for reason in entry["reasons"]]
        assert not any("CAPTCHA" in reason or "MFA" in reason for reason in reasons)
    finally:
        service.close()


def test_opportunity_detail_shows_core_sections_and_timeline(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get(f"/opportunity/{ids['a']}")
        assert response.status_code == 200
        body = response.text
        assert "Acme" in body
        assert "APPLIED" in body
        assert "SCREENING" in body  # interview stage
        assert "ACCEPTED" in body  # offer decision
        assert "OPPORTUNITY_CREATED" in body  # timeline event
    finally:
        app.dependency_overrides.clear()


def test_opportunity_detail_never_fabricates_missing_sections(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get(f"/opportunity/{ids['c']}")
        assert response.status_code == 200
        body = response.text
        assert "No blockers recorded." in body
        assert "No recruiter/hiring-manager contact recorded." in body
        assert "No employer response recorded yet." in body
        assert "No interview recorded." in body
        assert "No offer recorded." in body
    finally:
        app.dependency_overrides.clear()


def test_opportunity_detail_404_for_unknown_tracker(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/opportunity/999999")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_pipeline_counts_include_zero_stages_and_current_stage_only(tmp_path):
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        pipeline = service.pipeline_counts()
        assert pipeline["HIRED"] == 1
        assert pipeline["DISCOVERED"] == 1  # opportunity c never left DISCOVERED
        assert pipeline["OFFER"] == 0  # opportunity a moved on to ACCEPTED then HIRED
        assert "REJECTED" in pipeline  # listed even at zero
    finally:
        service.close()


def test_conversion_rate_is_none_not_zero_for_undefined_denominator(tmp_path):
    service = _open(tmp_path / "empty.db")
    try:
        counts = service.funnel_counts()
        rates = service.conversion_rates(counts)
        assert rates["offer_to_hired"] is None
    finally:
        service.close()


def test_dashboard_reads_the_real_production_crm_not_fixture_data():
    """Task 21.33 section 7: the dashboard must read the real, existing
    production database -- never demo/fixture data. Read-only: makes no
    writes, so it is safe to run against the real app/data/application_history.db.

    crm_stage is asserted as APPLIED-or-later (not pinned to exactly
    APPLIED): Task 21.34's Gmail Outcome Monitoring has since legitimately
    advanced some of these real trackers past APPLIED (e.g. to
    ACKNOWLEDGED) from real employer correspondence -- that forward
    progress is the intended behavior, not a regression.
    """
    from app.models.crm import ACTIVE_FORWARD_ORDER

    applied_or_later = set(ACTIVE_FORWARD_ORDER[ACTIVE_FORWARD_ORDER.index("APPLIED"):])
    service = OpportunityCRMService()
    try:
        for tracker_id, expected_company in ((61, "Jobgether"), (103, "Jobgether"), (81, "Isla Health")):
            record = service.get_opportunity(tracker_id)
            assert record is not None, f"Tracker {tracker_id} missing from production CRM ({APPLICATION_HISTORY_DB})"
            assert expected_company in (record.get("company") or "")
            assert record["crm_stage"] in applied_or_later
            assert record["applied_at"]
    finally:
        service.close()


# --- Web App Phase 1: shared shell / navigation / corrected funnel ---------
def test_sidebar_lists_all_nine_approved_sections_and_marks_dashboard_active(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        for label in (
            "Dashboard", "Opportunities", "Applications", "Action Required", "Employer Inbox",
            "Interviews", "Analytics &amp; Learning", "Automation", "Settings",
        ):
            assert label in body
        # The Dashboard nav item is the one marked active on "/".
        dashboard_item = body.split('href="/">Dashboard')[0].rsplit("<li", 1)[-1]
        assert "is-active" in dashboard_item
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("path", [
    "/opportunities", "/applications", "/action-required", "/employer-inbox",
    "/interviews", "/analytics", "/automation", "/settings",
])
def test_every_placeholder_nav_route_renders_the_shared_shell(path):
    """No fabricated functionality -- each of the other 8 approved sections
    renders honestly as a placeholder inside the same shared shell."""
    client = TestClient(app)
    response = client.get(path)
    assert response.status_code == 200
    body = response.text
    assert "Coming in a later phase" in body
    assert "Career Intelligence" in body  # shared sidebar brand present


def test_current_pipeline_and_cumulative_funnel_are_presented_as_two_distinct_sections(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "Current Pipeline (live stage)" in body
        assert "Cumulative Funnel (ever reached)" in body
        # The corrected cumulative funnel and the mutually-exclusive current
        # pipeline must both be present, distinctly labeled -- never merged
        # back into one confusing table.
        assert body.index("Current Pipeline (live stage)") < body.index("Cumulative Funnel (ever reached)")
    finally:
        app.dependency_overrides.clear()


def test_kpi_row_distinguishes_acknowledgements_from_meaningful_responses(tmp_path):
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        # Fixture opportunity 'a' only ever received an ACKNOWLEDGEMENT --
        # add a genuinely meaningful response on 'b' so the two KPI figures
        # can differ and be told apart.
        service.record_employer_response(ids["b"], "RECRUITER_CONTACT")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "Acknowledgements" in body
        assert "Meaningful Responses" in body
        quality = _open(db_path).response_quality_counts()
        assert quality["acknowledgements"] == 1
        assert quality["meaningful_responses"] == 1
    finally:
        app.dependency_overrides.clear()


def test_recent_activity_does_not_leak_a_filtered_out_companys_name(tmp_path):
    """Regression guard: the Recent Activity feed must respect the active
    filter, never surfacing another opportunity's company on a filtered page.
    (Needs My Attention is deliberately global/unfiltered by existing design
    -- Beta Co, which has an open blocker, is expected to still appear
    there; only the Recent Activity section itself is checked here.)"""
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/?market=australia&source=Indeed").text
        assert "Gamma Ltd" in body
        activity_section = body.split("Recent Activity")[1]
        assert "Acme" not in activity_section
        assert "Beta Co" not in activity_section
    finally:
        app.dependency_overrides.clear()


def test_dashboard_home_reads_real_production_data_end_to_end():
    """Same real-data guarantee, exercised through the actual FastAPI route
    (default, un-overridden dependency) rather than the service directly.

    Unfiltered (not `crm_stage=APPLIED`): Task 21.34's Gmail Outcome
    Monitoring has since legitimately advanced some of these real trackers
    past APPLIED, so a strict APPLIED-stage filter would correctly, but
    misleadingly, exclude them -- the unfiltered table is the right check
    for "did the real production data load at all"."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    for tracker_id in (61, 103, 81):
        assert f"/opportunity/{tracker_id}\"" in body
