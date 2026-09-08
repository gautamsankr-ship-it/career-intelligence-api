"""Web App Phase 7.1: KPI Reconciliation & Executive Drill-Through tests.

Mutation tests use tmp_path-based SQLite databases only. Read-only tests
against the real production database (`OpportunityCRMService()` with no
override) never write -- they only assert on already-existing evidence.
"""
from fastapi.testclient import TestClient

from app.api.dashboard import app, get_crm_service
from app.services import analytics_service as a
from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
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


def _seed_opportunity(service, *, external_id, company="Co", priority="C", applied=False, ack=False):
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id=external_id)
    record = service.create_opportunity(fingerprint, company=company, job_title="Analyst")
    service.update_opportunity(record["id"], intelligence_priority=priority)
    if applied:
        service.record_submission_confirmation(record["id"], confirmation_evidence="e", submission_reference=f"s-{external_id}")
    if ack:
        service.record_employer_response(record["id"], "ACKNOWLEDGEMENT", summary="thanks")
    return record["id"]


# --- Opportunity/priority population reconciliation --------------------------
def test_total_opportunity_count_reconciles(tmp_path):
    db_path = tmp_path / "total.db"
    service = _open(db_path)
    try:
        for i in range(10):
            _seed_opportunity(service, external_id=f"tot{i}")
        report = a.reconciliation_checks(service)
        assert report["opportunity_population"] == 10
        assert report["status"] == "Reconciled"
    finally:
        service.close()


def test_priority_buckets_including_unscored_sum_to_total(tmp_path):
    db_path = tmp_path / "buckets.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="p1", priority="A")
        _seed_opportunity(service, external_id="p2", priority="C")
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id="p3")
        service.create_opportunity(fingerprint, company="Unscored Co", job_title="Analyst")  # no priority set
        report = a.reconciliation_checks(service)
        check = next(c for c in report["checks"] if "sums to total" in c["name"])
        assert check["passed"] is True
        assert check["expected"] == 3
    finally:
        service.close()


# --- Submitted applications reconciliation ------------------------------------
def test_submitted_count_equals_confirmed_submission_population(tmp_path):
    db_path = tmp_path / "submitted.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="s1", applied=True)
        _seed_opportunity(service, external_id="s2", applied=True)
        _seed_opportunity(service, external_id="s3", applied=False)  # prepared/not submitted
        funnel = service.cumulative_funnel_counts()
        assert funnel["APPLIED"] == 2
    finally:
        service.close()


def test_submitted_drill_through_returns_exactly_the_kpi_population(tmp_path):
    db_path = tmp_path / "drillthrough.db"
    service = _open(db_path)
    try:
        ids = {_seed_opportunity(service, external_id=f"dt{i}", applied=True) for i in range(3)}
        _seed_opportunity(service, external_id="dt-not", applied=False)
        funnel = service.cumulative_funnel_counts()
    finally:
        service.close()
    try:
        client = _client(db_path)
        body = client.get("/applications?tab=submitted").text
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        register = service.applications_register(tab=OpportunityCRMService.SUBMITTED_TAB, page_size=1000)
        assert register["total"] == funnel["APPLIED"] == 3
        assert {r["id"] for r in register["results"]} == ids
    finally:
        service.close()


def test_dashboard_applications_submitted_kpi_links_to_exact_population(tmp_path):
    db_path = tmp_path / "kpi_link.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="link1", company="Link Co", applied=True)
    finally:
        service.close()
    try:
        client = _client(db_path)
        home_body = client.get("/").text
        assert 'href="/applications?tab=submitted"' in home_body
        drill_body = client.get("/applications?tab=submitted").text
        assert "Link Co" in drill_body
    finally:
        app.dependency_overrides.clear()


# --- Acknowledgement reconciliation -------------------------------------------
def test_acknowledgement_count_equals_drill_through_population(tmp_path):
    db_path = tmp_path / "ack.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="a1", applied=True, ack=True)
        _seed_opportunity(service, external_id="a2", applied=True, ack=True)
        _seed_opportunity(service, external_id="a3", applied=True, ack=False)
        funnel = service.cumulative_funnel_counts()
        assert funnel["ACKNOWLEDGED"] == 2
    finally:
        service.close()
    try:
        client = _client(db_path)
        inbox_body = client.get("/employer-inbox?filter=acknowledgement").text
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        items = [i for i in service.employer_inbox_items() if i["response_type"] == "ACKNOWLEDGEMENT"]
        distinct_trackers = {i["tracker_id"] for i in items}
        assert len(distinct_trackers) == 2 == funnel["ACKNOWLEDGED"]
    finally:
        service.close()


def test_acknowledgement_remains_excluded_from_meaningful_responses(tmp_path):
    db_path = tmp_path / "ack_meaningful.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="am1", applied=True, ack=True)
        funnel = service.cumulative_funnel_counts()
        assert funnel["ACKNOWLEDGED"] == 1
        assert funnel["MEANINGFUL_RESPONSE"] == 0
        report = a.reconciliation_checks(service)
        check = next(c for c in report["checks"] if "never classified as a meaningful" in c["name"])
        assert check["passed"] is True
    finally:
        service.close()


