"""Inspect the professional/specialist source registry without network access."""
from __future__ import annotations
import argparse
from app.services.professional_source_registry import PROFESSIONAL_JOB_SOURCES, source_summary

def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect professional and specialist finance job sources.")
    parser.add_argument("command", choices=("summary", "list"))
    args = parser.parse_args(argv)
    if args.command == "summary":
        counts = source_summary(); print("PROFESSIONAL / SPECIALIST JOB SOURCES"); print(f"Total evaluated: {len(PROFESSIONAL_JOB_SOURCES)}")
        for status in ("SUPPORTED", "DIAGNOSTIC_ONLY", "UNSUPPORTED"): print(f"{status.replace('_', ' ').title()}: {counts[status]}")
        return 0
    print("Source | Category | Priority | Method | Status | URL")
    for source in PROFESSIONAL_JOB_SOURCES: print(f"{source.name} | {source.category} | {source.priority} | {source.discovery_method} | {source.status} | {source.base_url}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
