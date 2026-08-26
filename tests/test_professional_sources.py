from app.config import JOB_SOURCES
from app.models.career_opportunity import CareerOpportunity
from app.services.discovery_quality_gate import CORE_FINANCE, DISCOVERY_IRRELEVANT, DISCOVERY_RELEVANT, FINANCE_TECH, DiscoveryQualityGate
from app.services.job_discovery_service import JobDiscoveryService
from app.services.professional_source_registry import PROFESSIONAL_JOB_SOURCES, source_summary

def test_professional_registry_has_acca_as_high_priority_and_no_unverified_enabled_source():
    acca = next(source for source in PROFESSIONAL_JOB_SOURCES if source.source_id == "acca")
    assert acca.priority == 1 and acca.category == "PROFESSIONAL_ACCOUNTING"
    assert source_summary()["SUPPORTED"] == 0 and JOB_SOURCES == ("linkedin", "indeed")

def test_professional_source_fixture_preserves_provenance_and_existing_taxonomy():
    job = CareerOpportunity(source="ACCA Careers", id="acca-1", company="Example", job_title="Senior Financial Accountant", work_arrangement="REMOTE", location="Remote")
    relevance = DiscoveryQualityGate().classify_relevance(job)
    assert job.source == "ACCA Careers" and relevance.classification == DISCOVERY_RELEVANT and relevance.career_track == CORE_FINANCE
    assert JobDiscoveryService().filter_remote_jobs([job]) == [job]

def test_finance_tech_and_professional_services_titles_remain_compatible():
    transformation = DiscoveryQualityGate().classify_relevance(CareerOpportunity(job_title="Finance Transformation Manager"))
    audit = DiscoveryQualityGate().classify_relevance(CareerOpportunity(job_title="Audit Manager", company="Accounting Firm", job_description="Audit and assurance."))
    assert transformation.classification == DISCOVERY_RELEVANT and transformation.career_track == FINANCE_TECH
    assert audit.classification == DISCOVERY_RELEVANT

def test_financial_board_does_not_admit_generic_engineering_and_cross_source_url_dedup_still_works():
    irrelevant = DiscoveryQualityGate().classify_relevance(CareerOpportunity(source="eFinancialCareers", job_title="Software Engineer", company="FinTech"))
    acca = CareerOpportunity(source="ACCA Careers", id="1", company="Example", job_title="Finance Manager", application_url="https://employer.test/apply/1")
    linkedin = CareerOpportunity(source="LinkedIn", id="2", company="Example", job_title="Finance Manager", application_url="https://employer.test/apply/1?src=li")
    assert irrelevant.classification == DISCOVERY_IRRELEVANT
    assert len(JobDiscoveryService().remove_duplicates([acca, linkedin])) == 1
