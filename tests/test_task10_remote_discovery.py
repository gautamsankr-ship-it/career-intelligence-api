from app.config import JOB_SOURCES
from app.models.career_opportunity import CareerOpportunity
from app.services.application_email_classifier import ApplicationEmailClassifier, EmailClassification
from app.services.cache_service import CacheService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.job_search_config import DISCOVERY_QUERY_CYCLE, FINANCE_TECH_ROLE_FAMILIES, PROFESSIONAL_SERVICE_QUERIES, ROLE_FAMILIES, TARGET_MARKETS, indeed_searches, linkedin_market_searches, linkedin_searches
from app.services.job_sources import normalize_job_item
from app.services.linkedin_url_builder import LinkedInURLBuilder


def test_daily_sources_and_target_markets_are_centralized():
    assert JOB_SOURCES == ("linkedin", "indeed")
    assert tuple(market.key for market in TARGET_MARKETS) == (
        "united_kingdom", "united_states", "australia",
    )
    assert len(linkedin_searches()) == 15
    assert [search["max_results"] for search in indeed_searches(5)] == [5, 5, 5]


def test_role_families_cover_requested_finance_roles_without_duplicate_queries():
    covered = {role for family in ROLE_FAMILIES for role in family.covered_roles}
    assert {"Senior Accountant", "FP&A Manager", "Finance Business Partner", "Head of Finance", "Finance Automation"} <= covered
    queries = [family.query.casefold() for family in ROLE_FAMILIES]
    assert len(queries) == len(set(queries))
    assert [search["keyword"] for search in indeed_searches(3, rotation_index=0)] == [
        "Financial Accountant", "Financial Analyst", "Finance Manager",
    ]


def test_indeed_family_rotation_is_deterministic_and_avoids_generic_finance():
    first = indeed_searches(3, rotation_index=0)
    next_rotation = indeed_searches(3, rotation_index=1)

    assert [search["family"].key for search in first] == [
        "accounting_reporting", "fpa_analysis", "finance_management",
    ]
    assert [search["family"].key for search in next_rotation] == [
        "fpa_analysis", "finance_management", "controller_leadership",
    ]
    assert all(search["keyword"].casefold() != "finance" for search in first)


def test_professional_services_queries_are_part_of_the_same_budget_capped_rotation():
    professional_queries = {query.query for query in PROFESSIONAL_SERVICE_QUERIES}

    searches = indeed_searches(3, rotation_index=len(ROLE_FAMILIES))

    assert {search["keyword"] for search in searches} <= professional_queries
    assert len(DISCOVERY_QUERY_CYCLE) == len(ROLE_FAMILIES) + len(PROFESSIONAL_SERVICE_QUERIES) + len(FINANCE_TECH_ROLE_FAMILIES)


def test_linkedin_urls_have_all_markets_and_strict_remote_freshness_filters():
    searches = LinkedInURLBuilder().build_urls()
    assert {search["market"] for search in searches} == {market.key for market in TARGET_MARKETS}
    assert all("f_WT=2" in search["url"] and "f_TPR=r604800" in search["url"] for search in searches)


def test_linkedin_market_query_groups_are_diverse_and_rotation_keeps_all_families_reachable():
    first = linkedin_market_searches(TARGET_MARKETS[0], 3, rotation_index=0)
    later = linkedin_market_searches(TARGET_MARKETS[0], 3, rotation_index=3)

    assert [search["family"].key for search in first] == [
        "accounting_reporting", "fpa_analysis", "finance_management",
    ]
    assert len({search["keyword"] for search in first}) == 3
    assert {search["family"].key for search in later} != {search["family"].key for search in first}


def test_remote_is_retained_but_hybrid_onsite_and_unknown_are_excluded():
    discovery = JobDiscoveryService()
    jobs = [
        CareerOpportunity(id="remote", work_arrangement="REMOTE", location="London"),
        CareerOpportunity(id="hybrid", work_arrangement="HYBRID", location="Remote / Sydney"),
        CareerOpportunity(id="onsite", work_arrangement="ON_SITE", location="Remote, New York"),
        CareerOpportunity(id="unknown", location="Edinburgh", job_description="Flexible work from home opportunities"),
    ]

    assert [job.id for job in discovery.filter_remote_jobs(jobs)] == ["remote"]
    assert discovery.work_arrangement_counts(jobs) == {
        "REMOTE": 1, "HYBRID": 1, "ON_SITE": 1, "UNKNOWN": 1,
    }


def test_explicit_remote_status_beats_a_city_location_for_all_target_markets():
    discovery = JobDiscoveryService()
    jobs = [
        CareerOpportunity(id="au", market="australia", location="Sydney NSW", remote_status=True),
        CareerOpportunity(id="uk", market="united_kingdom", location="London", remote_status=True),
        CareerOpportunity(id="us", market="united_states", location="New York NY", remote_status=True),
    ]

    assert discovery.filter_remote_jobs(jobs) == jobs


def test_cross_query_and_cross_source_duplicates_remain_deduplicated():
    discovery = JobDiscoveryService()
    first = CareerOpportunity(source="LinkedIn", id="same-query", job_title="Analyst", remote_status=True)
    second = CareerOpportunity(
        source="LinkedIn", id="same-query", job_title="Analyst", remote_status=True,
        application_url="https://employer.example/apply/1?source=linkedin",
    )
    other_source = CareerOpportunity(
        source="Indeed", id="indeed-1", job_title="Analyst", remote_status=True,
        application_url="https://employer.example/apply/1?source=indeed",
    )
    first.application_url = "https://employer.example/apply/1?source=linkedin"

    assert discovery.remove_duplicates([first, second, other_source]) == [first]


def test_market_application_url_and_source_metadata_survive_cache_merge(tmp_path):
    cache = CacheService()
    cache.cache_dir = tmp_path
    cache.jobs_file = tmp_path / "raw_jobs.json"
    job = normalize_job_item(
        {
            "id": "uk-remote-1", "title": "Financial Controller", "company": "Example",
            "location": "Manchester", "is_remote": True, "work_mode": "Remote",
            "externalApplyLink": "https://example.test/apply", "description": "Must have UK right to work.",
        },
        "Indeed",
    )
    job.market = "united_kingdom"
    job.metadata["market"] = job.market
    cache.save_jobs([job])

    loaded = cache.load_jobs()[0]
    assert loaded.source == "Indeed"
    assert loaded.market == "united_kingdom"
    assert loaded.application_url == "https://example.test/apply"
    assert loaded.remote_scope == "REMOTE_COUNTRY_RESTRICTED"


def test_email_metadata_remains_non_authorizing_with_remote_discovery_fields():
    job = normalize_job_item(
        {
            "title": "Remote Finance Manager", "is_remote": True, "work_mode": "Remote",
            "description": "For privacy questions contact security@example.com.",
            "contact_email": "security@example.com",
        },
        "Indeed",
    )

    classified = ApplicationEmailClassifier().classify_opportunity(job)
    assert classified.classification == EmailClassification.CONTACT_ONLY_EMAIL
    assert classified.selected_email is None
