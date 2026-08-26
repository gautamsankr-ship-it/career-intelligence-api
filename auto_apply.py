"""Run the safe Gmail draft-only application workflow against cached jobs."""

from __future__ import annotations

import argparse

from app.services.auto_application_orchestrator import (
    AutoApplicationOrchestrator,
    format_preview_results,
    format_run_results,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create safe Gmail drafts for eligible cached vacancies.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most this many cached jobs.")
    parser.add_argument("--preview", action="store_true", help="Evaluate and display actions without changing history, files, or Gmail.")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    orchestrator = AutoApplicationOrchestrator()
    try:
        summary = orchestrator.preview(limit=args.limit) if args.preview else orchestrator.run(limit=args.limit)
    finally:
        orchestrator.history.close()

    if args.preview:
        print(f"\n{format_preview_results(summary)}")
        print("\nPREVIEW SUMMARY")
        print(f"Cached jobs available: {summary.cached_jobs_available}")
        print(f"Jobs scanned: {summary.jobs_scanned}")
        print(f"Duplicates skipped: {summary.duplicates_skipped}")
        print(f"New jobs evaluated: {summary.new_jobs_evaluated}")
        print(f"Evaluation snapshots saved: {summary.snapshots_saved}")
        print("No vacancy records, documents, Gmail drafts, or lifecycle changes were made.")
        for failure in summary.failures:
            print(f"  FAILED: {failure}")
        return

    print(f"\n{format_run_results(summary)}")
    print("\nAUTO-APPLICATION SUMMARY")
    print(f"Cached jobs available: {summary.cached_jobs_available}")
    print(f"Jobs scanned: {summary.jobs_scanned}")
    print(f"Duplicates skipped: {summary.duplicates_skipped}")
    print(f"New jobs evaluated: {summary.new_jobs_evaluated}")
    print(f"Preview snapshots reused: {summary.preview_snapshots_reused}")
    print(f"SKIP (<70): {summary.skipped}")
    print(f"REVIEW (70–77): {summary.review}")
    print(f"AUTO_APPLY eligible: {summary.auto_apply_eligible}")
    print(f"Remote eligible: {summary.remote_eligible}")
    print(f"Remote ineligible: {summary.remote_ineligible}")
    print(f"Remote eligibility review: {summary.remote_eligibility_review}")
    print(f"Gmail drafts created: {summary.gmail_drafts_created}")
    print(f"Manual web required: {summary.manual_web_required}")
    print(f"Failed: {summary.failed}")
    for failure in summary.failures:
        print(f"  FAILED: {failure}")


if __name__ == "__main__":
    main()
