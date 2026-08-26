from app.models.career_opportunity import CareerOpportunity
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity
from app.services.job_discovery_service import JobDiscoveryService
from app.services.target_employer_discovery import TargetEmployerDiscovery
from app.services.target_employer_registry import TARGET_EMPLOYERS, TargetEmployer, normalize_employer_name, registry_summary
from app.services.discovery_route_snapshot import DiscoveryRouteSnapshotService

def test_registry_is_substantial_unique_and_covers_required_categories():
    categories, tiers, ats = registry_summary()
    assert 50 <= len(TARGET_EMPLOYERS) <= 100
    assert len({e.employer_id for e in TARGET_EMPLOYERS}) == len(TARGET_EMPLOYERS)
    assert {"ACCOUNTING_ADVISORY", "CONSULTING", "TECH_DATA", "FINANCIAL_SERVICES", "RECRUITER"} <= set(categories)
    assert {"Deloitte", "PwC", "EY", "KPMG", "Microsoft", "Visa", "JPMorgan Chase"} <= {e.name for e in TARGET_EMPLOYERS}
    assert normalize_employer_name("PricewaterhouseCoopers") == "pwc"
    assert normalize_employer_name("EY") == "ey"
    assert normalize_employer_name("CLA") == "cla"
    assert normalize_employer_name("Michael Page") == "pagegroup"

def test_public_ats_fixture_normalizes_with_provenance_and_official_application_url():
    discovery = TargetEmployerDiscovery(requester=lambda url: [{"id": "p-1", "text": "Finance Systems Manager (Remote)", "categories": {"location": "Remote"}, "descriptionPlain": "Work from anywhere", "hostedUrl": "https://jobs.lever.co/palantir/p-1", "applyUrl": "https://jobs.lever.co/palantir/p-1/apply"}])
    jobs = discovery.discover(1, employer_ids=("palantir",), market="united_kingdom", max_employers=1)
    job = jobs[0]
    assert job.source == "EmployerCareerSite" and job.work_arrangement == "REMOTE"
    assert job.market == ""  # Generic Lever board is not falsely labelled UK.
    assert job.application_url.endswith("/apply") and job.metadata["ats_platform"] == "LEVER"
    assert job.metadata["target_employer_id"] == "palantir"

def test_cross_source_duplicate_prefers_official_application_url_and_distinct_ids_remain():
    official = CareerOpportunity(source="EmployerCareerSite", id="one", company="Palantir", job_title="Finance Manager", application_url="https://employer.test/apply/1")
    linkedin = CareerOpportunity(source="LinkedIn", id="li", company="Palantir", job_title="Finance Manager", application_url="https://employer.test/apply/1?source=linkedin")
    distinct = CareerOpportunity(source="EmployerCareerSite", id="two", company="Palantir", job_title="Finance Manager", application_url="https://employer.test/apply/2")
    service = JobDiscoveryService()
    assert service.remove_duplicates([official, linkedin, distinct]) == [official, distinct]
    merged = service.remove_duplicates([linkedin, official])
    assert merged[0].source == "EmployerCareerSite"
    assert merged[0].metadata["discovered_sources"] == ["EmployerCareerSite", "LinkedIn"]
    history = ApplicationHistoryService(":memory:")
    history.claim_job(fingerprint_for_opportunity(official), status="APPLIED")
    assert history.is_duplicate(fingerprint_for_opportunity(official))
    history.close()


def test_employer_ats_rediscovery_matches_existing_cross_source_history_url():
    linkedin = CareerOpportunity(
        source="LinkedIn", id="li-1", company="Palantir", job_title="Finance Manager",
        job_url="https://source.test/job/1", application_url="https://employer.test/apply/1?via=linkedin",
    )
    employer = CareerOpportunity(
        source="EmployerCareerSite", id="ats-1", company="Palantir", job_title="Finance Manager",
        job_url="https://employer.test/apply/1", application_url="https://employer.test/apply/1",
    )
    history = ApplicationHistoryService(":memory:")
    history.claim_job(
        fingerprint_for_opportunity(linkedin), status="APPLIED",
        source=linkedin.source, application_url=linkedin.application_url,
    )
    assert history.duplicate_record_for_opportunity(employer)["status"] == "APPLIED"
    history.close()

def test_one_employer_failure_is_isolated_and_count_is_bounded():
    discovery = TargetEmployerDiscovery(requester=lambda url: (_ for _ in ()).throw(RuntimeError("endpoint down")))
    jobs = discovery.discover(1, employer_ids=("palantir", "databricks"), max_employers=1)
    assert jobs == [] and "palantir" in discovery.failures


