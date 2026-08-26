"""Offline application-route audit/enrichment using already stored discovery evidence."""
from __future__ import annotations
import argparse
from app.services.validation_target_selector import ValidationTargetSelector
from collections import Counter
from app.services.application_history_service import ApplicationHistoryService
from app.services.application_route_resolver import ApplicationRouteResolver
from app.services.cache_service import CacheService
from app.services.discovery_route_snapshot import DiscoveryRouteSnapshotService

def _audit(records):
    types=Counter(); portals=Counter(); statuses=Counter()
    for r in records:
        types[r.get("application_url_type") or ("SOURCE_ONLY" if not r.get("application_url") else "UNKNOWN_URL")] += 1
        portals[r.get("application_portal") or "UNKNOWN"] += 1; statuses[r.get("application_route_status") or "APPLICATION_ROUTE_UNRESOLVED"] += 1
    total=len(records); direct=sum(1 for r in records if (r.get("application_route_confidence") in {"HIGH","MEDIUM"} and r.get("application_url")))
    print("APPLICATION ROUTE AUDIT"); print(f"Tracked vacancies: {total}\nValidated direct routes: {direct}\nRoute coverage: {(100*direct/total if total else 0):.1f}%")
    print("Route types:"); [print(f"  {k}: {v}") for k,v in sorted(types.items())]
    print("Portals:"); [print(f"  {k}: {v}") for k,v in sorted(portals.items())]
    print("Statuses:"); [print(f"  {k}: {v}") for k,v in sorted(statuses.items())]

def _apply_route(obj, route):
    obj.source_listing_url = obj.source_listing_url or obj.job_url
    if route.resolution_status == "RESOLVED" and route.route_confidence in {"HIGH","MEDIUM"}:
        obj.application_url=route.application_url; obj.application_url_type=route.application_url_type; obj.application_url_source=route.application_url_source; obj.application_portal=route.portal; obj.application_route_confidence=route.route_confidence; obj.application_route_resolved_at=route.resolved_at; obj.application_route_status=route.resolution_status
        return True
    obj.application_route_status = "SOURCE_ONLY" if obj.source_listing_url else "APPLICATION_ROUTE_UNRESOLVED"; return False

def main():
    parser=argparse.ArgumentParser(description="Offline application-route intelligence")
    sub=parser.add_subparsers(dest="command", required=True); sub.add_parser("audit"); backfill=sub.add_parser("backfill"); sub.add_parser("cache-list"); discovery=sub.add_parser("discovery-list"); discovery.add_argument("--portal"); discovery.add_argument("--company"); discovery.add_argument("--browser-ready", action="store_true"); targets=sub.add_parser("validation-targets"); targets.add_argument("--portal"); targets.add_argument("--company"); targets.add_argument("--browser-ready", action="store_true"); targets.add_argument("--probe", action="store_true"); targets.add_argument("--refresh", action="store_true", help="Re-probe selected cached validation routes."); targets.add_argument("--limit", type=int, default=5); targets.add_argument("--best", action="store_true"); validation_discovery=sub.add_parser("discover-validation-targets", help="Save bounded public Greenhouse/Lever test routes only."); validation_discovery.add_argument("--portal", choices=("GREENHOUSE", "LEVER"), type=str.upper); validation_discovery.add_argument("--limit", type=int, default=20); validation_discovery.add_argument("--max-employers", type=int, default=3); validation_discovery.add_argument("--scan-limit", type=int, default=25); enrich=sub.add_parser("enrich"); enrich.add_argument("--tracker-id", type=int, required=True)
    args=parser.parse_args(); resolver=ApplicationRouteResolver()
    if args.command == "audit":
        with ApplicationHistoryService() as history: _audit(history.list_records())
    elif args.command == "backfill":
        cache=CacheService(); jobs=cache.load_jobs(); changed=0
        for job in jobs:
            route=resolver.resolve({"job_url": job.source_listing_url or job.job_url, "application_url": job.application_url})
            changed += _apply_route(job, route)
        cache.save_jobs(jobs); print(f"Offline cache route backfill complete. Enriched: {changed}; inspected: {len(jobs)}.")
    elif args.command == "cache-list":
        for job in CacheService().load_jobs():
            route=resolver.resolve({"job_url": job.source_listing_url or job.job_url, "application_url": job.application_url})
            direct=bool(route.application_url and route.route_confidence in {"HIGH","MEDIUM"})
            print(f"{job.company} | {job.job_title} | {getattr(job,'career_track','UNKNOWN')} | {getattr(job,'work_arrangement','UNKNOWN')} | {route.application_url or '-'} | {route.portal} | {route.route_confidence} | {'BROWSER_READY' if direct else 'ROUTE_REVIEW'}")
    elif args.command == "discovery-list":
        records=DiscoveryRouteSnapshotService().load()
        for record in records:
            if args.portal and record.get("application_portal", "").lower() != args.portal.lower(): continue
            if args.company and args.company.lower() not in record.get("company", "").lower(): continue
            if args.browser_ready and not record.get("browser_ready"): continue
            print(f"{record['company']} | {record['job_title']} | {record['market']} | {record['work_arrangement']} | {record['career_track']} | {record['application_portal']} | {record['route_confidence']} | {'YES' if record['browser_ready'] else 'NO'} | {record['application_url']}")
    elif args.command == "discover-validation-targets":
        # Isolated public-catalogue sampling: no career relevance/remote filters,
        # CacheService, ApplicationHistoryService, or tracker is constructed here.
        from app.services.target_employer_discovery import TargetEmployerDiscovery
        jobs=TargetEmployerDiscovery().discover_validation_targets(args.limit, portal=args.portal, max_employers=args.max_employers, scan_limit=args.scan_limit)
        records=DiscoveryRouteSnapshotService().save_validation_routes(jobs)
        print(f"Validation-only routes saved: {len(jobs)} (snapshot total: {len(records)}).")
    elif args.command == "validation-targets":
        selector=ValidationTargetSelector(); filters={"portal":args.portal,"company":args.company,"browser_ready":args.browser_ready}
        records=selector.probe(args.limit, refresh=args.refresh, **filters) if args.probe else selector.ranked(**filters)
        if args.best: records=records[:1]
        print("VALIDATION TARGET SELECTION")
        print("Rank | Company | Role | Portal | Confidence | Browser Ready | Probe State | Fields | URL")
        for index, record in enumerate(records, 1): print(f"{index} | {record['company']} | {record['job_title']} | {record['application_portal']} | {record['route_confidence']} | {'YES' if record['browser_ready'] else 'NO'} | {record['probe_state']} | {record['fields_detected']} | {record['application_url']}")
    else:
        with ApplicationHistoryService() as history:
            record=history.get_record_by_id(args.tracker_id)
            if not record: parser.error("Tracker ID not found.")
            route=resolver.resolve(record)
            if route.resolution_status == "RESOLVED":
                history.update_record(record["job_fingerprint"], application_url=route.application_url, source_listing_url=route.source_listing_url, application_url_type=route.application_url_type, application_url_source=route.application_url_source, application_portal=route.portal, application_route_confidence=route.route_confidence, application_route_resolved_at=route.resolved_at, application_route_status=route.resolution_status)
            for key,value in route.to_dict().items(): print(f"{key.replace('_',' ').title()}: {value}")
            print("Persistence: updated" if route.resolution_status == "RESOLVED" else "Persistence: no new offline route evidence")

if __name__ == "__main__": main()
