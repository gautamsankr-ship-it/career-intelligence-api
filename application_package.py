"""Preparation-only CLI for persisted application packages."""
from __future__ import annotations

import argparse

from app.services.application_package_orchestrator import ApplicationPackageOrchestrator


def _print(package):
    print("APPLICATION PACKAGE")
    print(f"Tracker ID: {package.tracker_id}\nCompany: {package.company}\nRole: {package.job_title}")
    print(f"\nApplication Route:\n{package.application_portal}\n{package.route_confidence} confidence\n{package.portal_capability}")
    print(f"\nResume: {package.resume_status}\nCover Letter: {package.cover_letter_status}\nAnswer Vault: {package.answer_vault_status}")
    print(f"\nReadiness:\n{package.readiness}")
    if package.blocking_reasons: print("Blocking reasons: " + ", ".join(package.blocking_reasons))


def main():
    parser = argparse.ArgumentParser(description="Prepare non-submitting application packages.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "show"):
        command = sub.add_parser(name); command.add_argument("--tracker-id", required=True, type=int)
    ready = sub.add_parser("ready"); ready.add_argument("--limit", type=int, default=20)
    bulk = sub.add_parser("prepare-ready"); bulk.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(); service = ApplicationPackageOrchestrator()
    try:
        if args.command == "prepare": _print(service.prepare(args.tracker_id))
        elif args.command == "show":
            package = service.show(args.tracker_id)
            if not package: parser.error("No package exists for this tracker ID. Run prepare first.")
            _print(package)
        elif args.command == "ready":
            for package in service.ready()[:args.limit]: print(f"{package.tracker_id} | {package.company} | {package.job_title} | {package.readiness} | {package.application_url}")
        else:
            for package in service.prepare_ready(args.limit): _print(package)
    finally:
        service.history.close()


if __name__ == "__main__": main()