def test_one_failed_employer_does_not_stop_another_public_endpoint():
    def requester(url):
        if "lever.co" in url:
            raise RuntimeError("endpoint down")
        return {"jobs": [{
            "id": 10, "title": "Finance Transformation Manager (Remote)",
            "location": {"name": "Remote"}, "content": "Fully remote.",
            "absolute_url": "https://boards.greenhouse.io/databricks/jobs/10",
        }]}
    discovery = TargetEmployerDiscovery(requester=requester)
    jobs = discovery.discover(1, employer_ids=("palantir", "databricks"), max_employers=2)
    assert [job.company for job in jobs] == ["Databricks"]
    assert "palantir" in discovery.failures


def test_greenhouse_fixture_normalizes_and_target_tier_never_changes_scores():
    payload = {"jobs": [{
        "id": 9,
        "title": "Financial Data Analyst - Remote",
        "location": {"name": "Remote"},
        "content": "Work from anywhere.",
        "absolute_url": "https://boards.greenhouse.io/databricks/jobs/9",
    }]}
    jobs = TargetEmployerDiscovery(requester=lambda url: payload).discover(
        1, employer_ids=("databricks",), max_employers=1
    )
    assert jobs[0].source == "EmployerCareerSite"
    assert jobs[0].metadata["ats_platform"] == "GREENHOUSE"
    assert jobs[0].raw_score == 0.0


def test_registry_can_represent_market_specific_ats_endpoints():
    employer = TargetEmployer(
        "example", "Example", (), "TECH_DATA", 1, "https://example.test",
        "WORKDAY", "global", ats_by_market=(("united_kingdom", "WORKDAY", "uk-tenant"),),
    )
    assert employer.ats_for_market("united_kingdom") == ("WORKDAY", "uk-tenant")
    assert employer.ats_for_market("australia") == ("WORKDAY", "global")


def test_catalogue_prefilter_applies_count_after_relevance_not_first_arbitrary_job():
    payload = {"jobs": [
        {"id": 1, "title": "Software Engineer", "location": {"name": "Remote"}, "content": "Build platform.", "absolute_url": "https://example/1"},
        {"id": 2, "title": "Data Engineer", "location": {"name": "Remote"}, "content": "Build data pipelines.", "absolute_url": "https://example/2"},
        {"id": 3, "title": "Finance Manager", "location": {"name": "Remote"}, "content": "Commercial finance leadership.", "absolute_url": "https://example/3"},
    ]}
    discovery = TargetEmployerDiscovery(requester=lambda _url: payload)
    jobs = discovery.discover(1, employer_ids=("databricks",), max_employers=1, scan_limit=10)
    assert [job.job_title for job in jobs] == ["Finance Manager"]
    assert discovery.diagnostics["databricks"] == {
        "ats": "GREENHOUSE", "catalogue_inspected": 3, "strong_relevant_candidates": 1,
        "ambiguous_candidates": 0, "irrelevant_skipped": 2, "returned": 1,
    }


def test_catalogue_scan_bound_and_deterministic_relevant_selection():
    payload = {"jobs": [
        {"id": index, "title": "Software Engineer" if index < 5 else "Senior FP&A Analyst", "location": {"name": "Remote"}, "content": "Finance planning." if index >= 5 else "Platform.", "absolute_url": f"https://example/{index}"}
        for index in range(8)
    ]}
    discovery = TargetEmployerDiscovery(requester=lambda _url: payload)
    first = discovery.discover(3, employer_ids=("databricks",), max_employers=1, scan_limit=6)
    second = discovery.discover(3, employer_ids=("databricks",), max_employers=1, scan_limit=6)
    assert [job.id for job in first] == ["5"] == [job.id for job in second]
    assert discovery.diagnostics["databricks"]["catalogue_inspected"] == 6


def test_validation_sampler_isolated_bypasses_relevance_and_only_returns_supported_ats(tmp_path):
    def requester(url):
        if "lever.co" in url:
            return [{"id":"l1", "text":"Sales Executive", "categories":{"location":"Office"}, "descriptionPlain":"Sell.", "hostedUrl":"https://jobs.lever.co/palantir/l1", "applyUrl":"https://jobs.lever.co/palantir/l1/apply"}]
        return {"jobs":[{"id":"g1", "title":"Software Engineer", "location":{"name":"Office"}, "content":"Build.", "absolute_url":"https://boards.greenhouse.io/databricks/jobs/g1"}]}
    discovery=TargetEmployerDiscovery(requester=requester)
    jobs=discovery.discover_validation_targets(2, max_employers=2, scan_limit=3)
    assert [job.application_portal for job in jobs] == ["LEVER", "GREENHOUSE"]
    assert [job.job_title for job in jobs] == ["Sales Executive", "Software Engineer"]
    assert all(job.metadata["validation_only"] for job in jobs)
    snapshot=DiscoveryRouteSnapshotService(tmp_path / "validation-routes.json")
    records=snapshot.save_validation_routes(jobs)
    assert len(records) == 2 and all(record["validation_only"] for record in records)
    assert all("tracker_id" not in record and "decision" not in record for record in records)
