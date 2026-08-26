from app.models.career_opportunity import CareerOpportunity
from app.services.cache_service import CacheService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.job_search_config import PROFESSIONAL_SERVICE_QUERIES, TARGET_MARKETS, distribute_result_budget, indeed_searches, linkedin_searches
from app.services.job_sources import JobSourceAdapter, MultiSourceJobDiscovery, normalize_job_item
from refresh_jobs import cache_composition, resolve_sources


def test_default_sources_and_all_three_markets_are_active():
    assert resolve_sources(None) == ("linkedin", "indeed")
    assert {market.key for market in TARGET_MARKETS} == {"united_kingdom", "united_states", "australia"}
    assert {search["market"].key for search in linkedin_searches()} == {market.key for market in TARGET_MARKETS}
    assert {search["market"].key for search in indeed_searches(3)} == {market.key for market in TARGET_MARKETS}
    assert PROFESSIONAL_SERVICE_QUERIES


def test_source_count_is_bounded_per_market_and_each_market_gets_a_small_run_opportunity():
    assert distribute_result_budget(3, 3) == (1, 1, 1)
    assert distribute_result_budget(10, 3) == (4, 3, 3)
    assert [search["max_results"] for search in indeed_searches(20)] == [20, 20, 20]


def test_remote_eligibility_metadata_is_diagnostic_only():
    global_job = normalize_job_item({"title": "Financial Analyst", "is_remote": True, "work_mode": "Remote", "description": "Work from anywhere."}, "Indeed")
    restricted = normalize_job_item({"title": "Financial Analyst", "is_remote": True, "work_mode": "Remote", "description": "Candidates must reside in Australia."}, "Indeed")
    unclear = normalize_job_item({"title": "Financial Analyst", "is_remote": True, "work_mode": "Remote"}, "Indeed")
    assert global_job.remote_scope == "REMOTE_GLOBAL"
    assert restricted.remote_scope == "REMOTE_COUNTRY_RESTRICTED"
    assert unclear.remote_scope == "REMOTE_UNCLEAR"
    assert JobDiscoveryService().filter_remote_jobs([global_job, restricted, unclear]) == [global_job, restricted, unclear]


def test_cache_merges_source_market_scopes_without_erasing_other_markets(tmp_path):
    cache = CacheService()
    cache.cache_dir = tmp_path
    cache.jobs_file = tmp_path / "raw_jobs.json"
    old_uk = CareerOpportunity(source="Indeed", market="united_kingdom", id="uk-old")
    old_us = CareerOpportunity(source="Indeed", market="united_states", id="us-old")
    linkedin_au = CareerOpportunity(source="LinkedIn", market="australia", id="au-old")
    new_uk = CareerOpportunity(source="Indeed", market="united_kingdom", id="uk-new")
    cache.save_jobs([old_uk, old_us, linkedin_au])
    merged = cache.merge_refreshed_jobs([new_uk], ("indeed",), JobDiscoveryService().remove_duplicates, (("indeed", "united_kingdom"),))
    assert {(job.source, job.market, job.id) for job in merged} == {
        ("Indeed", "united_kingdom", "uk-new"), ("Indeed", "united_states", "us-old"), ("LinkedIn", "australia", "au-old"),
    }
    assert cache_composition(merged)["market"] == {"united_kingdom": 1, "united_states": 1, "australia": 1}


def test_empty_successful_scope_preserves_existing_cache_on_temporary_failure(tmp_path):
    cache = CacheService()
    cache.cache_dir = tmp_path
    cache.jobs_file = tmp_path / "raw_jobs.json"
    old = CareerOpportunity(source="Indeed", market="united_kingdom", id="old")
    cache.save_jobs([old])
    merged = cache.merge_refreshed_jobs([], ("indeed",), JobDiscoveryService().remove_duplicates, ())
    assert [(job.source, job.market, job.id) for job in merged] == [("Indeed", "united_kingdom", "old")]


def test_market_failure_is_reported_without_losing_successful_market_results():
    class PartialAdapter(JobSourceAdapter):
        source_name = "indeed"
        market_failures = {"united_states": "quota limited"}

        def discover(self, count):
            return [CareerOpportunity(source="Indeed", market="united_kingdom", id="uk-1")]

    result = MultiSourceJobDiscovery({"indeed": PartialAdapter()}).discover(("indeed",), 3)
    assert result.source_counts == {"indeed": 1}
    assert result.jobs[0].market == "united_kingdom"
    assert result.failures["indeed/united_states"] == "quota limited"


def test_distinct_same_title_vacancies_are_not_collapsed_without_shared_identifier():
    first = CareerOpportunity(source="Indeed", company="Example", job_title="Financial Analyst", location="London", job_description="Treasury reporting", market="united_kingdom")
    second = CareerOpportunity(source="Indeed", company="Example", job_title="Financial Analyst", location="London", job_description="FP&A planning", market="united_kingdom")
    assert JobDiscoveryService().remove_duplicates([first, second]) == [first, second]
