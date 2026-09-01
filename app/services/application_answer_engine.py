"""Deterministic question matcher and answer resolver for future form clients."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.models.application_answer import AnswerDecision, ApplicationAnswer
from app.services.application_answer_vault import ApplicationAnswerVault


MARKETS = {"uk": "united_kingdom", "united kingdom": "united_kingdom", "britain": "united_kingdom", "us": "united_states", "usa": "united_states", "united states": "united_states", "australia": "australia"}


class ApplicationAnswerEngine:
    def __init__(self, vault: ApplicationAnswerVault | None = None) -> None:
        self.vault = vault or ApplicationAnswerVault()

    def resolve(self, question_text: str, field_type: str | None = None, choices: list[str] | None = None, market: str | None = None, vacancy: Any | None = None, application_date: str | None = None) -> AnswerDecision:
        question = " ".join(question_text.lower().split())
        normalized_market = self._market(market or question)
        concept = self._match(question, normalized_market)
        if concept in {"LEGAL_DECLARATION", "VOLUNTARY_DEMOGRAPHIC", "CRIMINAL_HISTORY", "SECURITY_CLEARANCE", "CONFLICT_OF_INTEREST"}:
            return self._manual(concept, "Sensitive, legal, or voluntary declaration requires human review.", "LEGAL" if concept != "VOLUNTARY_DEMOGRAPHIC" else "VOLUNTARY_DEMOGRAPHIC")
        if concept.startswith("WORK_AUTHORIZATION_") or concept in {"EARLIEST_START_DATE"} or concept.startswith("WILLING_TO_RELOCATE_") or concept.startswith("WILLING_TO_TRAVEL_"):
            return self._rule(concept, normalized_market, application_date, choices)
        if concept in {"ROLE_MOTIVATION", "COMPANY_MOTIVATION", "RELEVANT_EXPERIENCE_SUMMARY", "FINTECH_TRANSITION_MOTIVATION"}:
            return self._generated(concept, vacancy, question_text)
        if concept == "EXPECTED_SALARY" and (field_type or "").upper() in {"NUMBER", "NUMERIC", "CURRENCY"}:
            return self._manual(concept, "Mandatory numeric compensation conversion has not been approved.", "SENSITIVE")
        # Task 21.31: a designation-choice list (e.g. "ACA / ACCA / CA /
        # CPA / CIMA / Other") needs a different resolution than a plain
        # Yes/No mapping -- select the option that actually, honestly
        # represents the candidate's Chartered Accountant (ICAI/ICAN)
        # qualification, never an ACA/ACCA option specifically. A plain
        # Yes/No choice list (or no choices at all) falls through to the
        # normal vault-answer path below unaffected.
        if concept in {"ACCOUNTING_QUALIFICATION", "ACCOUNTING_QUALIFICATION_ACA_ACCA", "ACCOUNTING_QUALIFICATION_OR_EQUIVALENT"} and choices and self._looks_like_qualification_choices(choices):
            picked = self._qualification_choice(choices)
            if picked is None:
                return self._manual(concept, "No offered choice can honestly represent the candidate's Chartered Accountant (ICAI/ICAN) qualification without overclaiming ACA/ACCA.", "CONTEXTUAL")
            return AnswerDecision(
                concept, picked, "AUTO_FILL", "HIGH", "PROFILE_FACT",
                "Chartered Accountant (ICAI/ICAN) mapped to the closest honest offered choice; ACA/ACCA never selected.",
                False, "STANDARD", "master_candidate_profile.json:education[Chartered Accountant]", picked,
            )
        answer = self.vault.get_answer(concept)
        if not answer or answer.status != "APPROVED" or answer.automation_policy == "MANUAL_REVIEW" or answer.confidence != "HIGH":
            return self._manual(concept, "No approved high-confidence reusable answer is available.")
        result = answer.value
        if choices:
            result = self._map_choices(result, choices)
            if result is None:
                return self._manual(concept, "Approved value cannot be mapped safely to the offered choices.")
        return AnswerDecision(concept, result, answer.automation_policy, answer.confidence, answer.answer_source, "Approved profile fact.", False, answer.sensitivity, answer.evidence_reference, result)

    def _match(self, q: str, market: str | None) -> str:
        if re.search(r"criminal|convict|offen[cs]e", q): return "CRIMINAL_HISTORY"
        if re.search(r"security clearance|background check|sanctions", q): return "SECURITY_CLEARANCE"
        if re.search(r"conflict of interest|related party|politically exposed|\bpep\b", q): return "CONFLICT_OF_INTEREST"
        if re.search(r"race|ethnic|disabilit|gender identity|sexual orientation|veteran|religion", q): return "VOLUNTARY_DEMOGRAPHIC"
        if re.search(r"certif|privacy|terms|consent|declaration", q): return "LEGAL_DECLARATION"
        if re.search(r"(personal |contact )?e-?mail|email address|^email$", q): return "EMAIL_ADDRESS"
        # Task 21.29 real-contact finding (LinkedIn Easy Apply): "Phone
        # country code" is a separate dial-code SELECT control paired next
        # to the real phone-number field -- it must never be answered with
        # the PHONE_NUMBER concept's full "+<code><number>" string (a
        # dropdown option label, not a phone number). Checked first so the
        # broader "phone" pattern below never claims it; falls through to
        # UNKNOWN, which safely SKIPs an optional field rather than
        # mis-filling it.
        if re.search(r"country code", q): return "UNKNOWN"
        if re.search(r"phone|mobile|telephone|contact number", q): return "PHONE_NUMBER"
        if re.search(r"^first name$|given name|^first$", q): return "FIRST_NAME"
        if re.search(r"^last name$|surname|family name|^last$", q): return "LAST_NAME"
        if re.search(r"^full name$|^name$|legal name", q): return "FULL_NAME"
        if "sponsor" in q or "visa" in q:
            suffix = {"united_kingdom": "UK", "united_states": "US", "australia": "AUSTRALIA"}.get(market or "")
            return f"SPONSORSHIP_{suffix}" if suffix else "SPONSORSHIP"
        if re.search(r"right to work|authorized to work|authorised to work|employment authorization|eligible to work", q):
            suffix = {"united_kingdom": "UK", "united_states": "US", "australia": "AUSTRALIA"}.get(market or "")
            return f"WORK_AUTHORIZATION_{suffix}" if suffix else "WORK_AUTHORIZATION"
        # Task 21.31: the candidate holds a Chartered Accountant
        # qualification through ICAI/ICAN, never ACA or ACCA specifically.
        # An exact designation question ("Are you ACA?", "Do you hold
        # ACA/ACCA?") must answer NO -- but "...or equivalent" phrasing
        # must not auto-fail the candidate; ICAI/ICAN CA is the approved
        # equivalent professional qualification.
        if re.search(r"\baca\b|\bacca\b", q):
            if re.search(r"or equivalent|equivalent professional", q):
                return "ACCOUNTING_QUALIFICATION_OR_EQUIVALENT"
            return "ACCOUNTING_QUALIFICATION_ACA_ACCA"
        if re.search(r"qualified accountant|chartered accountant|recognised accounting qualification", q): return "ACCOUNTING_QUALIFICATION"
        if re.search(r"highest qualification|degree|education", q): return "EDUCATION"
        # Task 21.30: the candidate's current location is now an explicitly
        # human-approved standing fact (Kathmandu, Nepal) -- but "current
        # city"/"current country"/"current location" are three genuinely
        # different questions expecting three different answer shapes
        # (city only / country only / combined "City, Country"), so each
        # gets its own concept and its own approved vault value. Checked
        # most-specific (city) first so "current city" is never absorbed by
        # a broader "current location" match.
        if re.search(r"current city|city of (residence|birth)|which city .*(currently|now)|city you .*(currently )?(live|reside|based)", q): return "CURRENT_CITY"
        if re.search(r"country of residence|current country|which country .*(currently|now)", q): return "CURRENT_LOCATION_COUNTRY"
        if re.search(r"current location|where .* currently (live|based)|currently based|where are you (currently )?based", q): return "CURRENT_LOCATION"
        if re.search(r"how many years.*\bsql\b", q): return "SQL_YEARS"
        if re.search(r"\bsql\b.*experience", q): return "SQL_EXPERIENCE"
        if re.search(r"how many years.*\bpython\b", q): return "PYTHON_YEARS"
        if re.search(r"\bpython\b.*experience", q): return "PYTHON_EXPERIENCE"
        if "salary" in q or "compensation" in q or "desired pay" in q or "hourly rate" in q: return "EXPECTED_SALARY"
        if re.search(r"notice period|how much notice|current notice", q): return "NOTICE_PERIOD"
        if re.search(r"earliest start|start date|availability|available to start", q): return "EARLIEST_START_DATE"
        if "relocation assistance" in q: return "RELOCATION_ASSISTANCE"
        if "relocat" in q:
            suffix = {"united_kingdom": "UK", "united_states": "US", "australia": "AUSTRALIA"}.get(market or "")
            return f"WILLING_TO_RELOCATE_{suffix}" if suffix else "WILLING_TO_RELOCATE"
        if "travel" in q:
            if re.search(r"percent|percentage|%", q): return "TRAVEL_PERCENTAGE"
            suffix = {"united_kingdom": "UK", "united_states": "US", "australia": "AUSTRALIA"}.get(market or "")
            return f"WILLING_TO_TRAVEL_{suffix}" if suffix else "WILLING_TO_TRAVEL"
        if "remote" in q and re.search(r"comfortable|preference|willing", q): return "REMOTE_WORK_PREFERENCE"
        if re.search(r"why .*?(role|position|opportunity)|interested in (this )?(role|position|opportunity)", q): return "ROLE_MOTIVATION"
        if re.search(r"why .*?(company|organisation|organization|us)", q): return "COMPANY_MOTIVATION"
        if re.search(r"fintech|financial technology|moving into technology", q): return "FINTECH_TRANSITION_MOTIVATION"
        if re.search(r"describe .*experience|relevant experience|why are you suited", q): return "RELEVANT_EXPERIENCE_SUMMARY"
        return "UNKNOWN"

    def _rule(self, concept: str, market: str | None, application_date: str | None, choices: list[str] | None = None) -> AnswerDecision:
        context = {"market": market}
        today = date.fromisoformat(application_date) if application_date else date.today()
        for rule in sorted(self.vault.rules, key=lambda x: x.priority, reverse=True):
            if rule.concept != concept or rule.status != "APPROVED": continue
            effective = rule.conditions.get("effective_from")
            if effective and today < date.fromisoformat(effective): continue
            if all(context.get(key) == value for key, value in rule.conditions.items() if key != "effective_from"):
                result = (today.fromordinal(today.toordinal() + 7).isoformat() if rule.result == "APPLICATION_DATE_PLUS_7_DAYS" else rule.result)
                if choices:
                    result = self._map_choices(result, choices)
                    if result is None: return self._manual(concept, "Approved rule cannot be mapped safely to the offered choices.")
                return AnswerDecision(concept, result, rule.automation_policy, rule.confidence, rule.answer_source, rule.explanation, False, rule.sensitivity, f"rule:{rule.rule_id}")
        return self._manual(concept, "No approved market-specific work-authorization rule applies.", "LEGAL")

    def _generated(self, concept: str, vacancy: Any | None, question: str) -> AnswerDecision:
        title = self._field(vacancy, "title")
        if concept == "FINTECH_TRANSITION_MOTIVATION":
            text = "I bring 15+ years of accounting, finance, audit and advisory experience, complemented by financial systems, automation, Python, SQL and data-analytics capability. I am deliberately developing toward financial technology while applying that established finance foundation."
        elif concept == "RELEVANT_EXPERIENCE_SUMMARY":
            text = "I bring 15+ years across accounting, financial reporting, finance management, audit, advisory, financial modelling, ERP/process improvement, automation and data-driven finance."
        else:
            role = f"The {title} opportunity" if title else "This opportunity"
            text = f"{role} aligns with my 15+ years of accounting, finance, audit and advisory experience, together with financial systems, automation, Python, SQL and data-analytics capability."
        return AnswerDecision(concept, text, "AUTO_FILL_WITH_RULES", "HIGH", "GENERATED_WITH_EVIDENCE", "Generated only from approved profile evidence and vacancy context.", False, "CONTEXTUAL", "master_candidate_profile.json")

    @staticmethod
    def fit_character_limit(decision: AnswerDecision, max_length: int | None) -> AnswerDecision:
        if not max_length or not isinstance(decision.answer, str) or len(decision.answer) <= max_length: return decision
        text = decision.answer[: max(0, max_length - 1)].rsplit(" ", 1)[0].rstrip() + "…"
        decision.answer = text
        return decision

    @staticmethod
    def _field(value: Any, name: str) -> str | None:
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None) if value else None

    @staticmethod
    def _market(value: str | None) -> str | None:
        text = (value or "").lower()
        if text in {"united_kingdom", "united_states", "australia"}:
            return text
        return next((result for label, result in MARKETS.items() if re.search(rf"\b{re.escape(label)}\b", text)), None)

    @staticmethod
    def _map_choices(value: Any, choices: list[str]) -> str | None:
        normalized = {str(x).strip().lower(): x for x in choices}
        if str(value).strip().lower() in normalized: return normalized[str(value).strip().lower()]
        if isinstance(value, bool) or str(value).upper() in {"YES", "NO"}:
            desired = "yes" if value is True or str(value).upper() == "YES" else "no"
            return next((x for x in choices if x.strip().lower() in {desired, desired[0]}), None)
        return None

    _QUALIFICATION_CHOICE_MARKERS = {"aca", "acca", "ca", "cpa", "cima", "chartered accountant", "equivalent", "other"}

    @classmethod
    def _looks_like_qualification_choices(cls, choices: list[str]) -> bool:
        """True only for a genuine designation-choice list (e.g. ACA/ACCA/
        CA/CPA/CIMA/Other) -- never for a plain Yes/No choice list, which
        must keep using the ordinary vault-answer YES/NO mapping."""
        return any(str(c).strip().lower() in cls._QUALIFICATION_CHOICE_MARKERS for c in choices)

    @staticmethod
    def _qualification_choice(choices: list[str]) -> str | None:
        """Task 21.31: select the offered choice that honestly represents
        the candidate's Chartered Accountant (ICAI/ICAN) qualification --
        preferring an explicit CA/Chartered Accountant option, then an
        Equivalent/Other option, and NEVER an ACA/ACCA-specific option
        (the candidate does not hold that separately verified membership).
        Returns None when no honest choice exists (e.g. only ACA/ACCA
        options with no CA/Equivalent/Other alternative) so the caller
        routes to human review rather than overclaiming."""
        normalized = {str(c).strip().lower(): c for c in choices}
        for label in ("ca", "chartered accountant"):
            if label in normalized:
                return normalized[label]
        for label in ("equivalent", "other"):
            if label in normalized:
                return normalized[label]
        return None

    @staticmethod
    def _manual(concept: str, reason: str, sensitivity: str = "CONTEXTUAL") -> AnswerDecision:
        return AnswerDecision(concept, None, "MANUAL_REVIEW", "LOW", "MANUAL_REQUIRED", reason, True, sensitivity)
