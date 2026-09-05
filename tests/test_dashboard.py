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
    sqlite3 connection cannot cross threads.

    Note: `create_opportunity`/`claim_job`'s column whitelist does not
    include `intelligence_priority` -- it must be set via a follow-up
    `update_opportunity` call, not passed to `_create` directly."""
    db_path = tmp_path / "history.db"
    history = ApplicationHistoryService(db_path)
    service = OpportunityCRMService(history)

    def _create(external_id, priority=None, **fields):
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id=external_id)
        record = service.create_opportunity(fingerprint, **fields)
        if priority:
            record = service.update_opportunity(record["id"], intelligence_priority=priority)
        return record

    a = _create("a", priority="A", company="Acme", job_title="Finance Manager", market="united_kingdom", source="LinkedIn")
    service.transition_stage(a["id"], "ELIGIBLE")
    service.transition_stage(a["id"], "SHORTLISTED")
    service.record_submission_confirmation(a["id"], confirmation_evidence="Applied tab confirms it", submission_reference="a1")
    service.record_employer_response(a["id"], "ACKNOWLEDGEMENT")
    service.record_interview(a["id"], "SCREENING")
    offer = service.record_offer(a["id"])
    service.record_offer_decision(offer["id"], "ACCEPTED")
    service.record_hire(a["id"])

    b = _create("b", priority="C", company="Beta Co", job_title="Head of Finance", market="united_states", source="LinkedIn")
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


# --- KPI row: reconciled, evidence-based figures ----------------------------
def test_kpi_row_uses_the_reconciled_evidence_based_figures(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "Total Opportunities" in body
        assert "<div class=\"n\">3</div>" in body  # 3 total opportunities
        assert "Applications Submitted" in body
        assert "Acknowledgements" in body
        assert "Meaningful Responses" in body
        assert "Interviews" in body
        assert "Offers" in body
        # No internal implementation labels in the executive view.
        assert "OpportunityCRMService" not in body
        assert "career_intelligence.py" not in body
    finally:
        app.dependency_overrides.clear()


def test_kpi_row_distinguishes_acknowledgements_from_meaningful_responses(tmp_path):
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        # Fixture opportunity 'a' only ever received an ACKNOWLEDGEMENT --
        # add a genuinely meaningful response on 'b' so the two figures can
        # differ and be told apart.
        service.record_submission_confirmation(ids["b"], confirmation_evidence="confirmed", submission_reference="b1")
        service.record_employer_response(ids["b"], "RECRUITER_CONTACT")
    finally:
        service.close()
    cumulative = _open(db_path).cumulative_funnel_counts()
    assert cumulative["ACKNOWLEDGED"] == 1
    assert cumulative["MEANINGFUL_RESPONSE"] == 1


# --- Application Performance: no misleading/undefined percentages ----------
def test_application_performance_shows_na_for_undefined_interview_to_offer(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "Applied &rarr; Acknowledged" in body
        assert "Applied &rarr; Meaningful Response" in body
        assert "Applied &rarr; Interview" in body
        assert "Interview &rarr; Offer" in body
    finally:
        app.dependency_overrides.clear()


# --- Current Pipeline groups reconcile exactly to the total -----------------
def test_current_pipeline_groups_sum_exactly_to_total_opportunities(tmp_path):
    db_path, _ = _seed(tmp_path)
    service = _open(db_path)
    try:
        groups = service.pipeline_group_counts()
        total = service.connection.execute("SELECT COUNT(*) FROM application_history").fetchone()[0]
        assert sum(g["count"] for g in groups) == total == 3
    finally:
        service.close()


def test_current_pipeline_and_cumulative_funnel_are_two_distinct_labeled_sections(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "Current Pipeline (live stage)" in body
        assert "Cumulative Funnel (ever reached)" in body
        assert body.index("Current Pipeline (live stage)") < body.index("Cumulative Funnel (ever reached)")
        # No raw internal stage codes shown as visible text in the grouped
        # pipeline view (a single-stage group's drill-down href legitimately
        # carries its raw crm_stage in the URL -- that's not user-facing text).
        assert "ELIGIBILITY_REVIEW" not in body  # a multi-stage group -- never linked, never shown
        assert ">READY_FOR_HUMAN_SUBMIT<" not in body
        assert 'href="/opportunities?crm_stage=READY_FOR_HUMAN_SUBMIT"' in body
    finally:
        app.dependency_overrides.clear()


# --- Priority mix: verified counts, includes UNSCORED -----------------------
def test_priority_mix_shows_verified_counts_including_unscored(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "Priority Apply" in body  # A
        assert "Human Review" in body  # C
        assert "Not Yet Evaluated" in body  # UNSCORED (opportunity 'c')
    finally:
        app.dependency_overrides.clear()
    mix = _open(db_path).priority_mix_counts()
    assert mix["A"] == 1
    assert mix["C"] == 1
    assert mix["UNSCORED"] == 1


def test_priority_mix_badges_link_to_the_opportunities_workspace(tmp_path):
    """Phase 2 navigation: Dashboard Priority Mix clicks go to the real
    Opportunities workspace, filtered to that priority -- not the
    Dashboard's own inline filtered view."""
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert 'href="/opportunities?intelligence_priority=A"' in body
        filtered = client.get("/opportunities?intelligence_priority=A").text
        assert "Acme" in filtered
        assert "Beta Co" not in filtered
    finally:
        app.dependency_overrides.clear()


