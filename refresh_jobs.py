import argparse
from collections import Counter

from app.config import JOB_SOURCE_MAX_RESULTS, JOB_SOURCES
from app.services.job_discovery_service import JobDiscoveryService
from app.services.cache_service import CacheService
from app.services.discovery_route_snapshot import DiscoveryRouteSnapshotService
from app.services.discovery_quality_gate import (
    DISCOVERY_AMBIGUOUS,
    DISCOVERY_IRRELEVANT,
    DISCOVERY_RELEVANT,
    FRESH,
    FRESHNESS_UNKNOWN,
    STALE,
    DiscoveryQualityGate,
)
from app.services.job_sources import MultiSourceJobDiscovery
from app.services.job_search_config import TARGET_MARKETS


SOURCE_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "robert_half": "Robert Half",
    "seek": "SEEK",
    "hays": "Hays",
    "target_employers": "Target employers",
}


def resolve_sources(value: str | None) -> tuple[str, ...]:
    selected = value if value is not None else ",".join(JOB_SOURCES)
    return tuple(source.strip().lower() for source in selected.split(",") if source.strip())


def cache_composition(jobs):
    def count(values):
        result = {}
        for value in values:
            result[value or "UNKNOWN"] = result.get(value or "UNKNOWN", 0) + 1
        return result
    return {
        "source": count(job.source for job in jobs),
        "market": count(job.market for job in jobs),
        "arrangement": count(job.work_arrangement for job in jobs),
        "eligibility": count(job.remote_scope for job in jobs),
    }


def print_cache_inspection(jobs):
    print("CACHE COMPOSITION")
    print(f"Total cached jobs: {len(jobs)}")
    for label, values in cache_composition(jobs).items():
        print(f"By {label}:")
        for key, value in sorted(values.items()):
            print(f"  {key}: {value}")


