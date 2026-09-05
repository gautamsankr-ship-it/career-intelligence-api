"""Task 21.35: ONE operational Career Intelligence command.

    python career_intelligence.py run          Full workflow (default).
    python career_intelligence.py gmail        Gmail outcome monitor only.
    python career_intelligence.py dashboard    Launch the CRM dashboard.
    python career_intelligence.py status       Concise CRM/system status.

`run` orchestrates the existing, unmodified production services in order:
discover -> dedupe/hard-eligibility/scoring/A-E priority -> prepare packages
-> sync CRM -> browser-prepare ready applications (pausing at any genuine
CAPTCHA/MFA/unknown-field blocker) -> human-authorized final submit -> Gmail
outcome monitoring -> operational summary. See
`app.services.career_intelligence_runner.CareerIntelligenceRunner` for the
actual orchestration logic and its safety guarantees.
"""
import argparse
import json

DASHBOARD_URL = "http://127.0.0.1:8000"


def _print_summary(summary) -> None:
    print("\n" + "=" * 70)
    print("CAREER INTELLIGENCE -- OPERATIONAL SUMMARY")
    print("=" * 70)
    for label, value in summary.to_dict().items():
        if label == "errors":
            continue
        print(f"{label}: {value}")
    if summary.errors:
        print("\nErrors (isolated -- other opportunities were unaffected):")
        for error in summary.errors:
            print(f"  - {error}")
    print("=" * 70)
    print(f"\nDashboard: {DASHBOARD_URL}")
    print("If it is not already running: python dashboard.py")


def cmd_run(args) -> None:
    from app.services.career_intelligence_runner import CareerIntelligenceRunner

    runner = CareerIntelligenceRunner(headed=args.headed, skip_discovery=args.skip_discovery)
    summary = runner.run()
    _print_summary(summary)


def cmd_gmail(args) -> None:
    from app.services.career_intelligence_runner import CareerIntelligenceRunner

    runner = CareerIntelligenceRunner()
    report = runner.gmail_only()
    print(json.dumps(report, indent=2))


def cmd_dashboard(args) -> None:
    import uvicorn

    uvicorn.run("app.api.dashboard:app", host="127.0.0.1", port=8000)


def cmd_status(args) -> None:
    from app.services.career_intelligence_runner import CareerIntelligenceRunner

    runner = CareerIntelligenceRunner()
    print(json.dumps(runner.status_report(), indent=2, default=str))
    print(f"\nDashboard: {DASHBOARD_URL}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.set_defaults(command="run", headed=False, skip_discovery=False)
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Full operational workflow (default).")
    run_parser.add_argument("--headed", action="store_true", help="Run the browser visibly instead of headless.")
    run_parser.add_argument("--skip-discovery", action="store_true", help="Reuse the existing job cache instead of running refresh_jobs.py.")

    sub.add_parser("gmail", help="Gmail outcome monitor only.")
    sub.add_parser("dashboard", help="Launch the CRM dashboard.")
    sub.add_parser("status", help="Concise CRM/system status.")

    args = parser.parse_args()
    command = args.command or "run"  # bare invocation: subparsers resets dest to None, not the top-level default
    {"run": cmd_run, "gmail": cmd_gmail, "dashboard": cmd_dashboard, "status": cmd_status}[command](args)


if __name__ == "__main__":
    main()
