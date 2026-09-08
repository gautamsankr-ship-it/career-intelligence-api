"""Web App Phase 7: Analytics & Learning tests.

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


def _seed_opportunity(service, *, external_id, company="Co", priority="C", remote_eligibility="ELIGIBLE",
                       market="united_kingdom", source="LinkedIn", career_track="CORE_FINANCE",
                       work_arrangement="REMOTE", applied=False):
    fingerprint = job_fingerprint(source=source, external_job_id=external_id)
    record = service.create_opportunity(fingerprint, company=company, job_title="Analyst")
    service.update_opportunity(
        record["id"], intelligence_priority=priority, remote_eligibility=remote_eligibility,
        market=market, career_track=career_track, work_arrangement=work_arrangement, source=source,
    )
    if applied:
        service.record_submission_confirmation(record["id"], confirmation_evidence="e", submission_reference=f"s-{external_id}")
    return record["id"]


# --- Confidence framework ----------------------------------------------------
def test_confidence_tiny_sample_is_always_emerging():
    assert a.assess_confidence(3) == a.EMERGING
    assert a.assess_confidence(3, consistency_ratio=1.0, downstream_outcomes=5) == a.EMERGING


def test_confidence_moderate_requires_meaningful_sample():
    assert a.assess_confidence(15, consistency_ratio=0.6) == a.MODERATE


def test_confidence_high_requires_substantial_consistent_sample():
    assert a.assess_confidence(50, consistency_ratio=0.75) == a.HIGH


def test_confidence_low_consistency_stays_emerging_regardless_of_sample():
    assert a.assess_confidence(100, consistency_ratio=0.2) == a.EMERGING


def test_confidence_repeated_downstream_outcomes_reach_high():
    assert a.assess_confidence(12, downstream_outcomes=3) == a.HIGH


# --- Funnel reconciliation ---------------------------------------------------
def test_analytics_funnel_reconciles_to_crm_dashboard_definitions(tmp_path):
    db_path = tmp_path / "reconcile.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="r1", applied=True)
        _seed_opportunity(service, external_id="r2", applied=False)
        service.record_employer_response(1, "ACKNOWLEDGEMENT", summary="thanks")
        crm_funnel = service.cumulative_funnel_counts()
        overview = a.performance_overview(service)
    finally:
        service.close()
    assert overview["opportunities_discovered"] == crm_funnel["DISCOVERED"]
    assert overview["applications_submitted"] == crm_funnel["APPLIED"]


def test_acknowledgement_excluded_from_meaningful_response(tmp_path):
    db_path = tmp_path / "ack_excl.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="a1", applied=True)
        service.record_employer_response(1, "ACKNOWLEDGEMENT", summary="thanks")
        funnel = service.cumulative_funnel_counts()
        assert funnel["ACKNOWLEDGED"] == 1
        assert funnel["MEANINGFUL_RESPONSE"] == 0
    finally:
        service.close()


def test_zero_denominator_renders_as_em_dash(tmp_path):
    db_path = tmp_path / "zero_denom.db"
    service = _open(db_path)
    try:
        overview = a.performance_overview(service)
        assert overview["application_rate"] == "—"
        assert overview["meaningful_response_rate"] == "—"
    finally:
        service.close()


def test_small_samples_labelled_honestly(tmp_path):
    db_path = tmp_path / "small.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="s1", applied=True)
        overview = a.performance_overview(service)
        assert overview["early_data_note"] is not None
        assert "Early data" in overview["early_data_note"]
        rows = a.priority_effectiveness(service)
        c_row = next(r for r in rows if r["label"] == "C")
        assert c_row["insufficient_data"] is True
        assert "insufficient outcome data" in c_row["note"]
    finally:
        service.close()


# --- Priority preserves Unscored --------------------------------------------
def test_priority_analysis_preserves_unscored(tmp_path):
    db_path = tmp_path / "unscored.db"
    service = _open(db_path)
    try:
        fingerprint = job_fingerprint(source="LinkedIn", external_job_id="unscored1")
        service.create_opportunity(fingerprint, company="NoScore Co", job_title="Analyst")
        rows = a.priority_effectiveness(service)
        assert any(r["label"] == "Unscored" for r in rows)
    finally:
        service.close()


# --- Market/source use actual data ------------------------------------------
def test_market_and_source_analysis_uses_actual_data(tmp_path):
    db_path = tmp_path / "market_source.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="m1", market="australia", source="LinkedIn")
        _seed_opportunity(service, external_id="m2", market="united_states", source="Indeed")
        market_rows = a.market_performance(service)
        source_rows = a.source_performance(service)
        assert {r["label"] for r in market_rows} == {"australia", "united_states"}
        assert {r["label"] for r in source_rows} == {"LinkedIn", "Indeed"}
    finally:
        service.close()


# --- Screening/eligibility uses real blocker/reason data --------------------
def test_screening_eligibility_uses_real_reason_data(tmp_path):
    db_path = tmp_path / "screening.db"
    service = _open(db_path)
    try:
        for i in range(5):
            _seed_opportunity(service, external_id=f"e{i}", remote_eligibility="MANUAL_REVIEW")
        rows = a.screening_eligibility_intelligence(service)
        matching = next(r for r in rows if "geographic eligibility" in r["reason"])
        assert matching["occurrences"] == 5
    finally:
        service.close()


def test_screening_eligibility_empty_when_no_manual_review(tmp_path):
    db_path = tmp_path / "no_review.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="ok1", remote_eligibility="ELIGIBLE")
        rows = a.screening_eligibility_intelligence(service)
        assert rows == []
    finally:
        service.close()


# --- Rejection reason never fabricated --------------------------------------
def test_rejection_reason_never_fabricated_only_explicit_or_none(tmp_path):
    db_path = tmp_path / "rejection.db"
    service = _open(db_path)
    try:
        tid = _seed_opportunity(service, external_id="rej1", applied=True)
        service.record_rejection(tid, rejection_reason="Not enough SQL experience")
        result = a.rejection_intelligence(service)
        assert result["available"] is True
        assert result["rejections"][0]["explicit_reason"] == "Not enough SQL experience"
    finally:
        service.close()


def test_rejection_intelligence_clean_early_data_state_when_none(tmp_path):
    db_path = tmp_path / "no_rejection.db"
    service = _open(db_path)
    try:
        result = a.rejection_intelligence(service)
        assert result == {"available": False, "count": 0, "rejections": []}
    finally:
        service.close()


# --- Human intervention metric -----------------------------------------------
def test_human_intervention_metric_does_not_count_passive_events(tmp_path):
    """Merely discovering/viewing opportunities must never inflate the
    intervention count -- only genuinely auditable human actions do."""
    db_path = tmp_path / "passive.db"
    service = _open(db_path)
    try:
        for i in range(20):
            _seed_opportunity(service, external_id=f"passive{i}")
        metrics = a.human_intervention_metrics(service)
        assert metrics["total_observed_interventions"] == 0
        assert metrics["applications"] == 0
    finally:
        service.close()


def test_human_intervention_metric_counts_final_submit_and_decisions(tmp_path):
    db_path = tmp_path / "genuine.db"
    service = _open(db_path)
    try:
        tid = _seed_opportunity(service, external_id="genuine1", applied=True)
        service.record_user_decision(tid, "APPLY", reason_code="CAREER_VALUE")
        metrics = a.human_intervention_metrics(service)
        assert metrics["counted_by_type"]["Final-submit authorization"] == 1
        assert metrics["counted_by_type"]["Screening decision recorded (Apply/Watch/Reject)"] == 1
        assert metrics["total_observed_interventions"] == 2
        assert metrics["per_application"] == 2.0
    finally:
        service.close()


def test_partial_intervention_measurement_labelled_honestly(tmp_path):
    db_path = tmp_path / "partial.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="partial1", applied=True)
        metrics = a.human_intervention_metrics(service)
        assert metrics["partial_measurement"] is True
        assert "CAPTCHA/MFA/login handled" in metrics["unobserved_types"]
        assert "Unknown-answer approval" in metrics["unobserved_types"]
    finally:
        service.close()


def test_intervention_measurement_not_partial_once_all_types_observed(tmp_path):
    db_path = tmp_path / "full.db"
    service = _open(db_path)
    try:
        tid = _seed_opportunity(service, external_id="full1")
        service.record_human_blocker(tid, "HUMAN_CAPTCHA_REQUIRED", detail="captcha")
        service.record_human_blocker(tid, "HUMAN_ANSWER_APPROVAL_REQUIRED", detail="answer")
        metrics = a.human_intervention_metrics(service)
        assert metrics["partial_measurement"] is False
        assert metrics["unobserved_types"] == []
    finally:
        service.close()


# --- Observations are deterministic -----------------------------------------
def test_observations_are_deterministic(tmp_path):
    db_path = tmp_path / "deterministic.db"
    service = _open(db_path)
    try:
        for i in range(10):
            _seed_opportunity(service, external_id=f"det{i}", remote_eligibility="MANUAL_REVIEW")
        first = a.generate_observations(service)
        second = a.generate_observations(service)
    finally:
        service.close()
    assert first == second


# --- Proposed learning threshold (item 21) -----------------------------------
def test_proposed_learning_not_created_from_trivial_one_off_noise(tmp_path):
    """3 applications and zero interviews is NOT sufficient evidence."""
    db_path = tmp_path / "trivial.db"
    service = _open(db_path)
    try:
        for i in range(3):
            _seed_opportunity(service, external_id=f"trivial{i}", applied=True, remote_eligibility="MANUAL_REVIEW")
        candidates = a.generate_learning_candidates(service)
        assert candidates == []
    finally:
        service.close()


def test_proposed_learning_created_for_material_repeated_pattern(tmp_path):
    db_path = tmp_path / "material.db"
    service = _open(db_path)
    try:
        for i in range(35):
            _seed_opportunity(service, external_id=f"material{i}", remote_eligibility="MANUAL_REVIEW")
        for i in range(5):
            _seed_opportunity(service, external_id=f"eligible{i}", remote_eligibility="ELIGIBLE")
        candidates = a.generate_learning_candidates(service)
        assert len(candidates) == 1
        assert candidates[0]["key"] == "eligibility-review-burden"
        assert candidates[0]["confidence"] in (a.MODERATE, a.HIGH)
    finally:
        service.close()


# --- Learning governance -----------------------------------------------------
def _seed_material_pattern(service):
    for i in range(35):
        _seed_opportunity(service, external_id=f"gov{i}", remote_eligibility="MANUAL_REVIEW")
    for i in range(5):
        _seed_opportunity(service, external_id=f"gov-elig{i}", remote_eligibility="ELIGIBLE")


def test_learning_status_transitions_are_audited(tmp_path):
    db_path = tmp_path / "audit.db"
    service = _open(db_path)
    try:
        _seed_material_pattern(service)
    finally:
        service.close()
    try:
        client = _client(db_path)
        client.post("/analytics/learning/eligibility-review-burden/accept", data={"review_note": "solid"}, follow_redirects=False)
        client.post("/analytics/learning/eligibility-review-burden/need-more-evidence", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        learning = service.get_proposed_learning_by_key("eligibility-review-burden")
        assert learning["status"] == "NEED_MORE_EVIDENCE"
        history = service.list_proposed_learning_status_history(learning["id"])
        assert len(history) == 2
        assert history[-1]["previous_status"] is None
        assert history[-1]["new_status"] == "ACCEPTED"
        assert history[0]["previous_status"] == "ACCEPTED"
        assert history[0]["new_status"] == "NEED_MORE_EVIDENCE"
    finally:
        service.close()


def test_accept_does_not_silently_mutate_scoring_or_eligibility(tmp_path):
    db_path = tmp_path / "no_mutate.db"
    service = _open(db_path)
    try:
        _seed_material_pattern(service)
        before = {r: dict(service.get_opportunity(r)) for r in range(1, 41)}
    finally:
        service.close()
    try:
        client = _client(db_path)
        client.post("/analytics/learning/eligibility-review-burden/accept", follow_redirects=False)
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        for tracker_id, before_record in before.items():
            after_record = service.get_opportunity(tracker_id)
            assert after_record["intelligence_priority"] == before_record["intelligence_priority"]
            assert after_record["career_score"] == before_record["career_score"]
            assert after_record["remote_eligibility"] == before_record["remote_eligibility"]
    finally:
        service.close()


def test_need_more_evidence_works(tmp_path):
    db_path = tmp_path / "nme.db"
    service = _open(db_path)
    try:
        _seed_material_pattern(service)
    finally:
        service.close()
    try:
        client = _client(db_path)
        r = client.post("/analytics/learning/eligibility-review-burden/need-more-evidence", data={"review_note": "watch longer"}, follow_redirects=False)
        assert r.status_code == 303
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        learning = service.get_proposed_learning_by_key("eligibility-review-burden")
        assert learning["status"] == "NEED_MORE_EVIDENCE"
        assert learning["review_note"] == "watch longer"
    finally:
        service.close()


def test_reject_works(tmp_path):
    db_path = tmp_path / "reject.db"
    service = _open(db_path)
    try:
        _seed_material_pattern(service)
    finally:
        service.close()
    try:
        client = _client(db_path)
        r = client.post("/analytics/learning/eligibility-review-burden/reject", data={"review_note": "not actionable"}, follow_redirects=False)
        assert r.status_code == 303
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        learning = service.get_proposed_learning_by_key("eligibility-review-burden")
        assert learning["status"] == "REJECTED"
    finally:
        service.close()


def test_unknown_learning_key_is_ignored_not_crashed(tmp_path):
    db_path = tmp_path / "unknown_key.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="uk1")
    finally:
        service.close()
    try:
        client = _client(db_path)
        r = client.post("/analytics/learning/does-not-exist/accept", follow_redirects=False)
        assert r.status_code == 303
    finally:
        app.dependency_overrides.clear()
    service = _open(db_path)
    try:
        assert service.get_proposed_learning_by_key("does-not-exist") is None
    finally:
        service.close()


# --- User preference vs market evidence (item 23) ---------------------------
def test_analytics_never_reads_user_stated_preferences_as_evidence():
    """generate_observations/generate_learning_candidates must derive
    everything from stored outcome data -- never from the candidate
    profile's own stated career preferences."""
    import inspect
    source = inspect.getsource(a)
    for forbidden in ("career_preferences", "career_objective", "professional_strengths", "unique_value_proposition"):
        assert forbidden not in source


