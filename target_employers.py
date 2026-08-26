"""Diagnostic CLI for the curated target-employer intelligence registry."""
from __future__ import annotations
import argparse
from app.services.target_employer_registry import TARGET_EMPLOYERS, industry_tag_summary, registry_summary

def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect target-employer discovery intelligence.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("summary"); listing = sub.add_parser("list"); listing.add_argument("--tier", type=int); listing.add_argument("--category"); listing.add_argument("--ats")
    sub.add_parser("ats"); args = parser.parse_args(argv)
    entries = list(TARGET_EMPLOYERS)
    if args.command == "summary":
        categories, tiers, ats = registry_summary(); print("TARGET EMPLOYER INTELLIGENCE"); print(f"Total employers: {len(entries)}")
        for tier in (1,2,3): print(f"Tier {tier}: {tiers[tier]}")
        for category, count in sorted(categories.items()): print(f"{category}: {count}")
        tags = industry_tag_summary()
        print("Finance-technology coverage:"); [print(f"{name}: {count}") for name, count in sorted(tags.items())]
        print("ATS coverage:"); [print(f"{name}: {count}") for name, count in sorted(ats.items())]
        print(f"Discovery supported: {sum(e.discovery_method == 'PUBLIC_STRUCTURED_ENDPOINT' for e in entries)}")
    else:
        if args.command == "list": entries = [e for e in entries if (not args.tier or e.tier == args.tier) and (not args.category or e.category.casefold() == args.category.casefold()) and (not args.ats or e.ats_platform.casefold() == args.ats.casefold())]
        print("Employer | Category | Tier | ATS | Discovery Method | Careers URL")
        for e in entries: print(f"{e.name} | {e.category} | {e.tier} | {e.ats_platform} | {e.discovery_method} | {e.careers_url or '-'}")
    return 0
if __name__ == "__main__": raise SystemExit(main())
