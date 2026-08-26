"""Read-only browser application preview CLI. No submit mode exists."""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_history_service import ApplicationHistoryService
from app.services.application_preparation_engine import ApplicationPreparationEngine
from app.services.application_live_validation import LiveValidationService

def _print(plan, verbose=False):
    summary=plan.summary(); print("APPLICATION PREVIEW"); print(f"Tracker ID: {plan.tracker_id or '-'}\nCompany: {plan.company or '-'}\nRole: {plan.role or '-'}\nMarket: {plan.market or '-'}\nPortal: {plan.portal}\nApplication URL: {plan.url}")
    print(f"Fields detected: {summary['fields_detected']} | Auto-fillable: {summary['auto_fillable_fields']} | Manual review: {summary['manual_review_fields']} | Optional skipped: {summary['optional_skipped_fields']}")
    print(f"Authentication: {plan.authentication} | MFA: {plan.mfa} | CAPTCHA: {plan.captcha} | Final submit control: {'DETECTED' if plan.final_submit_detected else 'NO'}")
    print(f"Automation coverage: {summary['automation_coverage_percentage']}%\nStatus: {plan.readiness}\nSAFETY: Application submitted: NO | Tracker marked applied: NO | Gmail sent: NO")
    if verbose:
        for index, field in enumerate(plan.fields, 1): print(f"{index:02} | {field.label} | {field.field_type} | {field.action} | {field.concept} | {field.confidence} | {field.reason}")

