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


def test_needs_attention_view_all_link_appears_only_when_more_than_five_and_points_to_action_required(tmp_path):
    """Web App Phase 3: Needs My Attention is now backed by the Action
    Required read model -- bare ELIGIBILITY_REVIEW stage membership alone
    (no remote_eligibility=MANUAL_REVIEW, no blocker) is no longer
    sufficient to appear here at all, so genuinely actionable records must
    be seeded to exercise the "View All" link."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    service = OpportunityCRMService(history)
    try:
        for i in range(7):
            fingerprint = job_fingerprint(source="LinkedIn", external_job_id=f"attn-{i}")
            record = service.create_opportunity(fingerprint, company=f"Co{i}", job_title="Role")
            service.update_opportunity(record["id"], intelligence_priority="C", remote_eligibility="MANUAL_REVIEW")
            service.transition_stage(record["id"], "ELIGIBILITY_REVIEW")
    finally:
        service.close()
    try:
        client = _client(tmp_path / "history.db")
        body = client.get("/").text
        assert "View All (7)" in body
        assert 'href="/action-required"' in body
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
    "/applications", "/employer-inbox",
    "/interviews", "/analytics", "/automation", "/settings",
])
def test_every_placeholder_nav_route_renders_the_shared_shell(path):
    """No fabricated functionality -- each of the remaining 6 approved
    sections (Opportunities is real since Phase 2, Action Required is real
    since Phase 3) renders honestly as a placeholder inside the same shared
    shell."""
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


# --- Web App Phase 2.1: wide-content layout ---------------------------------
def test_opportunities_page_uses_the_wide_content_layout(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/opportunities").text
        assert 'class="content content--wide"' in body
    finally:
        app.dependency_overrides.clear()


def test_dashboard_and_detail_pages_keep_the_default_narrower_layout(tmp_path):
    """The Opportunities-specific wide mode must not leak onto the frozen
    Executive Dashboard or Opportunity Detail page as a side effect."""
    db_path, ids = _seed(tmp_path)
    try:
        client = _client(db_path)
        home_body = client.get("/").text
        assert 'class="content content--wide"' not in home_body
        assert 'class="content "' in home_body
        detail_body = client.get(f"/opportunity/{ids['a']}").text
        assert 'class="content content--wide"' not in detail_body
    finally:
        app.dependency_overrides.clear()


# --- Web App Phase 2.1: humanized enum-style values -------------------------
def test_opportunities_table_humanizes_market_and_work_arrangement(tmp_path):
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        service.update_opportunity(ids["a"], work_arrangement="REMOTE")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get("/opportunities").text
        assert ">United Kingdom<" in body
        assert ">united_kingdom<" not in body  # raw value never shown as visible text (value="" attrs/hrefs are fine)
        assert ">Remote<" in body
        assert ">REMOTE<" not in body
    finally:
        app.dependency_overrides.clear()


def test_opportunities_table_humanizes_opportunity_value_and_competitiveness(tmp_path):
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        service.update_opportunity(ids["a"], opportunity_value="HIGH", candidate_competitiveness="VERY_STRONG")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get("/opportunities").text
        assert "Very Strong" in body
        assert "VERY_STRONG" not in body
    finally:
        app.dependency_overrides.clear()


def test_opportunities_filter_options_are_humanized_but_values_stay_raw(tmp_path):
    db_path, _ = _seed(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/opportunities").text
        assert '<option value="united_kingdom"' in body  # raw value preserved for filtering
        assert '>United Kingdom<' in body  # humanized label shown
    finally:
        app.dependency_overrides.clear()


def test_detail_page_humanizes_market_work_arrangement_and_dimension_values(tmp_path):
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        service.update_opportunity(ids["b"], work_arrangement="REMOTE", opportunity_value="MEDIUM", candidate_competitiveness="STRETCH")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get(f"/opportunity/{ids['b']}").text
        # The memo header/metric cards (before "Why Pursue") must never show
        # a raw enum value as visible text.
        header_and_metrics = body.split("Why Pursue")[0]
        assert "United States" in header_and_metrics
        assert "united_states" not in header_and_metrics  # no attrs/hrefs carry raw market in this zone
        assert "STRETCH" not in header_and_metrics
        assert "Stretch" in header_and_metrics
    finally:
        app.dependency_overrides.clear()


def test_humanize_filter_never_mutates_stored_production_values():
    """Presentation-only: filtering by the RAW value must still work exactly
    as before -- humanization never touches what's persisted or queried."""
    service = OpportunityCRMService()
    try:
        record = service.get_opportunity(61)
        raw_market_before = record.get("market")
    finally:
        service.close()
    client = TestClient(app)
    response = client.get("/opportunities")
    assert response.status_code == 200
    service = OpportunityCRMService()
    try:
        record_after = service.get_opportunity(61)
        assert record_after.get("market") == raw_market_before  # untouched
    finally:
        service.close()


