from datetime import date

import pytest

from app.models.career_opportunity import CareerOpportunity
from app.services.application_email_classifier import ApplicationEmailClassifier, EmailClassification
from app.services.discovery_quality_gate import (
    BOTH,
    CAREER_TRACK_UNKNOWN,
    CORE_FINANCE,
    DISCOVERY_AMBIGUOUS,
    DISCOVERY_IRRELEVANT,
    DISCOVERY_RELEVANT,
    FINANCE_TECH,
    FRESH,
    FRESHNESS_UNKNOWN,
    STALE,
    DiscoveryQualityGate,
)
from app.services.job_discovery_service import JobDiscoveryService
from app.services.job_sources import normalize_job_item


@pytest.mark.parametrize(
    ("title", "company", "description"),
    [
        ("Senior Accountant", "Example", ""),
        ("FP&A Manager", "Example", ""),
        ("Financial Controller", "Example", ""),
        ("Treasury Analyst", "Example", ""),
        ("Audit Manager", "Chartered Accounting Firm", "Audit and assurance practice"),
        ("Tax Manager", "Advisory Practice", "Tax advisory services"),
        ("Risk Advisory Manager", "Example Professional Services", "Risk advisory"),
        ("Business Advisory Manager", "Example", "Business advisory and accounting"),
        ("Senior Consultant - CFO Advisory", "Example", "CFO advisory"),
        ("Financial Due Diligence Manager", "Example", "Transaction advisory"),
    ],
)
def test_finance_and_professional_services_roles_are_relevant(title, company, description):
    result = DiscoveryQualityGate().classify_relevance(
        CareerOpportunity(job_title=title, company=company, job_description=description)
    )

    assert result.classification == DISCOVERY_RELEVANT


@pytest.mark.parametrize(
    "title",
    [
        "Mortgage Advisor",
        "Insurance Lawyer",
        "Investment Sales Advisor",
        "Business Development Executive",
    ],
)
def test_obviously_off_domain_titles_are_irrelevant(title):
    assert DiscoveryQualityGate().classify_relevance(CareerOpportunity(job_title=title)).classification == DISCOVERY_IRRELEVANT


def test_generic_consultant_without_professional_services_context_is_ambiguous():
    assert DiscoveryQualityGate().classify_relevance(CareerOpportunity(job_title="Consultant")).classification == DISCOVERY_AMBIGUOUS


def test_professional_services_context_rescues_a_nonstandard_manager_title():
    result = DiscoveryQualityGate().classify_relevance(
        CareerOpportunity(
            job_title="Assistant Manager",
            company="Boutique Chartered Accounting Firm",
            job_description="Business advisory and outsourced finance practice.",
        )
    )

    assert result.classification == DISCOVERY_RELEVANT
    assert result.professional_services_context is True
    assert result.professional_services_sector in {"accounting", "chartered accountancy"}


def test_freshness_admits_recent_and_unknown_but_rejects_stale_jobs():
    gate = DiscoveryQualityGate()
    today = date(2026, 8, 25)
    fresh = CareerOpportunity(job_title="Financial Analyst", posted_date="2026-08-18T10:00:00Z")
    stale = CareerOpportunity(job_title="Financial Analyst", posted_date="2026-08-17")
    unknown = CareerOpportunity(job_title="Financial Analyst", posted_date="not a date")

    assert gate.freshness(fresh, today) == FRESH
    assert gate.freshness(stale, today) == STALE
    assert gate.freshness(unknown, today) == FRESHNESS_UNKNOWN

    admission = gate.admit([fresh, stale, unknown], today)
    assert admission.admitted == [fresh, unknown]
    assert admission.freshness_counts == {FRESH: 1, STALE: 1, FRESHNESS_UNKNOWN: 1}


def test_relevance_metadata_is_diagnostic_and_email_classifier_is_unchanged():
    job = CareerOpportunity(
        job_title="Tax Consultant",
        company="Tax Advisory Practice",
        job_description="For privacy questions contact security@example.com.",
    )

    admitted = DiscoveryQualityGate().admit([job], date(2026, 8, 25))
    email = ApplicationEmailClassifier().classify_opportunity(job)

    assert admitted.admitted == [job]
    assert job.metadata["discovery_relevance"] == DISCOVERY_RELEVANT
    assert email.classification == EmailClassification.CONTACT_ONLY_EMAIL


