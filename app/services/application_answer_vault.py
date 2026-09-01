"""Versioned, human-readable storage for approved application answers/rules."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.application_answer import ApplicationAnswer, ApplicationRule


DEFAULT_PATH = Path("app/data/application_answer_vault.json")


def _seed_answers() -> list[ApplicationAnswer]:
    # Only facts explicit in master_candidate_profile.json or frozen Task 13 policy.
    approved = "APPROVED"
    return [
        ApplicationAnswer("email_address", "EMAIL_ADDRESS", "gautamsankr@gmail.com", "EMAIL", "AUTO_FILL", "HIGH", "USER_APPROVED_ANSWER", "Task 20.1 candidate approval", [], "STANDARD", approved),
        ApplicationAnswer("phone_number", "PHONE_NUMBER", "+9779851139824", "PHONE", "AUTO_FILL", "HIGH", "USER_APPROVED_ANSWER", "Task 20.1 candidate approval", [], "STANDARD", approved),
        ApplicationAnswer("first_name", "FIRST_NAME", "Shankar", "TEXT", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:candidate.full_name", [], "STANDARD", approved),
        ApplicationAnswer("last_name", "LAST_NAME", "Gautam", "TEXT", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:candidate.full_name", [], "STANDARD", approved),
        ApplicationAnswer("full_name", "FULL_NAME", "Shankar Gautam", "TEXT", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:candidate.full_name", [], "STANDARD", approved),
        # Task 21.30: the candidate's CURRENT approved location, until
        # explicitly changed by the human. Never inferred from a target
        # market or from planned future relocation/study -- a single,
        # standing, human-approved fact, same as email/phone/name above.
        # Work authorization concepts (WORK_AUTHORIZATION_*/SPONSORSHIP_*)
        # remain entirely separate and are never derived from this.
        ApplicationAnswer("current_location_country", "CURRENT_LOCATION_COUNTRY", "Nepal", "TEXT", "AUTO_FILL", "HIGH", "USER_APPROVED_ANSWER", "Task 21.30 candidate approval: Kathmandu, Nepal", [], "STANDARD", approved),
        ApplicationAnswer("current_city", "CURRENT_CITY", "Kathmandu", "TEXT", "AUTO_FILL", "HIGH", "USER_APPROVED_ANSWER", "Task 21.30 candidate approval: Kathmandu, Nepal", [], "STANDARD", approved),
        ApplicationAnswer("current_location_full", "CURRENT_LOCATION", "Kathmandu, Nepal", "TEXT", "AUTO_FILL", "HIGH", "USER_APPROVED_ANSWER", "Task 21.30 candidate approval: Kathmandu, Nepal", [], "STANDARD", approved),
        ApplicationAnswer("accounting_qualification", "ACCOUNTING_QUALIFICATION", "YES", "BOOLEAN", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:education[Chartered Accountant]", [], "STANDARD", approved),
        # Task 21.31: the candidate is a Chartered Accountant through
        # ICAI/ICAN -- never ACA or ACCA specifically (no separately
        # verified ACA/ACCA membership), but also never "unqualified"
        # merely because a UK vacancy phrases the question in ACA/ACCA
        # terms. Two distinct approved facts, kept apart from the broad
        # ACCOUNTING_QUALIFICATION=YES above: an exact "are you ACA/ACCA"
        # designation question is honestly NO; an "...or equivalent"
        # question recognizes ICAI/ICAN CA as the equivalent qualification
        # and is YES.
        ApplicationAnswer("accounting_qualification_aca_acca", "ACCOUNTING_QUALIFICATION_ACA_ACCA", "NO", "BOOLEAN", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:education[Chartered Accountant (ICAI/ICAN), not ACA/ACCA]", [], "STANDARD", approved),
        ApplicationAnswer("accounting_qualification_or_equivalent", "ACCOUNTING_QUALIFICATION_OR_EQUIVALENT", "YES", "BOOLEAN", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:education[Chartered Accountant (ICAI/ICAN) recognized as the equivalent professional accounting qualification]", [], "STANDARD", approved),
        ApplicationAnswer("professional_membership", "PROFESSIONAL_MEMBERSHIP", "Institute of Chartered Accountants of India", "TEXT", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:professional_memberships", [], "STANDARD", approved),
        ApplicationAnswer("education", "EDUCATION", "Bachelor of Business Studies", "TEXT", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:education", [], "STANDARD", approved),
        ApplicationAnswer("education_status", "EDUCATION_STATUS", "COMPLETED", "TEXT", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:education", [], "STANDARD", approved),
        ApplicationAnswer("future_education", "POSTGRADUATE_STUDY_STATUS", "PLANNED", "TEXT", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:future_education", [], "STANDARD", approved),
        ApplicationAnswer("accounting_experience", "ACCOUNTING_EXPERIENCE", 15, "NUMBER", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:professional_summary", [], "STANDARD", approved),
        ApplicationAnswer("finance_experience", "FINANCE_EXPERIENCE", 15, "NUMBER", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:experience.years", [], "STANDARD", approved),
        ApplicationAnswer("sql_experience", "SQL_EXPERIENCE", "YES", "BOOLEAN", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:software.analytics", [], "STANDARD", approved),
        ApplicationAnswer("python_experience", "PYTHON_EXPERIENCE", "YES", "BOOLEAN", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:software.analytics", [], "STANDARD", approved),
        ApplicationAnswer("erp_experience", "ERP_EXPERIENCE", "YES", "BOOLEAN", "AUTO_FILL", "HIGH", "PROFILE_FACT", "master_candidate_profile.json:software.erp", [], "STANDARD", approved),
        ApplicationAnswer("remote_preference", "REMOTE_WORK_PREFERENCE", "YES", "BOOLEAN", "AUTO_FILL", "HIGH", "APPROVED_RULE", "Task 13 current operating policy", [], "CONTEXTUAL", approved),
        ApplicationAnswer("legal_declaration", "LEGAL_DECLARATION", None, "BOOLEAN", "MANUAL_REVIEW", "LOW", "MANUAL_REQUIRED", "", [], "LEGAL", approved),
        ApplicationAnswer("voluntary_demographic", "VOLUNTARY_DEMOGRAPHIC", None, "TEXT", "MANUAL_REVIEW", "LOW", "MANUAL_REQUIRED", "", [], "VOLUNTARY_DEMOGRAPHIC", approved),
        ApplicationAnswer("criminal_history", "CRIMINAL_HISTORY", None, "BOOLEAN", "MANUAL_REVIEW", "LOW", "MANUAL_REQUIRED", "", [], "LEGAL", approved),
        ApplicationAnswer("security_clearance", "SECURITY_CLEARANCE", None, "BOOLEAN", "MANUAL_REVIEW", "LOW", "MANUAL_REQUIRED", "", [], "LEGAL", approved),
        ApplicationAnswer("conflict_interest", "CONFLICT_OF_INTEREST", None, "BOOLEAN", "MANUAL_REVIEW", "LOW", "MANUAL_REQUIRED", "", [], "LEGAL", approved),
        ApplicationAnswer("notice_period", "NOTICE_PERIOD", "7 calendar days", "TEXT", "AUTO_FILL", "HIGH", "USER_APPROVED_ANSWER", "Task 20.1 candidate approval", [], "CONTEXTUAL", approved),
        ApplicationAnswer("expected_salary", "EXPECTED_SALARY", "Negotiable based on the role scope and total compensation, with a minimum expectation equivalent to USD 30 per hour.", "TEXT", "AUTO_FILL_WITH_RULES", "HIGH", "USER_APPROVED_ANSWER", "Task 20.1 candidate approval", [], "SENSITIVE", approved),
        ApplicationAnswer("sponsorship_uk", "SPONSORSHIP_UK", "YES", "BOOLEAN", "AUTO_FILL_WITH_RULES", "HIGH", "USER_APPROVED_ANSWER", "Task 20.1 candidate approval", ["united_kingdom"], "CONTEXTUAL", approved),
        ApplicationAnswer("sponsorship_us", "SPONSORSHIP_US", "YES", "BOOLEAN", "AUTO_FILL_WITH_RULES", "HIGH", "USER_APPROVED_ANSWER", "Task 20.1 candidate approval", ["united_states"], "CONTEXTUAL", approved),
        ApplicationAnswer("sponsorship_au", "SPONSORSHIP_AUSTRALIA", "YES", "BOOLEAN", "AUTO_FILL_WITH_RULES", "HIGH", "USER_APPROVED_ANSWER", "Task 20.1 candidate approval", ["australia"], "CONTEXTUAL", approved),
    ]


def _seed_rules() -> list[ApplicationRule]:
    return [
        ApplicationRule("work_auth_uk", "WORK_AUTHORIZATION_UK", {"market": "united_kingdom"}, "NO", explanation="Current approved UK work-right status (Task 13)."),
        ApplicationRule("work_auth_us", "WORK_AUTHORIZATION_US", {"market": "united_states"}, "NO", explanation="Current approved US work-right status (Task 13)."),
        ApplicationRule("work_auth_au", "WORK_AUTHORIZATION_AUSTRALIA", {"market": "australia"}, "NO", explanation="Current approved Australian work-right status (Task 13)."),
        ApplicationRule("earliest_start", "EARLIEST_START_DATE", {}, "APPLICATION_DATE_PLUS_7_DAYS", explanation="Candidate-approved availability: seven calendar days after application date."),
        ApplicationRule("relocation_uk", "WILLING_TO_RELOCATE_UK", {"market": "united_kingdom"}, "YES", explanation="Candidate plans to relocate to the United Kingdom next month; relocation has not occurred yet."),
        ApplicationRule("relocation_us", "WILLING_TO_RELOCATE_US", {"market": "united_states"}, "NO", explanation="Current candidate-approved relocation preference."),
        ApplicationRule("relocation_au", "WILLING_TO_RELOCATE_AUSTRALIA", {"market": "australia"}, "NO", explanation="Current candidate-approved relocation preference."),
        ApplicationRule("travel_uk", "WILLING_TO_TRAVEL_UK", {"market": "united_kingdom"}, "YES", explanation="Current candidate-approved travel preference; no percentage is approved."),
        ApplicationRule("travel_us", "WILLING_TO_TRAVEL_US", {"market": "united_states"}, "NO", explanation="Current candidate-approved travel preference; no percentage is approved."),
        ApplicationRule("travel_au", "WILLING_TO_TRAVEL_AUSTRALIA", {"market": "australia"}, "NO", explanation="Current candidate-approved travel preference; no percentage is approved."),
    ]


class ApplicationAnswerVault:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.data = self._load_or_seed()

    def _load_or_seed(self) -> dict[str, Any]:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._apply_task20_1_updates(data)
            return data
        data = {"schema_version": 1, "answers": [x.to_dict() for x in _seed_answers()], "rules": [x.to_dict() for x in _seed_rules()], "audit": []}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return data

    def _apply_task20_1_updates(self, data: dict[str, Any]) -> None:
        """One-way, approved Task 20.1 migration preserving unrelated user edits."""
        answers = {row.get("concept"): row for row in data.setdefault("answers", [])}
        changed = False
        for seeded in _seed_answers():
            if seeded.concept in {"EMAIL_ADDRESS", "PHONE_NUMBER", "CURRENT_LOCATION_COUNTRY", "NOTICE_PERIOD", "EXPECTED_SALARY", "SPONSORSHIP_UK", "SPONSORSHIP_US", "SPONSORSHIP_AUSTRALIA", "FIRST_NAME", "LAST_NAME", "FULL_NAME", "CURRENT_CITY", "CURRENT_LOCATION", "ACCOUNTING_QUALIFICATION_ACA_ACCA", "ACCOUNTING_QUALIFICATION_OR_EQUIVALENT"}:
                old = answers.get(seeded.concept)
                if old != seeded.to_dict():
                    if old: data["answers"].remove(old)
                    data["answers"].append(seeded.to_dict()); changed = True
        rules = {row.get("rule_id"): row for row in data.setdefault("rules", [])}
        for seeded in _seed_rules():
            if seeded.rule_id in {"earliest_start", "relocation_uk", "relocation_us", "relocation_au", "travel_uk", "travel_us", "travel_au"} and seeded.rule_id not in rules:
                data["rules"].append(seeded.to_dict()); changed = True
        if changed:
            data.setdefault("audit", []).append({"kind": "migration", "id": "task20_1", "changed_at": datetime.now(timezone.utc).isoformat(), "reason": "Candidate-approved practical application answers"})
            self.path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    @property
    def answers(self) -> list[ApplicationAnswer]:
        return [ApplicationAnswer.from_dict(item) for item in self.data.get("answers", [])]

    @property
    def rules(self) -> list[ApplicationRule]:
        return [ApplicationRule.from_dict(item) for item in self.data.get("rules", [])]

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")

    def get_answer(self, concept: str) -> ApplicationAnswer | None:
        return next((x for x in self.answers if x.concept == concept), None)

    def add_or_update_answer(self, answer: ApplicationAnswer, reason: str = "") -> None:
        prior = self.get_answer(answer.concept)
        rows = self.data.setdefault("answers", [])
        if prior:
            rows[:] = [row for row in rows if row.get("concept") != answer.concept]
        rows.append(answer.to_dict())
        self.data.setdefault("audit", []).append({"kind": "answer", "id": answer.answer_id, "concept": answer.concept, "old_value": prior.value if prior else None, "new_value": answer.value, "changed_at": datetime.now(timezone.utc).isoformat(), "reason": reason})
        self.save()

    def approve(self, concept: str, reason: str = "User approved") -> bool:
        for row in self.data.get("answers", []):
            if row.get("concept") == concept:
                old = row.get("status")
                row["status"] = "APPROVED"
                self.data.setdefault("audit", []).append({"kind": "approval", "concept": concept, "old_value": old, "new_value": "APPROVED", "changed_at": datetime.now(timezone.utc).isoformat(), "reason": reason})
                self.save()
                return True
        return False

    def learn_draft(self, answer: ApplicationAnswer, reason: str = "Manual answer captured") -> None:
        """Store a reusable answer as DRAFT; explicit approval is still required."""
        answer.status = "DRAFT"
        self.add_or_update_answer(answer, reason)
