from app.models.career_opportunity import CareerOpportunity
from app.services.application_history_service import fingerprint_for_opportunity
from app.services.application_email_classifier import (
    ApplicationEmailClassifier,
    EmailClassification,
)
from app.services.cache_service import CacheService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.job_sources import (
    ApifyJobSourceAdapter,
    ApifyRunFailedError,
    IndeedSourceAdapter,
    JobSourceAdapter,
    LinkedInSourceAdapter,
    MultiSourceJobDiscovery,
    normalize_job_item,
)
from app.config import JOB_SOURCES, JOB_SOURCE_STATUS, OPTIONAL_JOB_SOURCES
from app.services.job_search_config import DISCOVERY_QUERY_CYCLE
from refresh_jobs import resolve_sources


class FakeApifyActor:
    def __init__(self, run):
        self.run = run
        self.run_input = None
        self.run_inputs = []

    def call(self, run_input):
        self.run_input = run_input
        self.run_inputs.append(run_input)
        return self.run


class FakeDataset:
    def __init__(self, items=()):
        self.items = items

    def iterate_items(self):
        return iter(self.items)


class FakeApifyClient:
    def __init__(self, run, items=()):
        self.actor_client = FakeApifyActor(run)
        self.dataset_client = FakeDataset(items)

    def actor(self, actor_id):
        return self.actor_client

    def dataset(self, dataset_id):
        return self.dataset_client


class FakeSource(JobSourceAdapter):
    def __init__(self, source_name, jobs=None, error=None):
        self.source_name = source_name
        self.jobs = jobs or []
        self.error = error
        self.count = None

    def discover(self, count):
        self.count = count
        if self.error:
            raise self.error
        return self.jobs


class FakeLinkedInScraper:
    def __init__(self):
        self.calls = []

    def scrape_jobs(self, urls, count):
        self.calls.append((urls, count))
        return [CareerOpportunity(metadata={"inputUrl": urls[0]})]


def test_linkedin_apify_normalization_preserves_active_fields():
    job = CareerOpportunity.from_apify(
        {
            "id": "linkedin-1",
            "title": "Finance Manager",
            "companyName": "Example Co",
            "location": "Remote, Australia",
            "descriptionText": "Remote finance role",
            "link": "https://linkedin.example/jobs/1",
            "applicationUrl": "https://employer.example/apply/1",
            "remote": True,
        }
    )

    assert job.source == "LinkedIn"
    assert job.id == "linkedin-1"
    assert job.application_url == "https://employer.example/apply/1"
    assert job.remote_status is True


def test_default_sources_include_only_verified_daily_sources():
    assert JOB_SOURCES == ("linkedin", "indeed")
    assert resolve_sources(None) == ("linkedin", "indeed")
    assert "seek" not in resolve_sources(None)
    assert "hays" not in resolve_sources(None)
    assert JOB_SOURCE_STATUS == {
        "linkedin": "VERIFIED",
        "indeed": "VERIFIED",
        "seek": "UNRELIABLE",
        "hays": "UNCONFIGURED",
        "robert_half": "UNRELIABLE",
    }
    assert set(OPTIONAL_JOB_SOURCES) == {"robert_half", "seek", "hays"}


def test_seek_remains_explicitly_requestable_without_default_execution():
    seek = FakeSource("seek", [CareerOpportunity(source="Seek", id="seek-1")])
    discovery = MultiSourceJobDiscovery({"seek": seek})

    result = discovery.discover(resolve_sources("seek"), count=5)

    assert seek.count == 5
    assert result.source_counts == {"seek": 1}


def test_indeed_actual_actor_remote_fields_are_normalized():
    job = normalize_job_item(
        {
            "platform": "Indeed",
            "platform_url": "https://au.indeed.com/viewjob?jk=abc",
            "title": "Senior Accountant",
            "company_name": "Indeed Employer",
            "location": {"raw": "Sydney NSW"},
            "description": "Full job description",
            "official_url": "https://employer.example/apply/indeed-1",
            "posted_date": "2026-08-24",
            "is_remote": True,
            "work_mode": "Remote",
        },
        "Indeed",
    )

    assert job.source == "Indeed"
    assert job.location == "Sydney NSW"
    assert job.job_url.endswith("viewjob?jk=abc")
    assert job.remote_status is True
    assert job.work_arrangement == "REMOTE"
    assert job.metadata["work_arrangement_source"] == "work_mode"


def test_indeed_runs_one_strict_remote_query_per_market_with_a_per_market_budget():
    client = FakeApifyClient(
        {"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"},
        [{"id": "remote-1", "is_remote": True, "work_mode": "Remote"}],
    )

    jobs = IndeedSourceAdapter(client=client, rotation_index=0).discover(3)

    assert [payload["country"] for payload in client.actor_client.run_inputs] == [
        "United Kingdom", "United States", "Australia",
    ]
    assert all(payload["remote_only"] is True for payload in client.actor_client.run_inputs)
    assert [payload["max_results"] for payload in client.actor_client.run_inputs] == [3, 3, 3]
    assert [payload["keyword"] for payload in client.actor_client.run_inputs] == [
        "Financial Accountant", "Financial Analyst", "Finance Manager",
    ]
    assert [job.market for job in jobs] == ["united_kingdom", "united_states", "australia"]