# --- Needs My Attention: compact, plain language, priority chips -----------
def test_needs_attention_is_compact_and_plain_language(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "Salary needs your review" in body  # plain language, not the raw blocker code
        assert "Confirm min rate" in body
        # The hired opportunity (a) is not "needing attention".
        attention_section = body.split("Needs My Attention")[1].split("Opportunities</h2>")[0]
        assert "Acme" not in attention_section
        # Priority filter chips are present.
        assert 'href="/?attn_priority=A"' in body
        assert 'href="/?attn_priority=C"' in body
    finally:
        app.dependency_overrides.clear()


def test_needs_attention_priority_filter_narrows_the_list(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/?attn_priority=C").text
        assert "Beta Co" in body
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


def test_needs_attention_view_all_link_appears_only_when_more_than_five(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    service = OpportunityCRMService(history)
    try:
        for i in range(7):
            fingerprint = job_fingerprint(source="LinkedIn", external_job_id=f"attn-{i}")
            record = service.create_opportunity(fingerprint, company=f"Co{i}", job_title="Role")
            service.transition_stage(record["id"], "ELIGIBILITY_REVIEW")
    finally:
        service.close()
    try:
        client = _client(tmp_path / "history.db")
        body = client.get("/").text
        assert "View All (7)" in body
        full = client.get("/?show_all_attention=1").text
        assert "Show fewer" in full
    finally:
        app.dependency_overrides.clear()


# --- Opportunities section: compact snapshot, no full register -------------
def test_opportunities_section_shows_compact_snapshot_by_default(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "A/B Priority" in body
        assert "Human Review" in body
        assert "Watch / Reject" in body
        assert "View Opportunities" in body
        # The old full opportunities register (with filter dropdowns) is gone.
        assert "<select name=\"crm_stage\">" not in body
        assert "<select name=\"market\">" not in body
    finally:
        app.dependency_overrides.clear()


def test_opportunities_section_shows_filtered_list_when_a_filter_is_active(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/?crm_stage=ELIGIBILITY_REVIEW").text
        assert "Beta Co" in body
        assert "Clear filter" in body
        opportunities_section = body.split("<h2 class=\"section-title\">Opportunities</h2>")[1].split("Recent Activity")[0]
        assert "Acme" not in opportunities_section
        assert "Gamma Ltd" not in opportunities_section
    finally:
        app.dependency_overrides.clear()


def test_opportunities_empty_filter_result_shows_empty_state(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/?crm_stage=HIRED&intelligence_priority=E")
        assert response.status_code == 200
        assert "No opportunities match this filter." in response.text
    finally:
        app.dependency_overrides.clear()


# --- Recent Activity: max 5, business-relevant only -------------------------
def test_recent_activity_shows_business_events_not_raw_transitions(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        activity_section = body.split("Recent Activity")[1]
        # 'a' hits many milestones in sequence -- only the 5 MOST RECENT
        # show by default, so "Hired" (the last real thing that happened)
        # is guaranteed present; earlier milestones for the same tracker
        # may legitimately be pushed out by more recent ones.
        assert "Hired" in activity_section
        # No raw MIGRATED_STAGE/OPPORTUNITY_CREATED technical event labels,
        # and no duplicate "Hired"/"Offer..." from the redundant
        # STAGE_TRANSITION fired alongside each dedicated domain event.
        assert "OPPORTUNITY_CREATED" not in activity_section
        assert "MIGRATED_STAGE" not in activity_section
        assert activity_section.count("Hired") == 1
        assert activity_section.count("Offer accepted") == 1
    finally:
        app.dependency_overrides.clear()


def test_recent_activity_shows_acknowledgement_when_it_is_the_most_recent_event(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    service = OpportunityCRMService(history)
    try:
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id="ack-only")
        record = service.create_opportunity(fingerprint, company="Delta Inc", job_title="Analyst")
        service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
        service.record_employer_response(record["id"], "ACKNOWLEDGEMENT")
    finally:
        service.close()
    try:
        client = _client(tmp_path / "history.db")
        body = client.get("/").text
        activity_section = body.split("Recent Activity")[1]
        assert "Acknowledgement received" in activity_section
        assert activity_section.count("Acknowledgement received") == 1  # not duplicated by the paired STAGE_TRANSITION
    finally:
        app.dependency_overrides.clear()


def test_recent_activity_defaults_to_at_most_five_items(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    service = OpportunityCRMService(history)
    try:
        for i in range(8):
            fingerprint = job_fingerprint(source="LinkedIn", external_job_id=f"act-{i}")
            record = service.create_opportunity(fingerprint, company=f"Co{i}", job_title="Role")
            service.transition_stage(record["id"], "SHORTLISTED")
    finally:
        service.close()
    try:
        client = _client(tmp_path / "history.db")
        body = client.get("/").text
        activity_section = body.split("Recent Activity")[1]
        assert activity_section.count("Opportunity shortlisted") <= 5
        assert "View All Activity" in body
    finally:
        app.dependency_overrides.clear()


def test_recent_activity_does_not_leak_a_filtered_out_companys_name(tmp_path):
    """Needs My Attention and Recent Activity are both global/unfiltered by
    design -- only the Opportunities section itself responds to the active
    filter. This guards that Recent Activity's own global scope is at least
    limited to genuinely recent, business-relevant events (not the entire
    unfiltered history dumped onto a filtered page)."""
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/?crm_stage=ELIGIBILITY_REVIEW").text
        assert "Beta Co" in body  # present via the filtered Opportunities section
    finally:
        app.dependency_overrides.clear()


# --- Opportunity detail page (unchanged content, still shell-wrapped) ------
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


# --- CRM service-level tests (unchanged methods) ----------------------------
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


# --- Web App Phase 1: shared shell / navigation -----------------------------
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
        dashboard_item = body.split('href="/">Dashboard')[0].rsplit("<li", 1)[-1]
        assert "is-active" in dashboard_item
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("path", [
    "/applications", "/action-required", "/employer-inbox",
    "/interviews", "/analytics", "/automation", "/settings",
])
def test_every_placeholder_nav_route_renders_the_shared_shell(path):
    """No fabricated functionality -- each of the remaining 7 approved
    sections (Opportunities is now real, Phase 2) renders honestly as a
    placeholder inside the same shared shell."""
    client = TestClient(app)
    response = client.get(path)
    assert response.status_code == 200
    body = response.text
    assert "Coming in a later phase" in body
    assert "Career Intelligence" in body  # shared sidebar brand present


def test_topbar_never_shows_a_raw_cli_command(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "career_intelligence.py" not in body
    finally:
        app.dependency_overrides.clear()


# --- Real production data guarantees -----------------------------------
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


def test_dashboard_home_reads_real_production_data_end_to_end():
    """Same real-data guarantee, exercised through the actual FastAPI route
    (default, un-overridden dependency) rather than the service directly.
    The 3 real trackers surface via the KPI/pipeline/priority-mix figures
    reconciling to the real total, not via a full opportunities register
    (removed in Phase 1.1) -- so this checks the reconciled totals instead
    of scanning the page for specific tracker links."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert response.status_code == 200

    service = OpportunityCRMService()
    try:
        total = service.cumulative_funnel_counts()["DISCOVERED"]
        groups = service.pipeline_group_counts()
    finally:
        service.close()
    assert f"{total} opportunities tracked" in body
    assert sum(g["count"] for g in groups) == total


# --- Web App Phase 2: Opportunities workspace -------------------------------
def test_opportunities_page_is_a_real_workspace_not_a_placeholder(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/opportunities")
        assert response.status_code == 200
        body = response.text
        assert "Coming in a later phase" not in body
        assert "Acme" in body
        assert "Beta Co" in body
        assert "Gamma Ltd" in body
    finally:
        app.dependency_overrides.clear()


def test_opportunities_summary_counts_reconcile_to_total(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/opportunities").text
        assert "<div class=\"n\">3</div>" in body  # Total Opportunities
    finally:
        app.dependency_overrides.clear()
    mix = _open(db_path).priority_mix_counts()
    assert sum(mix.values()) == 3


def test_opportunities_search_filters_by_company_or_title(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/opportunities?search=Acme").text
        assert "Acme" in body
        assert "Beta Co" not in body
        assert "Gamma Ltd" not in body
    finally:
        app.dependency_overrides.clear()


def test_opportunities_filters_are_combinable(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        # Priority C AND market=united_states -- only Beta Co matches both.
        body = client.get("/opportunities?intelligence_priority=C&market=united_states").text
        assert "Beta Co" in body
        assert "Acme" not in body
        assert "Gamma Ltd" not in body
    finally:
        app.dependency_overrides.clear()


def test_opportunities_unscored_filter_shows_only_unevaluated_records(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/opportunities?intelligence_priority=UNSCORED").text
        assert "Gamma Ltd" in body  # 'c' was never scored
        assert "Acme" not in body
        assert "Beta Co" not in body
    finally:
        app.dependency_overrides.clear()


def test_opportunities_empty_result_shows_empty_state_and_zero_count(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/opportunities?search=NoSuchCompanyXYZ")
        assert response.status_code == 200
        assert "No opportunities match this filter." in response.text
        assert "0 opportunities match this filter." in response.text
    finally:
        app.dependency_overrides.clear()


def test_opportunities_are_paginated(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    service = OpportunityCRMService(history)
    try:
        for i in range(30):
            fingerprint = job_fingerprint(source="LinkedIn", external_job_id=f"opp-{i}")
            service.create_opportunity(fingerprint, company=f"Co{i}", job_title="Role")
    finally:
        service.close()
    try:
        client = _client(tmp_path / "history.db")
        page1 = client.get("/opportunities?page_size=10").text
        assert "Page 1 of 3" in page1
        page2 = client.get("/opportunities?page_size=10&page=2").text
        assert "Page 2 of 3" in page2
    finally:
        app.dependency_overrides.clear()


def test_opportunities_rows_link_to_detail_page(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/opportunities").text
        assert f'href="/opportunity/{ids["a"]}"' in body
    finally:
        app.dependency_overrides.clear()


def test_opportunities_default_order_surfaces_priority_over_tracker_id(tmp_path):
    """Default ordering emphasizes actionable/high-value opportunities
    (priority rank) rather than raw tracker id."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    service = OpportunityCRMService(history)
    try:
        low = service.create_opportunity(job_fingerprint(source="LinkedIn", external_job_id="ord-low"), company="LowCo", job_title="Role")
        service.update_opportunity(low["id"], intelligence_priority="E")
        high = service.create_opportunity(job_fingerprint(source="LinkedIn", external_job_id="ord-high"), company="HighCo", job_title="Role")
        service.update_opportunity(high["id"], intelligence_priority="A")
    finally:
        service.close()
    try:
        client = _client(tmp_path / "history.db")
        body = client.get("/opportunities").text
        assert body.index("HighCo") < body.index("LowCo")
    finally:
        app.dependency_overrides.clear()


# --- Opportunity Detail: investment-memo structure -------------------------
def test_detail_page_shows_investment_memo_header_and_open_job_link(tmp_path):
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        service.update_opportunity(ids["a"], job_url="https://example.com/jobs/finance-manager")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get(f"/opportunity/{ids['a']}").text
        assert "Open Original Job" in body
        assert 'href="https://example.com/jobs/finance-manager"' in body
        assert "Recommendation:" in body
        assert "Intelligence Assessment" in body
        assert "Opportunity Value" in body
        assert "Candidate Competitiveness" in body
        assert "Application Alignment" in body
    finally:
        app.dependency_overrides.clear()


def test_detail_page_hides_open_job_link_when_no_url_exists(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get(f"/opportunity/{ids['c']}").text
        assert "Open Original Job" not in body
    finally:
        app.dependency_overrides.clear()


def test_eligibility_matrix_distinguishes_unknown_from_fail(tmp_path):
    """Frozen policy: a remote vacancy silent on overseas eligibility is
    HUMAN REVIEW / UNKNOWN, never automatically FAIL."""
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        # opportunity 'c' has no remote_eligibility, no intelligence_priority set at all.
        body = client.get(f"/opportunity/{ids['c']}").text
        assert 'status-pill UNKNOWN">UNKNOWN</span>' in body
        assert 'status-pill FAIL">FAIL</span>' not in body
    finally:
        app.dependency_overrides.clear()


def test_eligibility_matrix_shows_review_not_fail_for_manual_review(tmp_path):
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        service.update_opportunity(ids["b"], remote_eligibility="MANUAL_REVIEW")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get(f"/opportunity/{ids['b']}").text
        assert "Remote vacancy is silent on overseas eligibility" in body
        matrix_section = body.split("Eligibility Matrix")[1].split("My Decision")[0]
        assert "REVIEW" in matrix_section
    finally:
        app.dependency_overrides.clear()


def test_eligibility_matrix_shows_fail_for_explicit_ineligibility(tmp_path):
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        service.update_opportunity(ids["b"], remote_eligibility="INELIGIBLE")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get(f"/opportunity/{ids['b']}").text
        assert "explicit work-right/residency restriction" in body
    finally:
        app.dependency_overrides.clear()


def test_opportunity_history_shows_plain_language_not_raw_event_codes(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get(f"/opportunity/{ids['a']}").text
        history_section = body.split("Opportunity History")[1].split("Technical Details")[0]
        assert "Hired" in history_section
        assert "STAGE_TRANSITION" not in history_section
        assert "MIGRATED_STAGE" not in history_section
    finally:
        app.dependency_overrides.clear()


# --- My Decision: auditable, never alters intelligence priority ------------
def test_recording_a_decision_persists_and_never_changes_priority(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        before = _open(db_path).get_opportunity(ids["b"])
        response = client.post(f"/opportunity/{ids['b']}/decision", data={"decision": "WATCH", "reason_code": "LOCATION", "note": "Too far"}, follow_redirects=False)
        assert response.status_code == 303
        after = _open(db_path).get_opportunity(ids["b"])
        assert after["intelligence_priority"] == before["intelligence_priority"]
        assert after["crm_stage"] == before["crm_stage"]

        detail_body = client.get(f"/opportunity/{ids['b']}").text
        assert "WATCH" in detail_body
        assert "Location" in detail_body
        assert "Too far" in detail_body
    finally:
        app.dependency_overrides.clear()


def test_decision_history_is_append_only_across_multiple_submissions(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        client.post(f"/opportunity/{ids['c']}/decision", data={"decision": "WATCH"}, follow_redirects=False)
        client.post(f"/opportunity/{ids['c']}/decision", data={"decision": "APPLY"}, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        history = service.list_user_decisions(ids["c"])
        assert len(history) == 2
        assert history[0]["decision"] == "APPLY"  # most recent first, never overwritten
    finally:
        service.close()


def test_invalid_decision_input_does_not_crash_or_corrupt_state(tmp_path):
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        response = client.post(f"/opportunity/{ids['c']}/decision", data={"decision": "NOT_A_REAL_DECISION"}, follow_redirects=False)
        assert response.status_code == 303  # redirected, never a 500
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        assert service.list_user_decisions(ids["c"]) == []
    finally:
        service.close()


# --- Task Phase 2 item 13: acknowledgement duplication investigation -------
def test_recent_activity_collapses_repeated_real_acknowledgements_with_a_count(tmp_path):
    """Two genuinely separate real Gmail acknowledgement messages for the
    SAME opportunity must not read as two near-identical activity lines --
    the underlying employer_responses rows are both preserved untouched;
    only the Recent Activity feed's presentation collapses them."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    service = OpportunityCRMService(history)
    try:
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id="dup-ack")
        record = service.create_opportunity(fingerprint, company="Isla Health", job_title="Finance Manager")
        service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
        service.record_employer_response(record["id"], "ACKNOWLEDGEMENT", evidence_reference="gmail-msg-1")
        service.record_employer_response(record["id"], "ACKNOWLEDGEMENT", evidence_reference="gmail-msg-2")
        # Both real messages are preserved, untouched, as separate evidence.
        assert len(service.get_opportunity_detail(record["id"])["employer_responses"]) == 2
    finally:
        service.close()
    try:
        client = _client(tmp_path / "history.db")
        body = client.get("/").text
        activity_section = body.split("Recent Activity")[1]
        assert activity_section.count("Acknowledgement received") == 1  # collapsed presentation
        assert "(2x)" in activity_section
    finally:
        app.dependency_overrides.clear()


def test_opportunity_history_also_collapses_repeated_acknowledgements(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    service = OpportunityCRMService(history)
    try:
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id="dup-ack-2")
        record = service.create_opportunity(fingerprint, company="Isla Health", job_title="Finance Manager")
        service.record_submission_confirmation(record["id"], confirmation_evidence="confirmed", submission_reference="s1")
        service.record_employer_response(record["id"], "ACKNOWLEDGEMENT", evidence_reference="gmail-msg-1")
        service.record_employer_response(record["id"], "ACKNOWLEDGEMENT", evidence_reference="gmail-msg-2")
        tracker_id = record["id"]
    finally:
        service.close()
    try:
        client = _client(tmp_path / "history.db")
        body = client.get(f"/opportunity/{tracker_id}").text
        history_section = body.split("Opportunity History")[1].split("Technical Details")[0]
        assert history_section.count("Acknowledgement received") == 1
        assert "(2x)" in history_section
    finally:
        app.dependency_overrides.clear()


def test_opportunities_reads_real_production_data():
    client = TestClient(app)
    response = client.get("/opportunities")
    assert response.status_code == 200
    body = response.text
    service = OpportunityCRMService()
    try:
        total = sum(service.priority_mix_counts().values())
    finally:
        service.close()
    assert f"{total} opportunities tracked" in body
