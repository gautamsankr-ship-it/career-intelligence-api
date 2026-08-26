from types import SimpleNamespace

from app.config import JOB_SOURCES
from app.models.career_opportunity import CareerOpportunity
from app.services.application_history_service import ApplicationHistoryService
from app.services.discovery_quality_gate import DiscoveryQualityGate, FINANCE_TECH
from app.services.job_search_config import DISCOVERY_QUERY_CYCLE, FINANCE_TECH_ROLE_FAMILIES, linkedin_market_searches, TARGET_MARKETS
from app.services.target_employer_registry import TARGET_EMPLOYERS, industry_tag_summary


def test_finance_tech_search_families_are_centralized_and_unique():
    keys = [family.key for family in FINANCE_TECH_ROLE_FAMILIES]
    queries = [family.query.casefold() for family in FINANCE_TECH_ROLE_FAMILIES]
    assert len(keys) == len(set(keys)) == 10
    assert len(queries) == len(set(queries))
    assert {"finance_transformation", "finance_systems", "finance_automation", "financial_data_analytics", "regtech_risk", "erp_epm"} <= set(keys)


def test_finance_tech_rotation_is_bounded_and_does_not_change_market_call_count():
    searches = linkedin_market_searches(TARGET_MARKETS[0], count=3, rotation_index=len(DISCOVERY_QUERY_CYCLE) - 2)
    assert len(searches) == 3
    assert {search["family"].key for search in searches} & {family.key for family in FINANCE_TECH_ROLE_FAMILIES}


def test_registry_contains_curated_fintech_regtech_accounting_tech_and_financial_data_coverage():
    names = {employer.name for employer in TARGET_EMPLOYERS}
    assert {"Adyen", "Airwallex", "BlackLine", "Anaplan", "ComplyAdvantage", "Bloomberg", "LSEG"} <= names
    tags = industry_tag_summary()
    assert {"FINTECH", "PAYMENTS", "ACCOUNTING_TECH", "ERP_EPM", "REGTECH", "FINANCIAL_DATA"} <= set(tags)
    assert JOB_SOURCES == ("linkedin", "indeed")


def test_career_track_metadata_persists_without_affecting_evaluation_values(tmp_path):
    job = CareerOpportunity(
        id="systems-1", source="EmployerCareerSite", company="BlackLine",
        job_title="Finance Systems Manager", job_description="Finance systems.",
        metadata={"career_track": FINANCE_TECH, "opportunity_themes": ["FINANCE_SYSTEMS"]},
    )
    evaluation = SimpleNamespace(
        career_decision=SimpleNamespace(overall_score=82.0), screening_decision="AUTO_APPLY",
        ats_result={"ats_score": {"overall_score": 71.0}},
    )
    history = ApplicationHistoryService(tmp_path / "history.db")
    try:
        fingerprint, accepted = history.record_evaluation(job, evaluation, "MANUAL_WEB_REQUIRED", application_method="WEB")
        record = history.get_record(fingerprint)
        assert accepted is True
        assert record["career_track"] == FINANCE_TECH
        assert record["opportunity_themes"] == "FINANCE_SYSTEMS"
        assert record["career_score"] == 82.0 and record["ats_score"] == 71.0
    finally:
        history.close()


def test_finance_tech_roles_still_follow_existing_remote_filter_and_eligibility_metadata():
    job = CareerOpportunity(
        job_title="Finance Systems Manager", job_description="Fully remote. Work from anywhere.",
        posted_date="2026-08-25", work_arrangement="HYBRID", remote_status=None,
    )
    admitted = DiscoveryQualityGate().admit([job]).admitted
    assert admitted[0].metadata["career_track"] == FINANCE_TECH
    assert admitted[0].work_arrangement == "HYBRID"