def test_indeed_rotation_advances_after_a_successful_refresh(tmp_path):
    state_path = tmp_path / "indeed_rotation.json"
    first_client = FakeApifyClient({"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"})
    IndeedSourceAdapter(client=first_client, rotation_state_path=state_path).discover(3)

    second_client = FakeApifyClient({"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"})
    IndeedSourceAdapter(client=second_client, rotation_state_path=state_path).discover(3)

    first_keywords = [payload["keyword"] for payload in first_client.actor_client.run_inputs]
    second_keywords = [payload["keyword"] for payload in second_client.actor_client.run_inputs]
    queries = [family.query for family in DISCOVERY_QUERY_CYCLE]
    first_index = queries.index(first_keywords[0])
    assert second_keywords == [queries[(first_index + offset) % len(queries)] for offset in (1, 2, 3)]


def test_linkedin_requests_the_full_bounded_count_independently_per_market():
    scraper = FakeLinkedInScraper()

    jobs = LinkedInSourceAdapter(scraper=scraper, rotation_index=0).discover(3)

    assert [count for _, count in scraper.calls] == [3, 3, 3]
    assert all(len(urls) == 3 for urls, _ in scraper.calls)
    assert [job.market for job in jobs] == ["united_kingdom", "united_states", "australia"]


def test_linkedin_market_runs_use_distinct_finance_query_groups_without_cross_market_starvation():
    scraper = FakeLinkedInScraper()

    LinkedInSourceAdapter(scraper=scraper, rotation_index=0).discover(3)

    assert len(scraper.calls) == 3
    assert [count for _, count in scraper.calls] == [3, 3, 3]
    assert "geoId=101165590" in scraper.calls[0][0][0]
    assert "geoId=103644278" in scraper.calls[1][0][0]
    assert "geoId=101452733" in scraper.calls[2][0][0]
    assert all(len(set(urls)) == 3 for urls, _ in scraper.calls)


def test_indeed_actual_work_mode_distinguishes_onsite_and_hybrid():
    onsite = normalize_job_item({"is_remote": False, "work_mode": "On-site"}, "Indeed")
    hybrid = normalize_job_item({"work_mode": "Hybrid"}, "Indeed")

    assert onsite.remote_status is False
    assert onsite.work_arrangement == "ON_SITE"
    assert hybrid.remote_status is None
    assert hybrid.work_arrangement == "HYBRID"


def test_missing_indeed_work_arrangement_stays_unknown():
    job = normalize_job_item({"title": "Financial Analyst"}, "Indeed")

    assert job.remote_status is None
    assert job.work_arrangement == "UNKNOWN"
    assert "work_arrangement_source" not in job.metadata


def test_mapped_indeed_remote_job_survives_existing_remote_filter():
    job = normalize_job_item(
        {"title": "Remote Analyst", "is_remote": True, "work_mode": "Remote"},
        "Indeed",
    )

    assert JobDiscoveryService().filter_remote_jobs([job]) == [job]


def test_seek_normalization_and_missing_fields_are_safe():
    job = normalize_job_item(
        {"id": "seek-1", "title": "Analyst", "companyName": "Seek Employer", "remote": False},
        "Seek",
    )

    assert job.source == "Seek"
    assert job.job_description == ""
    assert job.job_url == ""
    assert job.application_url == ""
    assert job.remote_status is False


def test_hays_normalization_preserves_public_listing_data():
    job = normalize_job_item(
        {
            "job_id": "hays-1",
            "jobTitle": "Finance Officer",
            "employer": "Hays",
            "jobLocation": "Melbourne VIC",
            "jobDescription": "Hybrid role",
            "jobUrl": "https://www.hays.com.au/job/1",
            "apply_url": "https://www.hays.com.au/job/1/apply",
        },
        "Hays",
    )

    assert job.source == "Hays"
    assert job.company == "Hays"
    assert job.application_url.endswith("/apply")


def test_robert_half_normalization_preserves_structured_public_listing_fields():
    job = normalize_job_item(
        {
            "Unique Job Number": "06810-0013484734",
            "Job Title": "Financial Analyst",
            "Source": "Robert Half",
            "Location": "Melbourne VIC",
            "Description": "Please apply through the job page. Contact security@example.com for privacy queries.",
            "Job Detail URL": "https://www.roberthalf.com/au/en/job/06810-0013484734",
            "Date Posted": "2026-08-24",
            "Remote": "remote",
            "RH Contact Email": "security@example.com",
        },
        "Robert Half",
    )

    assert job.source == "Robert Half"
    assert job.id == "06810-0013484734"
    assert job.application_url == ""
    assert job.remote_status is True
    assert "security@example.com" in job.metadata["RH Contact Email"]