def main():
    parser=argparse.ArgumentParser(description="Read-only application-form preview")
    sub=parser.add_subparsers(dest="command", required=True)
    preview=sub.add_parser("preview"); preview.add_argument("--tracker-id", type=int, required=True); preview.add_argument("--headed", action="store_true"); preview.add_argument("--application-date"); preview.add_argument("--verbose", action="store_true")
    fill=sub.add_parser("fill-preview"); fill.add_argument("--tracker-id", type=int, required=True); fill.add_argument("--headed", action="store_true"); fill.add_argument("--application-date"); fill.add_argument("--pause-for-review", type=int, default=0); fill.add_argument("--verbose", action="store_true")
    route=sub.add_parser("resolve-route"); route.add_argument("--tracker-id", type=int, required=True)
    fixture=sub.add_parser("preview-fixture"); fixture.add_argument("path"); fixture.add_argument("--market", default="united_kingdom"); fixture.add_argument("--application-date"); fixture.add_argument("--verbose", action="store_true")
    url=sub.add_parser("preview-url"); url.add_argument("url"); url.add_argument("--market", default=""); url.add_argument("--headed", action="store_true"); url.add_argument("--application-date"); url.add_argument("--verbose", action="store_true")
    fill_url=sub.add_parser("fill-preview-url"); fill_url.add_argument("url"); fill_url.add_argument("--market", default=""); fill_url.add_argument("--headed", action="store_true"); fill_url.add_argument("--application-date"); fill_url.add_argument("--pause-for-review", type=int, default=0); fill_url.add_argument("--verbose", action="store_true")
    prepare=sub.add_parser("prepare"); prepare.add_argument("--tracker-id",type=int,required=True); prepare.add_argument("--headed",action="store_true"); prepare.add_argument("--application-date"); prepare.add_argument("--max-pages",type=int,default=5); prepare.add_argument("--pause-for-review",type=int,default=0)
    resume=sub.add_parser("resume"); resume.add_argument("--session-id",required=True); resume.add_argument("--headed",action="store_true")
    exceptions=sub.add_parser("exceptions"); exceptions.add_argument("--tracker-id",type=int); exceptions.add_argument("--session-id")
    session_cmd=sub.add_parser("session"); session_cmd.add_argument("--session-id",required=True)
    sub.add_parser("sessions")
    validation=sub.add_parser("validate-live-url", help="Isolated public Greenhouse/Lever validation; never submits.")
    validation.add_argument("url"); validation.add_argument("--market", default="united_kingdom"); validation.add_argument("--headed", action="store_true")
    validation.add_argument("--headless", dest="headed", action="store_false", help="Explicitly opt into headless live validation."); validation.set_defaults(headed=True)
    validation.add_argument("--inspect-only", action="store_true"); validation.add_argument("--fill", action="store_true")
    validation.add_argument("--allow-safe-navigation", action="store_true"); validation.add_argument("--use-real-profile", action="store_true")
    validation.add_argument("--test-resume"); validation.add_argument("--test-cover-letter"); validation.add_argument("--application-date")
    validation.add_argument("--max-pages", type=int, default=5); validation.add_argument("--pause-for-review", type=int, default=0); validation.add_argument("--verbose", action="store_true")
    validation_session=sub.add_parser("validation-session"); validation_session.add_argument("--session-id", required=True)
    args=parser.parse_args(); service=ApplicationBrowserService()
    engine=ApplicationPreparationEngine(service)
    if args.command == "validate-live-url":
        if args.inspect_only and args.fill: parser.error("--inspect-only and --fill cannot be combined.")
        print("LIVE ATS VALIDATION MODE — NOT A REAL APPLICATION\nAPPLICATION SUBMISSION IS DISABLED")
        validation_service=LiveValidationService(service)
        session=asyncio.run(validation_service.validate_url(args.url, args.market, headed=args.headed, use_real_profile=args.use_real_profile,
            fill=args.fill and not args.inspect_only, allow_safe_navigation=args.allow_safe_navigation, test_resume=args.test_resume,
            test_cover_letter=args.test_cover_letter, application_date=args.application_date, max_pages=args.max_pages, pause_seconds=args.pause_for_review))
        print(f"Session: {session['session_id']}\nPortal: {session['portal']}\nPortal Confidence: {session.get('portal_evidence',{}).get('confidence','LOW')}\nWrapper detected: {'YES' if session.get('wrapper_detected') else 'NO'}\nApplication surface: {session.get('application_surface','NOT_FOUND')}\nState: {session['state']}\nPages processed: {session['pages_processed']}\nFields detected: {session['fields_detected']}\nFields filled: {session['fields_filled']}\nDocuments uploaded: {session['documents_uploaded']}\nExceptions: {len(session['exceptions'])}\nApplication submitted: NO\nTracker updated: NO\nGmail sent: NO")
        if args.verbose: print(f"Initial URL: {session['source_url']}\nFinal URL: {session['final_url']}\nPortal evidence: {session.get('portal_evidence',{})}")
        return
    if args.command == "validation-session":
        session=LiveValidationService(service).load(args.session_id)
        print(f"LIVE VALIDATION SESSION\nSession: {session['session_id']}\nPortal: {session['portal']}\nURL: {session['final_url']}\nState: {session['state']}\nPages: {session['pages_processed']}\nFields filled: {session['fields_filled']}\nExceptions: {len(session['exceptions'])}\nSubmission: NO")
        return
    if args.command in {"prepare","preview", "fill-preview", "resolve-route"}:
        with ApplicationHistoryService() as history:
            record=history.get_record_by_id(args.tracker_id)
        if not record: parser.error("Tracker ID not found.")
        destination=record.get("application_url") or record.get("job_url")
        if not destination: parser.error("Tracked vacancy has no application URL.")
        if args.command == "resolve-route":
            result=service.resolve_route_url(destination, record)
            for key, value in result.to_dict().items(): print(f"{key.replace('_', ' ').title()}: {value}")
            return
        if args.command == "prepare":
            # Live preparation deliberately remains browser-safe: direct route
            # inspection/fill-preview has no final-submit operation. Full page
            # continuation is exercised with deterministic fixtures until a
            # supported portal adapter validates its structure.
            plan=service.fill_preview_url(destination, record, args.tracker_id, args.headed, args.application_date, args.pause_for_review)
            prep=engine.create_session(record,args.tracker_id,args.application_date or "")
            prep.portal=plan.portal; prep.current_url=plan.url; prep.fields_detected=plan.summary()["fields_detected"]; prep.fields_filled=plan.fields_filled; prep.documents_uploaded=plan.documents_uploaded
            prep.state="PORTAL_LIMITED" if plan.portal not in {"GREENHOUSE","LEVER","GENERIC"} else plan.readiness; engine._stop(prep,prep.state,"LIVE_MULTI_PAGE_CONTINUATION_REQUIRES_VALIDATED_ADAPTER")
            print(f"Preparation session: {prep.session_id}\nState: {prep.state}\nApplication submitted: NO\nTracker marked applied: NO"); return
        plan=(service.fill_preview_url(destination, record, args.tracker_id, args.headed, args.application_date, args.pause_for_review) if args.command == "fill-preview" else service.preview_url(destination, record, args.tracker_id, args.headed, args.application_date))
    elif args.command == "resume":
        prep=engine.load(args.session_id); print(f"Session: {prep.session_id}\nState: {prep.state}\nResume safety: reopen and re-inspect required; no stored browser secrets."); return
    elif args.command == "exceptions":
        sessions=[engine.load(args.session_id)] if args.session_id else [s for s in engine.sessions() if args.tracker_id is None or s.tracker_id==args.tracker_id]
        for prep in sessions:
            for exc in prep.exceptions: print(f"{prep.session_id} | page {exc.page_number} | {exc.field_label} | {exc.exception_type} | {'REQUIRED' if exc.required else 'OPTIONAL'} | {exc.reason}")
        return
    elif args.command == "session":
        prep=engine.load(args.session_id); print(f"APPLICATION PREPARATION SESSION\nSession: {prep.session_id}\nTracker: {prep.tracker_id}\nPortal: {prep.portal}\nState: {prep.state}\nPages: {prep.pages_processed}\nFields filled: {prep.fields_filled}\nExceptions: {len(prep.exceptions)}\nApplication submitted: NO"); return
    elif args.command == "sessions":
        for prep in engine.sessions(): print(f"{prep.session_id} | tracker={prep.tracker_id or '-'} | {prep.portal} | {prep.state} | exceptions={len(prep.exceptions)}")
        return
    elif args.command == "preview-fixture": plan=service.preview_html(Path(args.path).read_text(encoding="utf-8"), vacancy={"market": args.market}, application_date=args.application_date)
    elif args.command == "fill-preview-url": plan=service.fill_preview_url(args.url, {"market": args.market}, headed=args.headed, application_date=args.application_date, pause_seconds=args.pause_for_review)
    else: plan=service.preview_url(args.url, {"market": args.market}, headed=args.headed, application_date=args.application_date)
    _print(plan, args.verbose)

if __name__ == "__main__": main()