# --- Drill-down links resolve -------------------------------------------------
def test_drill_down_links_resolve(tmp_path):
    db_path = tmp_path / "drilldown.db"
    service = _open(db_path)
    try:
        _seed_opportunity(service, external_id="dd1", priority="C", market="australia")
    finally:
        service.close()
    try:
        client = _client(db_path)
        assert client.get("/opportunities?intelligence_priority=C").status_code == 200
        assert client.get("/opportunities?market=australia").status_code == 200
        assert client.get("/action-required").status_code == 200
        assert client.get("/employer-inbox").status_code == 200
    finally:
        app.dependency_overrides.clear()


# --- CV strategy honesty ------------------------------------------------------
def test_cv_strategy_shows_honest_unavailable_message(tmp_path):
    db_path = tmp_path / "cv.db"
    service = _open(db_path)
    try:
        result = a.cv_strategy_performance(service)
        assert result["available"] is False
        assert result["message"] == "Insufficient version history for reliable comparison."
    finally:
        service.close()


# --- Placeholder route removal ------------------------------------------------
def test_analytics_no_longer_a_placeholder():
    client = TestClient(app)
    body = client.get("/analytics").text
    assert "Coming in a later phase" not in body
    assert "Performance Overview" in body


# --- Production read-only verification ---------------------------------------
def test_production_analytics_funnel_matches_dashboard_read_only():
    service = OpportunityCRMService()
    try:
        crm_funnel = service.cumulative_funnel_counts()
        overview = a.performance_overview(service)
    finally:
        service.close()
    assert overview["opportunities_discovered"] == crm_funnel["DISCOVERED"] == 157
    assert overview["applications_submitted"] == crm_funnel["APPLIED"] == 3


def test_production_analytics_page_creates_no_rows_read_only():
    service = OpportunityCRMService()
    try:
        before_learnings = service.connection.execute("SELECT COUNT(*) FROM proposed_learnings").fetchone()[0]
        before_history = service.connection.execute("SELECT COUNT(*) FROM proposed_learning_status_history").fetchone()[0]
        before_opportunities = service.connection.execute("SELECT COUNT(*) FROM application_history").fetchone()[0]
    finally:
        service.close()

    client = TestClient(app)
    for period in ("all", "7d", "30d", "90d"):
        response = client.get(f"/analytics?period={period}")
        assert response.status_code == 200

    service = OpportunityCRMService()
    try:
        after_learnings = service.connection.execute("SELECT COUNT(*) FROM proposed_learnings").fetchone()[0]
        after_history = service.connection.execute("SELECT COUNT(*) FROM proposed_learning_status_history").fetchone()[0]
        after_opportunities = service.connection.execute("SELECT COUNT(*) FROM application_history").fetchone()[0]
    finally:
        service.close()
    assert after_learnings == before_learnings
    assert after_history == before_history
    assert after_opportunities == before_opportunities
