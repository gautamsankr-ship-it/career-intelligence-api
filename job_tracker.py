"""Small local CLI for the authoritative SQLite application history."""

from __future__ import annotations

import argparse
from datetime import date, datetime

from app.services.application_history_service import ApplicationHistoryService
from app.services.cache_service import CacheService
from app.services.job_discovery_service import JobDiscoveryService
from app.services.remote_work_eligibility import RemoteWorkEligibilityClassifier


def _value(value) -> str:
    return "-" if value in (None, "") else str(value)


TERMINAL_APPLICATION_STATUSES = {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "FAILED"}


def _status(record) -> str:
    return record.get("application_status") or record.get("status") or ""


def _url(record) -> str:
    return record.get("application_url") or record.get("job_url") or ""


def _posted_sort_value(record) -> str:
    return record.get("posted_date") or ""


def _is_stale(record) -> bool:
    value = (record.get("posted_date") or "").strip()
    try:
        return (date.today() - datetime.fromisoformat(value.replace("Z", "+00:00")).date()).days > 7
    except ValueError:
        return False


def is_ready_record(record) -> bool:
    return (
        record.get("decision") == "AUTO_APPLY"
        and record.get("remote_eligibility") == "ELIGIBLE"
        and record.get("application_method") in {"EMAIL", "WEB"}
        and _status(record) not in TERMINAL_APPLICATION_STATUSES
    )


def queue_sections(records):
    """Return daily-action groups, deliberately excluding completed/blocked work."""
    ready, eligibility_review, career_review = [], [], []
    for record in records:
        if _status(record) in TERMINAL_APPLICATION_STATUSES:
            continue
        if is_ready_record(record):
            ready.append(record)
        elif record.get("decision") == "AUTO_APPLY" and record.get("remote_eligibility") == "MANUAL_REVIEW":
            eligibility_review.append(record)
        elif record.get("decision") == "REVIEW" and _status(record) == "REVIEW":
            career_review.append(record)
    key = lambda record: (-(record.get("career_score") or 0), _posted_sort_value(record))
    ready.sort(key=lambda record: (0 if record.get("application_method") == "WEB" else 1, *key(record)))
    eligibility_review.sort(key=key)
    career_review.sort(key=key)
    return {
        "READY TO APPLY": ready,
        "REMOTE ELIGIBILITY REVIEW": eligibility_review,
        "MANUAL CAREER REVIEW": career_review,
    }


def format_queue(records) -> str:
    lines = ["DAILY APPLICATION QUEUE"]
    for heading, section in queue_sections(records).items():
        lines.extend(["", heading, "-" * 60])
        if not section:
            lines.append("None.")
            continue
        for record in section:
            stale = " | STALE / VERIFY STILL OPEN" if _is_stale(record) else ""
            lines.extend([
                f"ID: {record['id']} | {record.get('company') or '-'} | {record.get('job_title') or '-'}{stale}",
                f"Market: {_value(record.get('market'))} | Source: {_value(record.get('source'))}",
                f"Career Track: {_value(record.get('career_track'))}",
                f"Career Score: {_value(record.get('career_score'))} | ATS Score: {_value(record.get('ats_score'))}",
                f"Remote Eligibility: {_value(record.get('remote_eligibility'))}",
                f"Reason: {_value(record.get('remote_eligibility_reason'))}" if record.get("remote_eligibility") == "MANUAL_REVIEW" else f"Route: {_value(record.get('application_method'))} | Status: {_status(record)}",
                f"URL: {_value(_url(record))}",
            ])
            if heading == "READY TO APPLY" and record.get("application_method") == "WEB":
                lines.append(f"Next: Apply manually, then run: python job_tracker.py applied {record['id']}")
            elif heading == "READY TO APPLY" and record.get("application_method") == "EMAIL":
                lines.append("Next: Review and manually send the Gmail draft, then mark it APPLIED.")
            elif heading == "MANUAL CAREER REVIEW":
                lines.append(f"Next: Review manually; optionally run: python job_tracker.py review {record['id']} proceed")
            lines.append("")
    return "\n".join(lines)


def _on_today(value) -> bool:
    if not value:
        return False
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date() == date.today()
    except ValueError:
        return False


def today_text(records) -> str:
    screened = [record for record in records if _on_today(record.get("screened_at") or record.get("processed_at"))]
    current = lambda status: sum(_status(record) == status for record in screened)
    sections = queue_sections(records)
    return "\n".join([
        "TODAY'S JOB SEARCH",
        f"Discovered/Screened today: {len(screened)}",
        f"Skipped today: {current('SKIPPED')}", f"Career review today: {current('REVIEW')}",
        f"Career AUTO_APPLY today: {sum(record.get('decision') == 'AUTO_APPLY' for record in screened)}",
        f"Ready for web application: {sum(record.get('application_method') == 'WEB' for record in sections['READY TO APPLY'])}",
        f"Remote eligibility review: {len(sections['REMOTE ELIGIBILITY REVIEW'])}",
        f"Career review outstanding: {len(sections['MANUAL CAREER REVIEW'])}",
        f"Applied today: {sum(_on_today(record.get('applied_at')) for record in records)}",
        f"Interviews updated today: {sum(_status(record) == 'INTERVIEW' and _on_today(record.get('processed_at')) for record in records)}",
        f"Offers today: {sum(_status(record) == 'OFFER' and _on_today(record.get('processed_at')) for record in records)}",
        f"Rejected today: {sum(_status(record) == 'REJECTED' and _on_today(record.get('processed_at')) for record in records)}",
        f"Outstanding application queue: {sum(len(section) for section in sections.values())}",
    ])


