"""Task 21.34: Gmail Outcome Monitoring -- manual operational command.

Reads recent Gmail inbox messages READ-ONLY (list/get only -- never sends,
replies, drafts, labels, or deletes anything) and connects any employer/
recruiter response to its matching opportunity in the existing Application
CRM (`OpportunityCRMService`), updating it via immutable CRM events. A
message is only ever recorded against an opportunity when the match evidence
is sufficient; anything ambiguous, unmatched, or unclassifiable is left for
human review rather than guessed.

Usage:
    python gmail_outcome_monitor.py [--lookback-days N] [--max-messages N]

Task 21.35 (the production automation runner) calls
`GmailOutcomeMonitor(...).run()` directly -- this script is the same entry
point, exposed as one simple manual command for now.
"""
import argparse
import json

from app.services.gmail_outcome_monitor_service import GmailOutcomeMonitor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=30, help="How many days of inbox mail to scan (default: 30).")
    parser.add_argument("--max-messages", type=int, default=200, help="Maximum messages to fetch in one run (default: 200).")
    args = parser.parse_args()

    monitor = GmailOutcomeMonitor(lookback_days=args.lookback_days, max_messages=args.max_messages)
    report = monitor.run()

    summary = {key: value for key, value in report.items() if key != "details"}
    print("Gmail Outcome Monitoring -- run summary")
    print(json.dumps(summary, indent=2))
    if report["details"]:
        print("\nPer-message detail:")
        for entry in report["details"]:
            print(f"  {entry}")


if __name__ == "__main__":
    main()