# --- Interview/offer reconciliation -------------------------------------------
def test_interview_count_reconciles(tmp_path):
    db_path = tmp_path / "interview_recon.db"
    service = _open(db_path)
    try:
        tid = _seed_opportunity(service, external_id="iv1", applied=True)
        service.record_interview(tid, "SCREENING")
        report = a.reconciliation_checks(service)
        check = next(c for c in report["checks"] if c["name"].startswith("Interviews"))
        assert check["passed"] is True
        assert check["expected"] == 1
    finally:
        service.close()


def test_offer_count_reconciles(tmp_path):
    db_path = tmp_path / "offer_recon.db"
    service = _open(db_path)
    try:
        tid = _seed_opportunity(service, external_id="of1", applied=True)
        service.record_offer(tid)
        report = a.reconciliation_checks(service)
        check = next(c for c in report["checks"] if c["name"].startswith("Offers"))
        assert check["passed"] is True
        assert check["expected"] == 1
    finally:
        service.close()


# --- Application rate calculation --------------------------------------------
def test_application_rate_uses_submitted_over_discovered(tmp_path):
    db_path = tmp_path / "rate.db"
    service = _open(db_path)
    try:
        for i in range(157):
            applied = i < 3
            _seed_opportunity(service, external_id=f"rate{i}", applied=applied)
        detail = a.rate_detail(service, "application")
        assert detail["numerator"] == 3
        assert detail["denominator"] == 157
    finally:
        service.close()


def test_application_rate_3_of_157_renders_exact_and_display(tmp_path):
    db_path = tmp_path / "rate_exact.db"
    service = _open(db_path)
    try:
        for i in range(157):
            applied = i < 3
            _seed_opportunity(service, external_id=f"exact{i}", applied=applied)
        detail = a.rate_detail(service, "application")
        assert detail["exact_percent"] == 1.91
        assert detail["display_percent"] == "2%"
    finally:
        service.close()


def test_zero_denominator_handled_safely(tmp_path):
    db_path = tmp_path / "zero.db"
    service = _open(db_path)
    try:
        detail = a.rate_detail(service, "meaningful_response")
        assert detail["display_percent"] == "—"
        assert detail["exact_percent"] is None
    finally:
        service.close()


def test_applied_plus_not_applied_composition_equals_total(tmp_path):
    db_path = tmp_path / "composition.db"
    service = _open(db_path)
    try:
        for i in range(20):
            applied = i < 5
            _seed_opportunity(service, external_id=f"comp{i}", applied=applied)
        detail = a.rate_detail(service, "application")
        composition = detail["composition"]
        assert composition["applied"] == 5
        assert composition["not_applied"] == 15
        assert composition["applied"] + composition["not_applied"] == 20
    finally:
        service.close()


