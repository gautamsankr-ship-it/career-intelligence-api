from app.models.career_opportunity import CareerOpportunity
from app.services.application_route_resolver import ApplicationRouteResolver
from app.services.job_discovery_service import JobDiscoveryService
from app.services.job_sources import normalize_job_item
from app.services.target_employer_discovery import TargetEmployerDiscovery


def test_linkedin_metadata_preserves_listing_and_external_ats_route():
    job = normalize_job_item({"id": "l1", "title": "Finance Manager", "company": "Example", "link": "https://uk.linkedin.com/jobs/view/1", "applicationUrl": "https://boards.greenhouse.io/example/jobs/1", "description": "Fully remote"}, "LinkedIn")
    assert job.source_listing_url.startswith("https://uk.linkedin.com/jobs/view/")
    assert job.application_url.startswith("https://boards.greenhouse.io/")
    assert (job.application_url_type, job.application_portal, job.application_route_confidence, job.application_route_status) == ("ATS_URL", "GREENHOUSE", "HIGH", "RESOLVED")


def test_linkedin_and_indeed_source_only_are_retained():
    for source, url in (("LinkedIn", "https://uk.linkedin.com/jobs/view/1"), ("Indeed", "https://www.indeed.com/viewjob?jk=1")):
        job = normalize_job_item({"id": source, "title": "Financial Accountant", "company": "Example", "link": url, "description": "Fully remote"}, source)
        assert job.source_listing_url == url and not job.application_url and job.application_route_status == "SOURCE_ONLY"


def test_target_employer_route_is_direct_and_browser_ready():
    source = TargetEmployerDiscovery(requester=lambda url: [{"id": "1", "text": "Finance Systems Manager", "categories": {"location": "Remote"}, "descriptionPlain": "Work from anywhere", "hostedUrl": "https://jobs.lever.co/palantir/1", "applyUrl": "https://jobs.lever.co/palantir/1/apply"}])
    job = source.discover(1, employer_ids=("palantir",), max_employers=1)[0]
    assert (job.application_url_type, job.application_portal, job.application_route_confidence, job.application_route_status) == ("ATS_URL", "LEVER", "HIGH", "DIRECT_ROUTE")


def test_professional_source_style_listing_keeps_both_urls():
    job = normalize_job_item({"id": "p1", "title": "Audit Manager", "company": "Example", "link": "https://professional.example/jobs/1", "externalApplyLink": "https://company.example/careers/apply/1", "description": "Fully remote"}, "ACCA Careers")
    assert job.source_listing_url.endswith("/jobs/1") and job.application_url.endswith("/apply/1")
    assert job.application_url_source == "DISCOVERY_METADATA:externalApplyLink"


def test_invalid_route_is_rejected():
    job = normalize_job_item({"title": "Finance Manager", "company": "Example", "link": "https://uk.linkedin.com/jobs/view/1", "applicationUrl": "javascript:alert(1)"}, "LinkedIn")
    assert not job.application_url and job.application_route_status == "SOURCE_ONLY"


def test_cross_source_enrichment_and_downgrade_protection():
    linkedin = CareerOpportunity(source="LinkedIn", company="Example", job_title="Finance Manager", location="London", posted_date="2026-08-25", job_url="https://linkedin.com/jobs/view/1", source_listing_url="https://linkedin.com/jobs/view/1", application_route_status="SOURCE_ONLY")
    lever = CareerOpportunity(source="EmployerCareerSite", company="Example", job_title="Finance Manager", location="London", posted_date="2026-08-25", job_url="https://jobs.lever.co/example/1", source_listing_url="https://jobs.lever.co/example/1", application_url="https://jobs.lever.co/example/1/apply", application_url_type="ATS_URL", application_portal="LEVER", application_route_confidence="HIGH", application_route_status="DIRECT_ROUTE")
    merged = JobDiscoveryService().remove_duplicates([linkedin, lever])
    assert len(merged) == 1 and merged[0].application_portal == "LEVER" and merged[0].application_route_confidence == "HIGH"


def test_public_listing_external_route_resolution_fixture():
    route = ApplicationRouteResolver().resolve({"job_url": "https://www.indeed.com/viewjob?jk=1"}, '<a href="https://jobs.lever.co/example/1/apply">Apply on employer careers</a>')
    assert route.resolution_status == "RESOLVED" and route.application_url_type == "ATS_URL"