def main():

    parser = argparse.ArgumentParser(description="Refresh the normalized multi-source job cache.")
    parser.add_argument("--sources", default=None, help="Comma-separated sources to run.")
    parser.add_argument("--count", type=int, default=JOB_SOURCE_MAX_RESULTS, help="Approximate raw results requested per target market and enabled source.")
    parser.add_argument("--inspect-cache", action="store_true", help="Show cache composition without discovery or scoring.")
    parser.add_argument("--employers", help="Comma-separated target employer IDs (target_employers only).")
    parser.add_argument("--market", choices=[market.key for market in TARGET_MARKETS], help="Target-employer market filter.")
    parser.add_argument("--tier", type=int, choices=(1, 2, 3), help="Target-employer tier filter.")
    parser.add_argument("--max-employers", type=int, default=3, help="Maximum target employers checked (target_employers only).")
    parser.add_argument("--scan-limit", type=int, default=100, help="Maximum ATS catalogue items inspected per target employer.")
    args = parser.parse_args()
    if args.inspect_cache:
        print_cache_inspection(CacheService().load_jobs())
        return
    if args.count < 1:
        parser.error("--count must be at least 1")
    sources = resolve_sources(args.sources)

    print("\n" + "=" * 80)
    print("MULTI-SOURCE JOB REFRESH")
    print("=" * 80)

    multi_source = MultiSourceJobDiscovery()
    if "target_employers" in sources:
        multi_source.adapters["target_employers"].configure(
            employer_ids=tuple(value.strip() for value in (args.employers or "").split(",") if value.strip()),
            market=args.market, tier=args.tier, max_employers=args.max_employers, scan_limit=args.scan_limit,
        )
    result = multi_source.discover(sources, args.count)
    discovery = JobDiscoveryService()
    cache = CacheService()
    raw_total = len(result.jobs)
    normalized_jobs = discovery.remove_duplicates(result.jobs)
    duplicates_removed = raw_total - len(normalized_jobs)
    normalized_total = len(normalized_jobs)
    # Diagnostic-only route retention is intentionally before relevance,
    # freshness, and remote-only admission. It never feeds the normal cache.
    DiscoveryRouteSnapshotService().save_routes(normalized_jobs)
    quality = DiscoveryQualityGate().admit(normalized_jobs)
    # Show normalized source arrangements across all deduplicated records.
    # Admission still follows relevance -> freshness -> strict remote below.
    arrangement_counts = discovery.work_arrangement_counts(normalized_jobs)
    cache.save_arrangement_review_jobs([
        job for job in quality.admitted
        if discovery.work_arrangement(job) == "UNKNOWN"
    ])
    remote_jobs = discovery.filter_remote_jobs(quality.admitted)
    successful_sources = tuple(source for source in sources if source not in result.failures)
    successful_scopes = tuple(
        (source, market.key)
        for source in sources
        for market in TARGET_MARKETS
        if f"{source}/{market.key}" not in result.failures and source not in result.failures
    )
    jobs = cache.merge_refreshed_jobs(
        remote_jobs,
        successful_sources,
        discovery.remove_duplicates,
        successful_scopes,
    )
    jobs = discovery.filter_remote_jobs(jobs)
    jobs.sort(key=lambda x: x.posted_date, reverse=True)
    cache.save_jobs(jobs)

    print("Target markets: " + ", ".join(market.label for market in TARGET_MARKETS))
    print("Enabled sources: " + ", ".join(SOURCE_LABELS.get(source, source) for source in sources))
    print(f"Requested count per market/source: {args.count}")
    for source in ("linkedin", "indeed"):
        if source in sources:
            print(f"{SOURCE_LABELS[source]} requested: {args.count} per market (up to {args.count * len(TARGET_MARKETS)} raw)")
            print(f"{SOURCE_LABELS[source]} discovered:")
            for market in TARGET_MARKETS:
                if source in result.failures and "ACTOR_LIMIT" in result.failures[source]:
                    value = "SKIPPED - ACTOR_LIMIT"
                else:
                    value = sum(1 for job in result.jobs if job.source.lower() == SOURCE_LABELS[source].lower() and job.market == market.key)
                print(f"  {market.label}: {value}")
            print(f"  Total: {result.source_counts.get(source, 0)}")
    if "target_employers" in sources:
        employer_jobs = [job for job in result.jobs if job.source == "EmployerCareerSite"]
        categories = {}
        for job in employer_jobs:
            category = (job.metadata or {}).get("employer_category", "UNKNOWN")
            categories[category] = categories.get(category, 0) + 1
        print("Target employers discovered:")
        print(f"  Total: {len(employer_jobs)}")
        for category, value in sorted(categories.items()):
            print(f"  {category}: {value}")
        for employer, diagnostic in multi_source.adapters["target_employers"].diagnostics.items():
            print(f"  {employer}: ATS={diagnostic['ats']} | Catalogue inspected={diagnostic['catalogue_inspected']} | Relevant={diagnostic['strong_relevant_candidates']} | Returned={diagnostic['returned']}")
    if "seek" not in sources:
        print("SEEK: DISABLED BY DEFAULT")
    if "hays" not in sources:
        print("Hays: DISABLED BY DEFAULT")
    if "robert_half" not in sources:
        print("Robert Half: DISABLED BY DEFAULT (UNRELIABLE)")
    print(f"Total raw: {raw_total}")
    print(f"Cross-query/source duplicates removed: {duplicates_removed}")
    print(f"Normalized jobs: {normalized_total}")
    print(f"Discovery relevant: {quality.relevance_counts[DISCOVERY_RELEVANT]}")
    print(f"Discovery irrelevant excluded: {quality.relevance_counts[DISCOVERY_IRRELEVANT]}")
    print(f"Discovery ambiguous excluded: {quality.relevance_counts[DISCOVERY_AMBIGUOUS]}")
    print(f"Fresh <=7 days: {quality.freshness_counts[FRESH]}")
    print(f"Stale >7 days excluded: {quality.freshness_counts[STALE]}")
    print(f"Freshness unknown: {quality.freshness_counts[FRESHNESS_UNKNOWN]}")
    print(f"Remote: {arrangement_counts['REMOTE']}")
    print(f"Hybrid excluded: {arrangement_counts['HYBRID']}")
    print(f"On-site excluded: {arrangement_counts['ON_SITE']}")
    print(f"Unknown arrangement excluded: {arrangement_counts['UNKNOWN']}")
    print(f"Professional-services relevant: {quality.professional_services_relevant}")
    print(f"Accounting/Audit/Tax/Risk/Advisory relevant: {quality.professional_services_relevant}")
    career_tracks = {"CORE_FINANCE": 0, "FINANCE_TECH": 0, "BOTH": 0, "UNKNOWN": 0}
    theme_counts = {}
    for job in normalized_jobs:
        metadata = job.metadata or {}
        career_tracks[metadata.get("career_track", "UNKNOWN")] = career_tracks.get(metadata.get("career_track", "UNKNOWN"), 0) + 1
        for theme in metadata.get("opportunity_themes", []):
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
    print("Career track: " + ", ".join(f"{track}={count}" for track, count in career_tracks.items()))
    if theme_counts:
        print("Finance-Tech themes: " + ", ".join(f"{theme}={count}" for theme, count in sorted(theme_counts.items())))
    print(f"Remote jobs retained for processing: {len(remote_jobs)}")
    route_counts = Counter(job.application_url_type or "SOURCE_ONLY" for job in normalized_jobs)
    browser_ready = sum(bool(job.application_url) and job.application_route_confidence in {"HIGH", "MEDIUM"} for job in normalized_jobs)
    print("Application routes: " + ", ".join(f"{kind}={count}" for kind, count in sorted(route_counts.items())))
    print(f"Browser-ready routes: {browser_ready} | Source-only/unresolved: {sum(not job.application_url for job in normalized_jobs)}")
    print(f"Remote eligibility - Global: {sum(job.remote_scope == 'REMOTE_GLOBAL' for job in normalized_jobs)}")
    print(f"Remote eligibility - Country-restricted: {sum(job.remote_scope == 'REMOTE_COUNTRY_RESTRICTED' for job in normalized_jobs)}")
    print(f"Remote eligibility - Unclear: {sum(job.remote_scope == 'REMOTE_UNCLEAR' for job in normalized_jobs)}")
    print(f"New jobs retained this refresh: {len(remote_jobs)}")
    print(f"Total jobs currently in cache: {len(jobs)}")
    print(f"Sources/markets failed: {len(result.failures)}")
    for source, error in result.failures.items():
        print(f"  {source}: {error}")
    print("=" * 80)


if __name__ == "__main__":

    main()