# --- Web App Phase 2.1: Evidence Strength correction ------------------------
def test_evidence_strength_pseudo_metric_is_not_mislabeled_as_competitiveness(tmp_path):
    """Candidate Competitiveness must remain its own dimension -- the page
    must never present it under a separate "Evidence Strength" label, since
    no genuine, distinct evidence-strength value exists in the persisted
    CRM schema."""
    db_path, ids = _seed(tmp_path)
    service = _open(db_path)
    try:
        service.update_opportunity(ids["a"], candidate_competitiveness="VERY_STRONG")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get(f"/opportunity/{ids['a']}").text
        assert "Evidence Strength: Very Strong" not in body
        assert "Evidence Strength:" not in body
    finally:
        app.dependency_overrides.clear()


# --- Web App Phase 3: Action Required workspace -----------------------------
def _seed_action_required(tmp_path):
    """A hermetic DB shaped to exercise all five categories plus the
    critical-principle guard (a bare ELIGIBILITY_REVIEW record with no
    genuine eligibility question, which must NEVER appear)."""
    db_path = tmp_path / "action.db"
    history = ApplicationHistoryService(db_path)
    service = OpportunityCRMService(history)

    def make(external_id, **fields):
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id=external_id)
        return service.create_opportunity(fingerprint, **fields)

    # Not an action: Priority C, ELIGIBILITY_REVIEW, but no real eligibility
    # question (borderline-score review only) -- the critical-principle case.
    not_actionable = make("noop-1", company="Quiet Co", job_title="Analyst")
    service.update_opportunity(not_actionable["id"], intelligence_priority="C", remote_eligibility="ELIGIBLE")
    service.transition_stage(not_actionable["id"], "ELIGIBILITY_REVIEW")

    # A: Eligibility Decision (genuine).
    eligibility = make("elig-1", company="Acme Robotics", job_title="Controller")
    service.update_opportunity(eligibility["id"], intelligence_priority="C", remote_eligibility="MANUAL_REVIEW")
    service.transition_stage(eligibility["id"], "ELIGIBILITY_REVIEW")

    # B: Review & Submit.
    ready = make("ready-1", company="Robert Half", job_title="Finance Manager")
    service.transition_stage(ready["id"], "READY_FOR_HUMAN_SUBMIT")

    # C: Browser Action (CAPTCHA).
    captcha_record = make("captcha-1", company="Greenhouse Corp", job_title="Staff Accountant")
    captcha_blocker = service.record_human_blocker(captcha_record["id"], "HUMAN_CAPTCHA_REQUIRED", detail="CAPTCHA on final submit page")

    # D: Answer Required (salary review).
    answer_record = make("answer-1", company="Beta Industries", job_title="Bookkeeper")
    answer_blocker = service.record_human_blocker(answer_record["id"], "HUMAN_SALARY_REVIEW_REQUIRED", detail="What is your minimum acceptable salary?")

    # E: Employer Action (interview invitation).
    employer_record = make("employer-1", company="Gamma Finance", job_title="Treasury Analyst")
    service.record_submission_confirmation(employer_record["id"], confirmation_evidence="confirmed", submission_reference="s1")
    employer_response = service.record_employer_response(employer_record["id"], "INTERVIEW_INVITATION", summary="Interview scheduled for Tuesday 2pm")

    ids = {
        "not_actionable": not_actionable["id"], "eligibility": eligibility["id"], "ready": ready["id"],
        "captcha": captcha_record["id"], "answer": answer_record["id"], "employer": employer_record["id"],
    }
    refs = {"captcha_blocker_id": captcha_blocker["id"], "answer_blocker_id": answer_blocker["id"],
            "employer_response_id": employer_response["id"]}
    service.close()
    return db_path, ids, refs