def pipeline_text(records) -> str:
    screened = sum(record.get("career_score") is not None for record in records)
    auto = sum(record.get("decision") == "AUTO_APPLY" for record in records)
    ready = sum(is_ready_record(record) for record in records)
    applied = sum(bool(record.get("applied_at")) or _status(record) in {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"} for record in records)
    interview = sum(bool(record.get("interview_date")) or _status(record) in {"INTERVIEW", "OFFER"} for record in records)
    return "\n".join([
        "CAREER APPLICATION PIPELINE",
        f"Screened: {screened}", "        ↓", f"Career AUTO_APPLY: {auto}", "        ↓",
        f"Remote Eligible: {sum(record.get('remote_eligibility') == 'ELIGIBLE' for record in records)}", "        ↓",
        f"Ready / Manual Web: {ready}", "        ↓", f"Applied (historical): {applied}", "        ↓",
        f"Interview (historical): {interview}", "        ↓", f"Offer: {sum(_status(record) == 'OFFER' for record in records)}",
        f"Career Review: {sum(_status(record) == 'REVIEW' for record in records)}",
        f"Remote Eligibility Review: {sum(record.get('remote_eligibility') == 'MANUAL_REVIEW' for record in records)}",
        f"Rejected: {sum(_status(record) == 'REJECTED' for record in records)} | Withdrawn: {sum(_status(record) == 'WITHDRAWN' for record in records)}",
    ])


def format_arrangement_review(jobs) -> str:
    lines = ["WORK-ARRANGEMENT REVIEW"]
    if not jobs:
        return "\n".join([*lines, "No fresh, relevant UNKNOWN-arrangement vacancies are available for review."])
    for job in jobs:
        metadata = job.metadata or {}
        lines.extend([
            f"\n{job.company or '-'} | {job.job_title or '-'}",
            f"Source: {job.source or '-'} | Market: {job.market or '-'}",
            f"Arrangement: {job.work_arrangement or 'UNKNOWN'}",
            f"Evidence: {metadata.get('work_arrangement_evidence') or 'no reliable workplace evidence'}",
            f"URL: {job.application_url or job.job_url or '-'}",
        ])
    return "\n".join(lines)


def format_records(records) -> str:
    if not records:
        return "No tracked vacancies found."
    lines = ["ID | Company | Job Title | Source | Market | Track | Career | ATS | Decision | Work | Eligibility | Status | Method | Applied | URL"]
    for record in records:
        url = record.get("application_url") or record.get("job_url") or ""
        lines.append(" | ".join([
            _value(record.get("id")), _value(record.get("company")), _value(record.get("job_title")),
            _value(record.get("source")), _value(record.get("market")), _value(record.get("career_track")), _value(record.get("career_score")), _value(record.get("ats_score")),
            _value(record.get("decision")),
            _value(record.get("work_arrangement")), _value(record.get("remote_eligibility")), _value(record.get("application_status") or record.get("status")),
            _value(record.get("application_method")), _value(record.get("applied_at")), _value(url),
        ]))
    return "\n".join(lines)


def summary_text(records) -> str:
    total = len(records)
    current = lambda status: sum((r.get("application_status") or r.get("status")) == status for r in records)
    auto = sum(r.get("decision") == "AUTO_APPLY" for r in records)
    ready = sum(
        r.get("decision") == "AUTO_APPLY"
        and r.get("remote_eligibility") == "ELIGIBLE"
        and r.get("application_method") in {"EMAIL", "WEB"}
        and (r.get("application_status") or r.get("status")) not in {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "FAILED"}
        for r in records
    )
    historically_ready = sum(
        r.get("decision") == "AUTO_APPLY"
        and r.get("remote_eligibility") == "ELIGIBLE"
        and r.get("application_method") in {"EMAIL", "WEB"}
        for r in records
    )
    applied = sum(bool(r.get("applied_at")) or (r.get("application_status") or r.get("status")) in {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"} for r in records)
    interview = sum(bool(r.get("interview_date")) or (r.get("application_status") or r.get("status")) in {"INTERVIEW", "OFFER"} for r in records)
    offer = current("OFFER")
    rate = lambda value, denominator: "N/A" if not denominator else f"{value / denominator:.0%}"
    return "\n".join([
        "JOB APPLICATION FUNNEL",
        f"Total tracked: {total}",
        f"Screened: {sum(r.get('career_score') is not None for r in records)}",
        f"Skipped: {current('SKIPPED')}", f"Career review: {current('REVIEW')}",
        f"Career AUTO_APPLY candidates: {auto}", f"Remote-work eligible: {sum(r.get('remote_eligibility') == 'ELIGIBLE' for r in records)}",
        f"Application-ready: {ready}", f"Manual web required: {current('MANUAL_WEB_REQUIRED')}",
        f"Gmail drafts: {current('DRAFTED')}", f"Applied (historical): {applied}",
        f"Interview (historical): {interview}", f"Offer: {offer}",
        f"Rejected: {current('REJECTED')}", f"Withdrawn: {current('WITHDRAWN')}", f"Failed: {current('FAILED')}",
        f"Application rate: {rate(applied, historically_ready)}", f"Interview rate: {rate(interview, applied)}",
        f"Offer rate: {rate(offer, applied)}",
        f"Remote-work ineligible: {sum(r.get('remote_eligibility') == 'INELIGIBLE' for r in records)}",
        f"Remote eligibility manual review: {sum(r.get('remote_eligibility') == 'MANUAL_REVIEW' for r in records)}",
    ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Track applications in the existing SQLite history.")
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list")
    list_parser.add_argument("--status")
    list_parser.add_argument("--eligibility")
    sub.add_parser("summary")
    sub.add_parser("ready")
    sub.add_parser("queue")
    sub.add_parser("today")
    sub.add_parser("pipeline")
    sub.add_parser("arrangement-review")
    sub.add_parser("backfill-eligibility")
    eligibility = sub.add_parser("eligibility")
    eligibility.add_argument("id", type=int)
    eligibility.add_argument("decision", choices=("eligible", "ineligible", "review"))
    eligibility.add_argument("--note", required=True)
    review = sub.add_parser("review")
    review.add_argument("id", type=int)
    review.add_argument("action", choices=("proceed", "skip"))
    review.add_argument("--note")
    for name in ("applied", "offer", "rejected", "withdrawn"):
        command = sub.add_parser(name)
        command.add_argument("id", type=int)
    interview = sub.add_parser("interview")
    interview.add_argument("id", type=int)
    interview.add_argument("--stage")
    interview.add_argument("--date")
    interview.add_argument("--notes")
    note = sub.add_parser("note")
    note.add_argument("id", type=int)
    note.add_argument("--notes", required=True)
    note.add_argument("--follow-up")
    args = parser.parse_args(argv)

    with ApplicationHistoryService() as history:
        if args.command == "list":
            records = history.list_records(args.status)
            if args.eligibility:
                records = [record for record in records if record.get("remote_eligibility") == args.eligibility]
            print(format_records(records))
        elif args.command == "summary":
            print(summary_text(history.list_records()))
        elif args.command == "ready":
            print(format_records(history.list_ready_records()))
        elif args.command == "queue":
            print(format_queue(history.list_records()))
        elif args.command == "today":
            print(today_text(history.list_records()))
        elif args.command == "pipeline":
            print(pipeline_text(history.list_records()))
        elif args.command == "arrangement-review":
            print(format_arrangement_review(CacheService().load_arrangement_review_jobs()))
        elif args.command == "backfill-eligibility":
            outcome = history.backfill_remote_eligibility(RemoteWorkEligibilityClassifier())
            print("REMOTE ELIGIBILITY BACKFILL")
            print(f"Classified: {outcome['classified']}")
            print(f"Already classified: {outcome['already_classified']}")
            print(f"Unclassified / insufficient evidence: {outcome['insufficient_evidence']}")
        elif args.command == "eligibility":
            mapping = {"eligible": "ELIGIBLE", "ineligible": "INELIGIBLE", "review": "MANUAL_REVIEW"}
            try:
                history.set_manual_eligibility(args.id, mapping[args.decision], args.note)
            except ValueError as exc:
                parser.error(str(exc))
            print(f"ID {args.id} remote eligibility marked {mapping[args.decision]} (MANUAL).")
        elif args.command == "review":
            try:
                history.set_manual_review_action(args.id, args.action, args.note)
            except ValueError as exc:
                parser.error(str(exc))
            print(f"ID {args.id} CareerDecision REVIEW action recorded: {args.action.upper()}.")
        elif args.command == "note":
            record = history.get_record_by_id(args.id)
            if not record:
                parser.error(f"No tracked vacancy found with ID {args.id}.")
            history.update_lifecycle(args.id, record["application_status"] or record["status"], notes=args.notes, follow_up_date=args.follow_up)
            print(f"Updated notes for ID {args.id}.")
        else:
            fields = {}
            if args.command == "interview":
                fields = {"interview_stage": args.stage, "interview_date": args.date, "notes": args.notes}
            try:
                history.update_lifecycle(args.id, args.command.upper(), **{k: v for k, v in fields.items() if v is not None})
            except ValueError as exc:
                parser.error(str(exc))
            print(f"ID {args.id} marked {args.command.upper()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