def test_source_email_metadata_does_not_authorize_an_application_recipient():
    job = normalize_job_item(
        {
            "title": "Analyst",
            "description": "For privacy questions contact security@example.com.",
            "RH Contact Email": "security@example.com",
        },
        "Robert Half",
    )

    result = ApplicationEmailClassifier().classify_opportunity(job)

    assert result.classification == EmailClassification.CONTACT_ONLY_EMAIL
    assert result.selected_email is None


def test_application_url_deduplicates_matching_cross_source_jobs():
    linked_in = normalize_job_item(
        {"id": "linkedin-1", "title": "Analyst", "company": "Example", "applicationUrl": "https://employer.example/apply/1?source=linkedin"},
        "LinkedIn",
    )
    indeed = normalize_job_item(
        {"jobId": "indeed-99", "positionName": "Analyst", "company": "Example", "externalApplyLink": "https://employer.example/apply/1?source=indeed"},
        "Indeed",
    )

    jobs = JobDiscoveryService().remove_duplicates([linked_in, indeed])

    assert len(jobs) == 1
    assert jobs[0].source == "LinkedIn"


def test_failed_source_does_not_stop_other_sources():
    linked_in = CareerOpportunity(source="LinkedIn", id="1", job_title="Analyst")
    discovery = MultiSourceJobDiscovery(
        {
            "linkedin": FakeSource("linkedin", [linked_in]),
            "indeed": FakeSource("indeed", error=RuntimeError("Indeed unavailable")),
            "seek": FakeSource("seek", []),
        }
    )

    result = discovery.discover(("linkedin", "indeed", "seek"), count=3)

    assert result.jobs == [linked_in]
    assert result.source_counts == {"linkedin": 1, "indeed": 0, "seek": 0}
    assert result.failures["indeed"] == "Indeed unavailable"


def test_failed_apify_run_raises_clear_source_error():
    adapter = ApifyJobSourceAdapter(
        "seek",
        "configured-seek-actor",
        lambda count: {"urls": ["https://www.seek.com.au/jobs"], "max_items_per_url": count},
        client=FakeApifyClient({"status": "FAILED", "defaultDatasetId": "dataset-1"}),
    )

    try:
        adapter.discover(5)
        raise AssertionError("Expected failed actor status to raise")
    except ApifyRunFailedError as exc:
        assert "seek" in str(exc)
        assert "FAILED" in str(exc)


def test_failed_seek_actor_is_reported_while_other_source_continues():
    seek = ApifyJobSourceAdapter(
        "seek",
        "configured-seek-actor",
        lambda count: {},
        client=FakeApifyClient({"status": "ABORTED"}),
    )
    linked_in = CareerOpportunity(source="LinkedIn", id="1", job_title="Analyst")
    result = MultiSourceJobDiscovery(
        {"linkedin": FakeSource("linkedin", [linked_in]), "seek": seek}
    ).discover(("linkedin", "seek"), count=5)

    assert result.jobs == [linked_in]
    assert result.source_counts["seek"] == 0
    assert "ABORTED" in result.failures["seek"]


def test_successful_apify_run_with_zero_jobs_is_not_a_failure():
    adapter = ApifyJobSourceAdapter(
        "seek",
        "configured-seek-actor",
        lambda count: {},
        client=FakeApifyClient({"status": "SUCCEEDED", "defaultDatasetId": "dataset-1"}),
    )
    result = MultiSourceJobDiscovery({"seek": adapter}).discover(("seek",), count=5)

    assert result.jobs == []
    assert result.source_counts == {"seek": 0}
    assert result.failures == {}


def test_normalized_job_survives_cache_and_fingerprint_pipeline(tmp_path):
    job = normalize_job_item(
        {"id": "seek-1", "title": "Analyst", "company": "Example", "location": "Remote", "description": "remote reporting role", "url": "https://seek.example/1"},
        "Seek",
    )
    cache = CacheService()
    cache.cache_dir = tmp_path
    cache.jobs_file = tmp_path / "raw_jobs.json"
    cache.save_jobs([job])

    loaded = cache.load_jobs()[0]

    assert loaded.source == "Seek"
    assert loaded.application_url == ""
    assert fingerprint_for_opportunity(loaded)


def test_cache_merges_refreshed_source_without_erasing_other_sources(tmp_path):
    cache = CacheService()
    cache.cache_dir = tmp_path
    cache.jobs_file = tmp_path / "raw_jobs.json"
    linked_in = CareerOpportunity(source="LinkedIn", id="linkedin-1", job_title="LinkedIn role")
    old_indeed = CareerOpportunity(source="Indeed", id="indeed-old", job_title="Old Indeed role")
    new_indeed = CareerOpportunity(source="Indeed", id="indeed-new", job_title="New Indeed role")
    cache.save_jobs([linked_in, old_indeed])

    merged = cache.merge_refreshed_jobs(
        [new_indeed],
        ("indeed",),
        JobDiscoveryService().remove_duplicates,
    )

    assert {(job.source, job.id) for job in merged} == {
        ("LinkedIn", "linkedin-1"),
        ("Indeed", "indeed-new"),
    }