def test_linkedin_unknown_remote_remains_excluded_after_quality_admission():
    job = CareerOpportunity(
        source="LinkedIn", job_title="Financial Analyst", posted_date="2026-08-25", work_arrangement="UNKNOWN"
    )

    admitted = DiscoveryQualityGate().admit([job], date(2026, 8, 25)).admitted
    assert JobDiscoveryService().filter_remote_jobs(admitted) == []


@pytest.mark.parametrize(
    ("work_mode", "is_remote", "expected_retained"),
    [
        ("Remote", True, True),
        ("Hybrid", None, False),
        ("On-site", False, False),
        ("", None, False),
    ],
)
def test_relevant_fresh_indeed_work_arrangement_controls_strict_cache_admission(
    work_mode, is_remote, expected_retained
):
    payload = {
        "title": "Financial Analyst",
        "posted_date": "2026-08-25T10:00:00Z",
    }
    if work_mode:
        payload["work_mode"] = work_mode
    if is_remote is not None:
        payload["is_remote"] = is_remote
    job = normalize_job_item(payload, "Indeed")

    admitted = DiscoveryQualityGate().admit([job], date(2026, 8, 25)).admitted
    retained = JobDiscoveryService().filter_remote_jobs(admitted)

    assert bool(retained) is expected_retained


def test_irrelevant_remote_indeed_job_does_not_reach_strict_remote_filter():
    job = normalize_job_item(
        {
            "title": "Business Development Manager", "is_remote": True,
            "work_mode": "Remote", "posted_date": "2026-08-25",
        },
        "Indeed",
    )

    admitted = DiscoveryQualityGate().admit([job], date(2026, 8, 25)).admitted

    assert job.work_arrangement == "REMOTE"
    assert admitted == []


@pytest.mark.parametrize(
    ("title", "description", "theme"),
    [
        ("Finance Transformation Manager", "Lead digital finance transformation.", "FINANCE_TRANSFORMATION"),
        ("Financial Systems Manager", "Own finance applications and financial systems.", "FINANCE_SYSTEMS"),
        ("Finance Automation Analyst", "Deliver accounting automation.", "FINANCE_AUTOMATION"),
        ("Financial Data Analyst", "Finance analytics and management reporting.", "FINANCIAL_DATA_ANALYTICS"),
        ("Regulatory Technology Consultant", "Risk technology and regulatory reporting.", "REGTECH_RISK"),
        ("Accounting Systems Manager", "Financial close technology and general ledger systems.", "ACCOUNTING_TECH"),
        ("ERP Finance Consultant", "Oracle EPM and financial planning systems.", "ERP_EPM"),
    ],
)
def test_finance_technology_crossover_roles_are_relevant(title, description, theme):
    result = DiscoveryQualityGate().classify_relevance(CareerOpportunity(job_title=title, job_description=description))
    assert result.classification == DISCOVERY_RELEVANT
    assert result.career_track in {FINANCE_TECH, BOTH}
    assert theme in result.opportunity_themes


@pytest.mark.parametrize("title", ["Software Engineer", "Data Scientist", "Cloud Engineer", "AI Engineer"])
def test_generic_technology_roles_are_not_rescued_by_fintech_employer(title):
    result = DiscoveryQualityGate().classify_relevance(
        CareerOpportunity(job_title=title, company="Example FinTech", job_description="Payments platform.")
    )
    assert result.classification == DISCOVERY_IRRELEVANT


def test_traditional_finance_role_at_fintech_remains_core_finance():
    result = DiscoveryQualityGate().classify_relevance(
        CareerOpportunity(job_title="Financial Controller", company="Payments FinTech")
    )
    assert result.classification == DISCOVERY_RELEVANT
    assert result.career_track in {CORE_FINANCE, BOTH}


def test_professional_services_finance_transformation_is_bridge_track():
    result = DiscoveryQualityGate().classify_relevance(
        CareerOpportunity(job_title="Finance Transformation Consultant", company="Accounting Advisory Firm", job_description="Audit and assurance practice.")
    )
    assert result.classification == DISCOVERY_RELEVANT
    assert result.career_track == BOTH


def test_career_track_metadata_is_diagnostic_only_and_persisted_on_admission():
    job = CareerOpportunity(job_title="Finance Systems Manager", posted_date="2026-08-25")
    admitted = DiscoveryQualityGate().admit([job], date(2026, 8, 25))
    assert admitted.admitted == [job]
    assert job.metadata["career_track"] == FINANCE_TECH
    assert job.metadata["opportunity_themes"] == ["FINANCE_SYSTEMS"]