# --- Drill-through routes resolve --------------------------------------------
def test_clicking_drill_through_routes_resolve(tmp_path):
    db_path = tmp_path / "routes.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="r1", applied=True, ack=True)
    finally:
        service.close()
    try:
        client = _client(db_path)
        for path in (
            "/opportunities", "/applications?tab=submitted", "/employer-inbox?filter=acknowledgement",
            "/employer-inbox?filter=meaningful", "/interviews", "/opportunities?application_state=not_applied",
            "/opportunities?application_state=applied",
            "/analytics/rate/application", "/analytics/rate/meaningful_response",
            "/analytics/rate/interview", "/analytics/rate/offer", "/analytics/rate/hire",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
    finally:
        app.dependency_overrides.clear()


def test_individual_application_link_resolves_from_drill_through(tmp_path):
    db_path = tmp_path / "individual.db"
    service = _open(db_path)
    try:
        tid = _seed_opportunity(service, external_id="ind1", company="Individual Co", applied=True)
    finally:
        service.close()
    try:
        client = _client(db_path)
        register_body = client.get("/applications?tab=submitted").text
        assert f'href="/application/{tid}"' in register_body
        detail_response = client.get(f"/application/{tid}")
        assert detail_response.status_code == 200
        assert "Individual Co" in detail_response.text
    finally:
        app.dependency_overrides.clear()


def test_unknown_rate_metric_404s():
    client = TestClient(app)
    response = client.get("/analytics/rate/not-a-real-metric")
    assert response.status_code == 404


# --- application_state filter preserves correct populations -----------------
def test_application_state_filter_preserves_correct_populations(tmp_path):
    db_path = tmp_path / "filter.db"
    service = _open(db_path)
    try:
        for i in range(10):
            applied = i < 4
            _seed_opportunity(service, external_id=f"filt{i}", applied=applied)
    finally:
        service.close()
    try:
        client = _client(db_path)
        applied_body = client.get("/opportunities?application_state=applied").text
        not_applied_body = client.get("/opportunities?application_state=not_applied").text
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        applied_result = service.search_opportunities(application_state="applied", page_size=1000)
        not_applied_result = service.search_opportunities(application_state="not_applied", page_size=1000)
        assert applied_result["total"] == 4
        assert not_applied_result["total"] == 6
        assert applied_result["total"] + not_applied_result["total"] == 10
    finally:
        service.close()


# --- Reconciliation status becomes Attention Required on a synthetic mismatch
def test_reconciliation_status_becomes_attention_required_on_mismatch():
    """Directly exercises the pure _check() aggregation logic with a
    deliberately fabricated mismatch -- proves the panel does not silently
    hide a discrepancy when one genuinely exists."""
    checks = [
        a._check("Fabricated check A", 3, 3),
        a._check("Fabricated check B (mismatched)", 5, 7),
    ]
    assert checks[0]["passed"] is True
    assert checks[1]["passed"] is False
    status = "Reconciled" if all(c["passed"] for c in checks) else "Attention Required"
    assert status == "Attention Required"


def test_reconciliation_checks_reflects_real_mismatch_via_monkeypatch(tmp_path, monkeypatch):
    db_path = tmp_path / "mismatch.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="mm1", applied=True)
        # Fabricate a divergence: response_quality_counts disagrees with the
        # independently-queried acknowledgement count.
        monkeypatch.setattr(service, "response_quality_counts", lambda: {"acknowledgements": 999, "meaningful_responses": 0, "unknown_responses": 0})
        report = a.reconciliation_checks(service)
        assert report["status"] == "Attention Required"
        failing = [c for c in report["checks"] if not c["passed"]]
        # cumulative_funnel_counts() itself reads through the same
        # monkeypatched method, so it agrees with response_quality_counts --
        # the divergence is only visible against the INDEPENDENT raw-SQL
        # query, exactly the accounting-style cross-check this panel exists
        # to perform.
        assert any(c["name"] == "Acknowledgements: funnel count matches independent query" for c in failing)
    finally:
        service.close()


# --- No production mutation / frozen scoring unchanged -----------------------
def test_no_mutation_from_drill_through_navigation(tmp_path):
    db_path = tmp_path / "no_mutate.db"
    service = _open(db_path)
    try:
        tid = _seed_opportunity(service, external_id="nomut1", applied=True, ack=True)
        before = dict(service.get_opportunity(tid))
    finally:
        service.close()
    try:
        client = _client(db_path)
        client.get("/")
        client.get("/analytics")
        client.get("/analytics/rate/application")
        client.get("/applications?tab=submitted")
        client.get("/opportunities?application_state=applied")
        client.get("/employer-inbox?filter=acknowledgement")
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        after = dict(service.get_opportunity(tid))
        assert after == before
    finally:
        service.close()


def test_frozen_scoring_eligibility_unchanged_by_reconciliation_module():
    import inspect
    source = inspect.getsource(a)
    for forbidden in ("UPDATE application_history", "INSERT INTO application_history", "update_opportunity(", "SET intelligence_priority", "SET career_score"):
        assert forbidden not in source


# --- Production read-only verification ---------------------------------------
def test_production_reconciliation_matches_expected_figures_read_only():
    service = OpportunityCRMService()
    try:
        report = a.reconciliation_checks(service)
    finally:
        service.close()
    assert report["opportunity_population"] == 157
    assert report["priority_population"] == 157
    assert report["confirmed_applications"] == 3
    assert report["acknowledged"] == 2
    assert report["meaningful_responses"] == 0
    assert report["interview_records"] == 0
    assert report["offer_records"] == 0
    assert report["status"] == "Reconciled"


def test_production_application_rate_matches_expected_figures_read_only():
    service = OpportunityCRMService()
    try:
        detail = a.rate_detail(service, "application")
    finally:
        service.close()
    assert detail["numerator"] == 3
    assert detail["denominator"] == 157
    assert detail["exact_percent"] == 1.91
    assert detail["display_percent"] == "2%"
    assert detail["composition"]["applied"] == 3
    assert detail["composition"]["not_applied"] == 154


def test_production_drill_through_pages_create_no_rows_read_only():
    service = OpportunityCRMService()
    try:
        before_opportunities = service.connection.execute("SELECT COUNT(*) FROM application_history").fetchone()[0]
        before_events = service.connection.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0]
    finally:
        service.close()

    client = TestClient(app)
    for path in (
        "/", "/analytics", "/analytics/rate/application", "/analytics/rate/meaningful_response",
        "/applications?tab=submitted", "/opportunities?application_state=applied",
        "/opportunities?application_state=not_applied", "/employer-inbox?filter=acknowledgement",
    ):
        response = client.get(path)
        assert response.status_code == 200

    service = OpportunityCRMService()
    try:
        after_opportunities = service.connection.execute("SELECT COUNT(*) FROM application_history").fetchone()[0]
        after_events = service.connection.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0]
    finally:
        service.close()
    assert after_opportunities == before_opportunities
    assert after_events == before_events