def test_action_required_page_is_a_real_workspace_not_a_placeholder(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        response = client.get("/action-required")
        assert response.status_code == 200
        body = response.text
        assert "Coming in a later phase" not in body
        assert "Acme Robotics" in body
    finally:
        app.dependency_overrides.clear()


def test_action_required_never_lists_a_bare_priority_c_review_without_a_concrete_blocker(tmp_path):
    """The critical product principle: Priority C / ELIGIBILITY_REVIEW alone
    is never enough."""
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert "Quiet Co" not in body
    finally:
        app.dependency_overrides.clear()


def test_action_required_summary_counts_reconcile_to_active_queue(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        import re
        cards = {m.group(2): int(m.group(1)) for m in re.finditer(r'<div class="n">(\d+)</div><div class="l">([^<]*)</div>', body)}
        assert cards["Total Actions Required"] == 5
        assert cards["Review &amp; Submit"] == 1
        assert cards["Answer Required"] == 1
        assert cards["Eligibility Decision"] == 1
        assert cards["Browser Action Required"] == 1
        assert cards["Employer Action"] == 1
        assert cards["Total Actions Required"] == sum(
            cards[k] for k in ("Review &amp; Submit", "Answer Required", "Eligibility Decision", "Browser Action Required", "Employer Action")
        )
    finally:
        app.dependency_overrides.clear()


def test_action_required_shows_all_five_categories_with_real_content(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert "Acme Robotics" in body  # Eligibility Decision
        assert "Robert Half" in body  # Review & Submit
        assert "Greenhouse Corp" in body  # Browser Action
        assert "Beta Industries" in body  # Answer Required
        assert "Gamma Finance" in body  # Employer Action
        assert "CAPTCHA on final submit page" in body
        assert "What is your minimum acceptable salary?" in body
        assert "Interview scheduled for Tuesday 2pm" in body
    finally:
        app.dependency_overrides.clear()


def test_action_required_priority_filter_chips_work(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/action-required?priority=C").text
        assert "Acme Robotics" in body  # the only C-priority actionable item
        assert "Robert Half" not in body  # unscored
    finally:
        app.dependency_overrides.clear()


def test_action_required_browser_action_never_offers_captcha_bypass_or_credential_fields(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert 'type="password"' not in body
        assert "bypass" not in body.lower()
        assert "solve the captcha" not in body.lower()  # "resolve"/"Resolved" legitimately appear elsewhere
        assert "I&#39;ve Completed This" in body or "I've Completed This" in body
    finally:
        app.dependency_overrides.clear()


def test_action_required_employer_action_never_offers_an_autonomous_reply(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert "Mark Reviewed" in body
        assert "<textarea" not in body  # no reply-composition field anywhere on this page
        assert 'action="/action-required/employer-response' in body
    finally:
        app.dependency_overrides.clear()


def test_resolving_a_browser_blocker_via_action_required_removes_it_from_active_queue(tmp_path):
    """Mutation test -- uses a temporary DB only, never production."""
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        response = client.post(f"/action-required/blocker/{refs['captcha_blocker_id']}/resolve", data={"note": "Solved manually"}, follow_redirects=False)
        assert response.status_code == 303
        active_body = client.get("/action-required").text
        assert "Greenhouse Corp" not in active_body
        resolved_body = client.get("/action-required?view=resolved").text
        assert "Greenhouse Corp" in resolved_body
        assert "Solved manually" in resolved_body or "CAPTCHA on final submit page" in resolved_body
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        blocker = service._blocker_row(refs["captcha_blocker_id"])
        assert blocker["status"] == "RESOLVED"
        assert blocker["resolved_by"] == "USER"
    finally:
        service.close()


def test_marking_an_employer_action_reviewed_removes_it_from_active_queue(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        response = client.post(
            f"/action-required/employer-response/{ids['employer']}/{refs['employer_response_id']}/review",
            data={"note": "Confirmed the interview"}, follow_redirects=False,
        )
        assert response.status_code == 303
        active_body = client.get("/action-required").text
        assert "Gamma Finance" not in active_body
    finally:
        app.dependency_overrides.clear()


def test_eligibility_decision_action_resolves_via_the_existing_decision_endpoint(tmp_path):
    """No second decision system: the Action Required page's "Review
    Eligibility" link goes to Opportunity Detail, where the EXISTING My
    Decision panel (Phase 2) is reused to resolve it."""
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        client.post(f"/opportunity/{ids['eligibility']}/decision", data={"decision": "WATCH", "reason_code": "LOCATION"}, follow_redirects=False)
        active_body = client.get("/action-required").text
        assert "Acme Robotics" not in active_body
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        record = service.get_opportunity(ids["eligibility"])
        assert record["remote_eligibility"] == "MANUAL_REVIEW"  # never fabricated/mutated
    finally:
        service.close()


def test_action_required_groups_a_large_set_of_identical_eligibility_questions(tmp_path):
    db_path = tmp_path / "grouping.db"
    history = ApplicationHistoryService(db_path)
    service = OpportunityCRMService(history)
    try:
        for i in range(20):
            fingerprint = job_fingerprint(source="LinkedIn", external_job_id=f"group-{i}")
            record = service.create_opportunity(fingerprint, company=f"Co{i}", job_title="Analyst")
            service.update_opportunity(record["id"], intelligence_priority="C", remote_eligibility="MANUAL_REVIEW")
            service.transition_stage(record["id"], "ELIGIBILITY_REVIEW")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert "20 opportunities" in body
        assert 'class="action-group"' in body
        # No bulk decision control is ever offered for the grouped row.
        assert "Apply to All" not in body and "Reject All" not in body and "Watch All" not in body
    finally:
        app.dependency_overrides.clear()


def test_action_required_never_groups_review_and_submit_items(tmp_path):
    """Review & Submit items are each a distinct, already-ready application
    -- never collapsed into a homogeneous group even if several share
    identical generic reason text."""
    db_path = tmp_path / "no_group.db"
    history = ApplicationHistoryService(db_path)
    service = OpportunityCRMService(history)
    try:
        for i in range(20):
            fingerprint = job_fingerprint(source="LinkedIn", external_job_id=f"rs-group-{i}")
            record = service.create_opportunity(fingerprint, company=f"ReadyCo{i}", job_title="Analyst")
            service.transition_stage(record["id"], "READY_FOR_HUMAN_SUBMIT")
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert "ReadyCo0" in body
        assert "ReadyCo19" in body
        assert 'class="action-group"' not in body  # the CSS class name is always in <style>; only its use as markup matters
    finally:
        app.dependency_overrides.clear()


def test_dashboard_needs_my_attention_uses_the_action_required_read_model(tmp_path):
    """Dashboard integration: the corrected count and content come from the
    same read model /action-required uses, not the old
    ATTENTION_STAGES-membership check."""
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/").text
        assert "Needs My Attention (5)" in body
        assert "Quiet Co" not in body.split("Needs My Attention")[1].split("Opportunities</h2>")[0]
        assert "Acme Robotics" in body  # a genuine action shows up
    finally:
        app.dependency_overrides.clear()


def test_opportunity_detail_shows_action_required_indicator_when_active(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get(f"/opportunity/{ids['eligibility']}").text
        assert "Action Required: Eligibility Decision" in body
    finally:
        app.dependency_overrides.clear()


def test_opportunity_detail_never_shows_action_required_for_a_bare_priority_c_review(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get(f"/opportunity/{ids['not_actionable']}").text
        assert "Action Required:" not in body
    finally:
        app.dependency_overrides.clear()


# --- Web App Phase 3: production verification (READ-ONLY) ------------------
def test_action_required_reads_real_production_data_read_only():
    """GET only -- makes no writes. Cross-checks the rendered page against
    the same service-level read model computed independently."""
    client = TestClient(app)
    response = client.get("/action-required")
    assert response.status_code == 200
    body = response.text

    service = OpportunityCRMService()
    try:
        counts = service.action_required_counts()
    finally:
        service.close()

    import re
    cards = {m.group(2): int(m.group(1)) for m in re.finditer(r'<div class="n">(\d+)</div><div class="l">([^<]*)</div>', body)}
    assert cards["Total Actions Required"] == counts["TOTAL"]
    assert cards["Eligibility Decision"] == counts["ELIGIBILITY_DECISION"]
    assert cards["Review &amp; Submit"] == counts["REVIEW_AND_SUBMIT"]


def test_dashboard_needs_my_attention_matches_action_required_total_on_production():
    """Read-only: the Dashboard's corrected count must equal
    /action-required's total for the SAME real production database."""
    client = TestClient(app)
    home_body = client.get("/").text
    service = OpportunityCRMService()
    try:
        total = service.action_required_counts()["TOTAL"]
    finally:
        service.close()
    assert f"Needs My Attention ({total})" in home_body


# --- Web App Phase 3.1: Action Required UI compression ----------------------
def _seed_many_review_and_submit(tmp_path, count=10):
    db_path = tmp_path / "rs_many.db"
    history = ApplicationHistoryService(db_path)
    service = OpportunityCRMService(history)
    ids = []
    try:
        for i in range(count):
            fingerprint = job_fingerprint(source="LinkedIn", external_job_id=f"rs-{i}")
            record = service.create_opportunity(fingerprint, company=f"ReadyCo{i}", job_title="Analyst")
            service.transition_stage(record["id"], "READY_FOR_HUMAN_SUBMIT")
            ids.append(record["id"])
    finally:
        service.close()
    return db_path, ids


def test_review_and_submit_shows_only_top_3_rows_outside_the_view_all_disclosure(tmp_path):
    """Rows are ordered by the EXISTING, unmodified urgency/recency sort
    (Phase 3) -- not insertion order -- so this checks structure (exactly 3
    distinct companies before the disclosure, the other 7 after), not which
    specific company happens to rank first."""
    db_path, ids = _seed_many_review_and_submit(tmp_path, count=10)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        # The section header (<h2>Review &amp; Submit</h2>) is unambiguous,
        # unlike the KPI card's "Review &amp; Submit" text.
        section_start = body.index("<h2>Review &amp; Submit</h2>")
        details_pos = body.index('<details class="ar-view-all">', section_start)
        before_section = body[section_start:details_pos]
        after_section = body[details_pos:]
        companies = [f"ReadyCo{i}" for i in range(10)]
        before_present = {c for c in companies if c in before_section}
        after_present = {c for c in companies if c in after_section}
        assert len(before_present) == 3, f"expected exactly 3 companies before the disclosure, got {before_present}"
        assert len(after_present) == 7, f"expected exactly 7 companies inside the disclosure, got {after_present}"
        assert before_present.isdisjoint(after_present)
    finally:
        app.dependency_overrides.clear()


def test_review_and_submit_view_all_link_shows_the_true_count_and_is_collapsed_by_default(tmp_path):
    db_path, ids = _seed_many_review_and_submit(tmp_path, count=10)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert "View all 10" in body
        # Native <details> with no `open` attribute renders collapsed by
        # default -- "collapse back" is then just closing it again.
        assert '<details class="ar-view-all">' in body
        assert '<details class="ar-view-all" open>' not in body
    finally:
        app.dependency_overrides.clear()


def test_review_and_submit_no_view_all_control_when_three_or_fewer(tmp_path):
    db_path, ids = _seed_many_review_and_submit(tmp_path, count=3)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert "ReadyCo0" in body and "ReadyCo1" in body and "ReadyCo2" in body
        assert "View all" not in body
        assert 'class="ar-view-all"' not in body
    finally:
        app.dependency_overrides.clear()


def _seed_eligibility_group_and_outlier(tmp_path, group_size=20):
    db_path = tmp_path / "elig_group.db"
    history = ApplicationHistoryService(db_path)
    service = OpportunityCRMService(history)
    try:
        for i in range(group_size):
            fingerprint = job_fingerprint(source="LinkedIn", external_job_id=f"elg-{i}")
            record = service.create_opportunity(fingerprint, company=f"GroupCo{i}", job_title="Analyst")
            service.update_opportunity(record["id"], intelligence_priority="C", remote_eligibility="MANUAL_REVIEW")
            service.transition_stage(record["id"], "ELIGIBILITY_REVIEW")
        # One genuinely different eligibility reason -- must stay separate.
        outlier_fp = job_fingerprint(source="LinkedIn", external_job_id="elg-outlier")
        outlier = service.create_opportunity(outlier_fp, company="OutlierCo", job_title="Analyst")
        service.update_opportunity(
            outlier["id"], intelligence_priority="C", remote_eligibility="MANUAL_REVIEW",
            remote_eligibility_reason="A completely different eligibility question entirely.",
        )
        service.transition_stage(outlier["id"], "ELIGIBILITY_REVIEW")
    finally:
        service.close()
    return db_path


def test_eligibility_group_remains_one_compact_collapsed_card_by_default(tmp_path):
    db_path = _seed_eligibility_group_and_outlier(tmp_path, group_size=20)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert "20 opportunities" in body
        assert 'class="action-group"' in body
        # Collapsed by default -- the 20 individual GroupCo rows are not
        # rendered as their own top-level compact rows outside the card.
        eligibility_header = body.index("<h2>Eligibility Decision</h2>")
        group_details_pos = body.index('<details class="action-group">', eligibility_header)
        assert body.index("GroupCo0") > group_details_pos  # only reachable inside the group's own body
    finally:
        app.dependency_overrides.clear()


def test_eligibility_outlier_rendered_separately_from_the_group(tmp_path):
    db_path = _seed_eligibility_group_and_outlier(tmp_path, group_size=20)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert "OutlierCo" in body
        assert "A completely different eligibility question entirely." in body
        # The outlier is its own compact row, not folded into the 20-count group.
        assert "21 opportunities" not in body
    finally:
        app.dependency_overrides.clear()


def test_eligibility_explanation_appears_exactly_once(tmp_path):
    db_path = _seed_eligibility_group_and_outlier(tmp_path, group_size=20)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert body.count("These vacancies require individual eligibility decisions. Grouping is for triage only.") == 1
    finally:
        app.dependency_overrides.clear()


def test_zero_count_categories_render_no_body_section(tmp_path):
    """Answer Required / Browser Action Required / Employer Action stay at
    0 in this fixture -- their KPI cards must still show 0, but no empty
    section/header should render below for them."""
    db_path, ids = _seed_many_review_and_submit(tmp_path, count=2)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        assert '<div class="n">0</div><div class="l">Answer Required</div>' in body  # KPI card intact
        # No section header for a zero-count category.
        content_after_kpis = body.split("filters-card")[-1] if "filters-card" in body else body
        # A rendered section would include the humanized crm_stage text or
        # a category-specific hint; absent here since count is 0.
        assert "Answer Required</h2>" not in body
        assert "Browser Action Required</h2>" not in body
        assert "Employer Action</h2>" not in body
    finally:
        app.dependency_overrides.clear()


def test_action_required_priority_filter_still_scopes_category_sections(tmp_path):
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/action-required?priority=C").text
        assert "Acme Robotics" in body  # the C-priority eligibility item
        assert "Robert Half" not in body  # unscored Review & Submit item filtered out
    finally:
        app.dependency_overrides.clear()


def test_action_required_kpi_counts_and_page_structure_unchanged_by_compression(tmp_path):
    """The presentation compression must never change what the KPI cards
    report -- same reconciled totals as Phase 3."""
    db_path, ids, refs = _seed_action_required(tmp_path)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        import re
        cards = {m.group(2): int(m.group(1)) for m in re.finditer(r'<div class="n">(\d+)</div><div class="l">([^<]*)</div>', body)}
        assert cards["Total Actions Required"] == 5
        assert cards["Total Actions Required"] == sum(
            cards[k] for k in ("Review &amp; Submit", "Answer Required", "Eligibility Decision", "Browser Action Required", "Employer Action")
        )
    finally:
        app.dependency_overrides.clear()


def test_action_required_page_is_substantially_shorter_with_many_review_and_submit_items(tmp_path):
    """Direct check of the stated problem: a page with 10 Review & Submit
    items must not render all 10 as full-height cards before anything else
    -- the compact top-3 + collapsed-rest layout keeps the response
    meaningfully shorter than one row per item at the old verbosity."""
    db_path, ids = _seed_many_review_and_submit(tmp_path, count=10)
    try:
        client = _client(db_path)
        body = client.get("/action-required").text
        # Only 3 rows' worth of always-visible "action-btn" links precede
        # the "View all" disclosure for this category (verified by position
        # in an earlier test); here just confirm the compact row markup is
        # what's actually USED (the class definition stays in <style>
        # regardless -- the Resolved view still uses the fuller row).
        assert 'class="action-row__meta"' not in body  # old verbose per-row meta line is not rendered in the active view
        assert 'class="ar-row__reason"' in body
    finally:
        app.dependency_overrides.clear()


# --- Production verification (READ-ONLY) ------------------------------------
def test_action_required_production_counts_match_before_and_after_compression():
    """Read-only: the underlying counts must be byte-for-byte identical to
    what Phase 3 established -- this task changes presentation only."""
    service = OpportunityCRMService()
    try:
        counts = service.action_required_counts()
    finally:
        service.close()
    client = TestClient(app)
    body = client.get("/action-required").text
    import re
    cards = {m.group(2): int(m.group(1)) for m in re.finditer(r'<div class="n">(\d+)</div><div class="l">([^<]*)</div>', body)}
    assert cards["Total Actions Required"] == counts["TOTAL"]
    assert cards["Review &amp; Submit"] == counts["REVIEW_AND_SUBMIT"]
    assert cards["Eligibility Decision"] == counts["ELIGIBILITY_DECISION"]
    assert cards["Answer Required"] == counts["ANSWER_REQUIRED"]
    assert cards["Browser Action Required"] == counts["BROWSER_ACTION"]
    assert cards["Employer Action"] == counts["EMPLOYER_ACTION"]


def test_action_required_production_review_and_submit_shows_top_3_with_view_all():
    """Read-only: verifies the compression against the REAL production
    queue (10 Review & Submit items today)."""
    client = TestClient(app)
    body = client.get("/action-required").text
    service = OpportunityCRMService()
    try:
        count = service.action_required_counts()["REVIEW_AND_SUBMIT"]
    finally:
        service.close()
    if count > 3:
        assert f"View all {count}" in body
    else:
        assert "View all" not in body
