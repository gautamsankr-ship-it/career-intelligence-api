"""Single-vacancy, no-submit ApplicationPackage browser handoff."""
from __future__ import annotations

import argparse

from app.services.application_execution_orchestrator import ApplicationExecutionOrchestrator


def _print(result):
    print("APPLICATION PREPARATION" if result.mode == "PREPARE" else "APPLICATION AUTOFILL PREVIEW")
    print(f"Tracker ID: {result.tracker_id}\nPortal: {result.portal}\nFields detected: {result.fields_detected}")
    print(f"Fields resolved: {result.fields_resolved}\nFields filled: {result.fields_filled}\nManual review: {result.manual_review_fields}")
    print(f"Resume uploaded: {'YES' if result.resume_uploaded else 'NO'}\nCover letter uploaded: {'YES' if result.cover_letter_uploaded else 'NO'}")
    print(f"Final submit detected: {'YES' if result.final_submit_detected else 'NO'}\nFinal submit clicked: NO\nStatus: {result.status}")
    print("SAFETY: Application submitted: NO | Tracker marked APPLIED: NO | Gmail sent: NO")


def main():
    parser = argparse.ArgumentParser(description="Safe package-to-browser preparation; submission is impossible.")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "preview", "prepare", "progress"):
        item = sub.add_parser(command); item.add_argument("--tracker-id", type=int, required=True); item.add_argument("--headed", action="store_true"); item.add_argument("--application-date")
    resume=sub.add_parser("resume"); resume.add_argument("--execution-id", required=True); resume.add_argument("--headed", action="store_true")
    sub.add_parser("ready")
    args = parser.parse_args(); service = ApplicationExecutionOrchestrator()
    try:
        if args.command == "ready":
            for package in service.ready(): print(f"{package.tracker_id} | {package.company} | {package.job_title} | {package.readiness} | {package.application_url}")
            return
        if args.command == "resume": _print(service.resume(args.execution_id, headed=args.headed)); return
        mode = {"inspect":"INSPECT_ONLY", "preview":"AUTOFILL_PREVIEW", "prepare":"PREPARE", "progress":"PROGRESS"}[args.command]
        _print(service.execute(args.tracker_id, mode, headed=args.headed, application_date=args.application_date))
    finally:
        service.history.close()


if __name__ == "__main__": main()
