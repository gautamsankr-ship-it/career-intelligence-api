"""CLI diagnostics and controlled maintenance for the Application Answer Vault."""
from __future__ import annotations

import argparse
import re
from uuid import uuid4

from app.models.application_answer import ApplicationAnswer
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_answer_vault import ApplicationAnswerVault


def main() -> None:
    parser = argparse.ArgumentParser(description="Application Answer Vault")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("summary")
    sub.add_parser("list")
    show = sub.add_parser("show"); show.add_argument("concept")
    resolve = sub.add_parser("resolve"); resolve.add_argument("question"); resolve.add_argument("--market"); resolve.add_argument("--application-date"); resolve.add_argument("--max-length", type=int)
    approve = sub.add_parser("approve"); approve.add_argument("concept"); approve.add_argument("--reason", default="User approved")
    learn = sub.add_parser("learn"); learn.add_argument("concept"); learn.add_argument("value"); learn.add_argument("--type", default="TEXT"); learn.add_argument("--reason", default="Manual answer captured")
    args = parser.parse_args(); vault = ApplicationAnswerVault()
    if args.command == "summary":
        answers, rules = vault.answers, vault.rules
        print("APPLICATION ANSWER VAULT")
        print(f"Approved facts: {sum(x.status == 'APPROVED' for x in answers)}")
        print(f"Approved rules: {sum(x.status == 'APPROVED' for x in rules)}")
        for policy in ("AUTO_FILL", "AUTO_FILL_WITH_RULES", "MANUAL_REVIEW"):
            print(f"{policy}: {sum(x.automation_policy == policy for x in answers) + sum(x.automation_policy == policy for x in rules)}")
    elif args.command == "list":
        for x in vault.answers:
            display = "<not set>" if x.value is None else "<stored>" if x.sensitivity in {"LEGAL", "SENSITIVE"} else str(x.value)
            print(f"{x.concept:32} {x.automation_policy:22} {x.status:9} {x.confidence:6} {x.answer_source:18} {display}")
        for x in vault.rules: print(f"{x.concept:32} {x.automation_policy:22} {x.status:9} {x.confidence:6} {x.answer_source:18} rule={x.rule_id}")
    elif args.command == "show":
        answer = vault.get_answer(args.concept)
        rules = [x for x in vault.rules if x.concept == args.concept]
        if answer: print(answer)
        for rule in rules: print(rule)
        if not answer and not rules: raise SystemExit(f"Unknown concept: {args.concept}")
    elif args.command == "resolve":
        decision = ApplicationAnswerEngine(vault).resolve(args.question, market=args.market, application_date=args.application_date)
        ApplicationAnswerEngine.fit_character_limit(decision, args.max_length)
        for key, value in decision.__dict__.items(): print(f"{key.replace('_', ' ').title()}: {value}")
    elif args.command == "approve":
        if not vault.approve(args.concept, args.reason): raise SystemExit(f"Unknown concept: {args.concept}")
        print(f"Approved {args.concept}.")
    else:
        concept = re.sub(r"[^A-Z0-9_]+", "_", args.concept.upper()).strip("_")
        vault.learn_draft(ApplicationAnswer(f"manual_{uuid4().hex[:12]}", concept, args.value, args.type, "AUTO_FILL", "HIGH", "USER_APPROVED_ANSWER"), args.reason)
        print(f"Saved {concept} as DRAFT. Review then approve it explicitly with: python application_answers.py approve {concept}")


if __name__ == "__main__": main()
