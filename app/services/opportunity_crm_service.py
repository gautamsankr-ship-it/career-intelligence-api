"""Task 21.32: Application CRM / Tracking Module.

Single source of truth for the complete lifecycle of every opportunity, from
DISCOVERY through APPLICATION through EMPLOYER RESPONSE through INTERVIEW
through OFFER / REJECTION / HIRED.

Deliberately NOT a second, competing tracking system: the canonical
opportunity record stays the existing `application_history` row (one row per
`job_fingerprint`, keyed by its integer `id` == "tracker_id"), reused via
composition with `ApplicationHistoryService`. Everything new here -- the
richer `crm_stage` lifecycle, immutable event history, human blockers,
employer responses, recruiter/hiring-manager contacts, interviews, and
offers -- lives in the SAME sqlite database file, as additive columns and new
tables. The legacy `status`/`application_status` vocabulary and every
existing consumer (CareerAgent, ApplicationPackageOrchestrator,
FinalReviewService, ApplicationSubmissionService,
application_eligibility_policy) are completely untouched.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.models.crm import (
    ACTIVE_FORWARD_ORDER,
    ALL_STAGES,
    ATTENTION_STAGES,
    EMPLOYER_RESPONSE_TYPES,
    HUMAN_BLOCKER_TYPES,
    INTERVIEW_STAGES,
    LEGACY_STATUS_TO_CRM_STAGE,
    OFFER_ACCEPTED,
    OFFER_DECLINED,
    OFFER_PENDING,
    PIPELINE_VIEW_STAGES,
    TERMINAL_STAGES,
    BLOCKER_OPEN,
    BLOCKER_RESOLVED,
)
from app.services.application_history_service import ApplicationHistoryService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_allowed_transitions() -> dict[str, frozenset[str]]:
    """A literal, precomputed lookup table -- not a formula -- of every
    permitted `crm_stage` transition, built once at import time from the
    ordered `ACTIVE_FORWARD_ORDER` plus a small set of deliberate, documented
    exceptions:

      * "Skip-ahead" to any LATER stage is allowed in general ("do not force
        every opportunity through every stage"), EXCEPT for ACCEPTED and
        HIRED, which are reachable only from their immediate predecessor
        (OFFER and ACCEPTED respectively) -- the one hard integrity rule this
        task requires ("HIRED cannot precede OFFER/appropriate evidence").
      * Each stage may branch to a curated set of terminal/branch stages
        appropriate to its zone (e.g. DECLINED_OFFER only from OFFER).
      * WATCHED is the one non-fully-terminal branch: a Priority-D "kept for
        future reconsideration" opportunity can resume back into the funnel.
      * Every stage may "transition" to itself (idempotent re-affirmation).
      * Every terminal stage (see `TERMINAL_STAGES`) has no outgoing
        transition at all, other than to itself.
    """
    order = ACTIVE_FORWARD_ORDER
    index = {stage: i for i, stage in enumerate(order)}
    early_branches = {"INELIGIBLE", "DUPLICATE", "INVALID_VACANCY", "EXPIRED", "WITHDRAWN", "WATCHED", "FAILED"}
    prep_branches = {"WITHDRAWN", "EXPIRED", "INELIGIBLE", "FAILED"}
    applied_branches = {"REJECTED", "WITHDRAWN"}
    offer_branches = {"REJECTED", "WITHDRAWN", "DECLINED_OFFER"}
    accepted_branches = {"WITHDRAWN"}

    early_zone = order[: index["SHORTLISTED"] + 1]
    prep_zone = order[index["PREPARED"] : index["READY_FOR_HUMAN_SUBMIT"] + 1]
    applied_zone = order[index["APPLIED"] : index["FINAL_INTERVIEW"] + 1]

    def zone_branches(stage: str) -> set[str]:
        if stage in early_zone:
            return early_branches
        if stage in prep_zone:
            return prep_branches
        if stage in applied_zone:
            return applied_branches
        if stage == "OFFER":
            return offer_branches
        if stage == "ACCEPTED":
            return accepted_branches
        return set()

    transitions: dict[str, frozenset[str]] = {}
    for stage in order:
        i = index[stage]
        forward = set(order[i + 1 :])
        # ACCEPTED/HIRED are sequential-only -- never a skip-ahead target
        # from an earlier stage than their immediate predecessor.
        if stage != "OFFER":
            forward.discard("ACCEPTED")
        if stage != "ACCEPTED":
            forward.discard("HIRED")
        transitions[stage] = frozenset(forward | zone_branches(stage) | {stage})

    for stage in TERMINAL_STAGES:
        transitions.setdefault(stage, frozenset({stage}))
    # WATCHED can resume back into the early funnel, or still be finally
    # dropped -- the one non-fully-terminal branch stage.
    transitions["WATCHED"] = frozenset(
        {"WATCHED", "ELIGIBILITY_REVIEW", "ELIGIBLE", "SCORED", "SHORTLISTED"} | early_branches
    )
    return transitions


ALLOWED_TRANSITIONS = _build_allowed_transitions()

_APPLICATION_HISTORY_ADDITIONS = {
    "crm_stage": "TEXT",
    "crm_stage_updated_at": "TEXT",
    "package_id": "TEXT",
    "resume_pdf_path": "TEXT",
    "submission_confirmation_reference": "TEXT",
    "submission_confirmation_source": "TEXT",
    "rejection_stage": "TEXT",
    "rejection_reason": "TEXT",
    "rejection_at": "TEXT",
    "offer_at": "TEXT",
    "offer_reference": "TEXT",
    "offer_decision": "TEXT",
    "offer_decision_at": "TEXT",
    "hired_at": "TEXT",
}

_ALLOWED_BREAKDOWN_FIELDS = frozenset({
    "source", "market", "career_track", "company", "application_portal",
    "intelligence_priority", "opportunity_value", "candidate_competitiveness",
    "crm_stage", "resume_path", "application_method",
})


class OpportunityCRMService:
    def __init__(self, history: ApplicationHistoryService | None = None) -> None:
        self.history = history or ApplicationHistoryService()
        self.connection: sqlite3.Connection = self.history.connection
        self._initialize_schema()

    # -- schema ---------------------------------------------------------
    def _initialize_schema(self) -> None:
        existing = {row[1] for row in self.connection.execute("PRAGMA table_info(application_history)")}
        for column, definition in _APPLICATION_HISTORY_ADDITIONS.items():
            if column not in existing:
                self.connection.execute(f"ALTER TABLE application_history ADD COLUMN {column} {definition}")

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS opportunity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                previous_stage TEXT,
                new_stage TEXT,
                occurred_at TEXT NOT NULL,
                source TEXT,
                reason TEXT,
                evidence_reference TEXT,
                actor TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS human_blockers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER NOT NULL,
                blocker_type TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution_note TEXT,
                resolved_by TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS employer_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER NOT NULL,
                response_type TEXT NOT NULL,
                received_at TEXT NOT NULL,
                source TEXT,
                summary TEXT,
                evidence_reference TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS recruiter_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER NOT NULL,
                name TEXT,
                role TEXT,
                contact_reference TEXT,
                outreach_status TEXT,
                outreach_date TEXT,
                outreach_channel TEXT,
                response_status TEXT,
                response_date TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                scheduled_at TEXT,
                completed_at TEXT,
                outcome TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER NOT NULL,
                offer_date TEXT,
                details_reference TEXT,
                status TEXT NOT NULL,
                decision_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    # -- opportunity CRUD -------------------------------------------------
    def get_opportunity(self, tracker_id: int) -> dict | None:
        return self.history.get_record_by_id(tracker_id)

    def create_opportunity(self, job_fingerprint_value: str, **fields) -> dict | None:
        """Create the one permanent CRM record for this vacancy (delegates
        de-duplication entirely to `ApplicationHistoryService.claim_job` --
        no second identity/dedup mechanism). Returns None for a duplicate,
        matching `claim_job`'s own contract."""
        status = fields.pop("status", "DISCOVERED")
        accepted = self.history.claim_job(job_fingerprint_value, status=status, **fields)
        if not accepted:
            return None
        record = self.history.get_record(job_fingerprint_value)
        stage = LEGACY_STATUS_TO_CRM_STAGE.get(status, "DISCOVERED")
        self._set_stage(record["id"], stage)
        self.append_event(record["id"], "OPPORTUNITY_CREATED", new_stage=stage, source="SYSTEM")
        return self.get_opportunity(record["id"])

    _PROTECTED_FIELDS = frozenset({"id", "job_fingerprint", "crm_stage", "crm_stage_updated_at", "status", "application_status"})

    def update_opportunity(self, tracker_id: int, **fields) -> dict:
        """Update allowed metadata only -- lifecycle stage never changes
        here; use `transition_stage` (or one of the `record_*` methods,
        which call it) instead."""
        blocked = self._PROTECTED_FIELDS & fields.keys()
        if blocked:
            raise ValueError(f"Cannot set protected field(s) via update_opportunity: {sorted(blocked)}")
        record = self._require(tracker_id)
        self.history.update_record(record["job_fingerprint"], **fields)
        return self.get_opportunity(tracker_id)

    # -- lifecycle --------------------------------------------------------
    def transition_stage(
        self, tracker_id: int, new_stage: str, *, reason: str = "", source: str = "SYSTEM",
        evidence_reference: str = "", actor: str = "",
    ) -> dict:
        if new_stage not in ALL_STAGES:
            raise ValueError(f"Unknown CRM lifecycle stage: {new_stage!r}")
        record = self._require(tracker_id)
        current = record.get("crm_stage") or "DISCOVERED"
        if new_stage == "APPLIED" and not evidence_reference:
            # Critical invariant: APPLIED only after confirmed submission
            # success. Never inferred merely because Submit was clicked.
            raise ValueError("Transitioning to APPLIED requires confirmed submission evidence_reference.")
        allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
        if new_stage not in allowed:
            raise ValueError(f"Cannot transition opportunity {tracker_id} from {current} to {new_stage}.")
        self._set_stage(tracker_id, new_stage)
        self.append_event(
            tracker_id, "STAGE_TRANSITION", previous_stage=current, new_stage=new_stage,
            reason=reason, source=source, evidence_reference=evidence_reference, actor=actor,
        )
        return self.get_opportunity(tracker_id)

    def _set_stage(self, tracker_id: int, new_stage: str, *, updated_at: str | None = None) -> None:
        record = self.history.get_record_by_id(tracker_id)
        self.history.update_record(record["job_fingerprint"], crm_stage=new_stage, crm_stage_updated_at=updated_at or _now())

    def append_event(self, tracker_id: int, event_type: str, **fields) -> dict:
        row = {
            "tracker_id": tracker_id, "event_type": event_type,
            "previous_stage": fields.get("previous_stage", ""), "new_stage": fields.get("new_stage", ""),
            "occurred_at": fields.get("occurred_at") or _now(), "source": fields.get("source", "SYSTEM"),
            "reason": fields.get("reason", ""), "evidence_reference": fields.get("evidence_reference", ""),
            "actor": fields.get("actor", ""),
        }
        cursor = self.connection.execute(
            "INSERT INTO opportunity_events (tracker_id, event_type, previous_stage, new_stage, occurred_at, source, reason, evidence_reference, actor) "
            "VALUES (:tracker_id, :event_type, :previous_stage, :new_stage, :occurred_at, :source, :reason, :evidence_reference, :actor)",
            row,
        )
        self.connection.commit()
        row["id"] = cursor.lastrowid
        return row

    # -- human blockers -----------------------------------------------------
    def record_human_blocker(self, tracker_id: int, blocker_type: str, detail: str = "") -> dict:
        if blocker_type not in HUMAN_BLOCKER_TYPES:
            raise ValueError(f"Unknown human blocker type: {blocker_type!r}")
        existing = self.connection.execute(
            "SELECT * FROM human_blockers WHERE tracker_id = ? AND blocker_type = ? AND status = ?",
            (tracker_id, blocker_type, BLOCKER_OPEN),
        ).fetchone()
        if existing:
            return dict(existing)
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO human_blockers (tracker_id, blocker_type, status, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (tracker_id, blocker_type, BLOCKER_OPEN, detail, now),
        )
        self.connection.commit()
        self.append_event(tracker_id, "BLOCKER_CREATED", reason=blocker_type, evidence_reference=detail)
        return self._blocker_row(cursor.lastrowid)

    def resolve_human_blocker(self, blocker_id: int, resolution_note: str = "", resolved_by: str = "") -> dict:
        blocker = self._blocker_row(blocker_id)
        if not blocker:
            raise ValueError(f"No human blocker found with ID {blocker_id}.")
        if blocker["status"] == BLOCKER_RESOLVED:
            return blocker
        now = _now()
        self.connection.execute(
            "UPDATE human_blockers SET status = ?, resolved_at = ?, resolution_note = ?, resolved_by = ? WHERE id = ?",
            (BLOCKER_RESOLVED, now, resolution_note, resolved_by, blocker_id),
        )
        self.connection.commit()
        self.append_event(
            blocker["tracker_id"], "BLOCKER_RESOLVED", reason=blocker["blocker_type"],
            evidence_reference=resolution_note, actor=resolved_by,
        )
        return self._blocker_row(blocker_id)

    def _blocker_row(self, blocker_id: int) -> dict | None:
        row = self.connection.execute("SELECT * FROM human_blockers WHERE id = ?", (blocker_id,)).fetchone()
        return dict(row) if row else None

    def list_open_blockers(self, tracker_id: int | None = None) -> list[dict]:
        query = "SELECT * FROM human_blockers WHERE status = ?"
        params: tuple = (BLOCKER_OPEN,)
        if tracker_id is not None:
            query += " AND tracker_id = ?"
            params += (tracker_id,)
        return [dict(row) for row in self.connection.execute(query, params)]

    # -- application package / submission -----------------------------------
    def record_application_package(
        self, tracker_id: int, package_id: str, resume_path: str = "", resume_pdf_path: str = "",
        cover_letter_path: str = "",
    ) -> dict:
        record = self._require(tracker_id)
        updates = {"package_id": package_id}
        if resume_path:
            updates["resume_path"] = resume_path
        if resume_pdf_path:
            updates["resume_pdf_path"] = resume_pdf_path
        if cover_letter_path:
            updates["cover_letter_path"] = cover_letter_path
        self.history.update_record(record["job_fingerprint"], **updates)
        self.append_event(tracker_id, "PACKAGE_RECORDED", evidence_reference=package_id)
        current = (record.get("crm_stage") or "DISCOVERED")
        if "PREPARED" in ALLOWED_TRANSITIONS.get(current, frozenset()) and current != "PREPARED":
            self.transition_stage(tracker_id, "PREPARED", reason="Application package recorded", evidence_reference=package_id)
        return self.get_opportunity(tracker_id)

    def record_submission_confirmation(
        self, tracker_id: int, *, confirmed_at: str = "", confirmation_source: str = "",
        confirmation_evidence: str, submission_reference: str = "",
    ) -> dict:
        if not confirmation_evidence:
            raise ValueError("record_submission_confirmation requires non-empty confirmation_evidence.")
        record = self._require(tracker_id)
        # Idempotent: re-confirming the same submission is a safe no-op, not
        # a duplicate event.
        if record.get("crm_stage") == "APPLIED" and submission_reference and record.get("submission_confirmation_reference") == submission_reference:
            return record
        confirmed_at = confirmed_at or _now()
        updates = {
            "submission_confirmation_reference": submission_reference,
            "submission_confirmation_source": confirmation_source,
        }
        if not record.get("applied_at"):
            updates["applied_at"] = confirmed_at
        self.history.update_record(record["job_fingerprint"], **updates)
        # Keep the legacy status vocabulary in sync going forward, but never
        # regress a status already past APPLIED in the legacy lifecycle.
        if record.get("status") not in {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"}:
            self.history.update_record(record["job_fingerprint"], status="APPLIED", application_status="APPLIED")
        self.transition_stage(
            tracker_id, "APPLIED", reason="Confirmed submission", source=confirmation_source or "SYSTEM",
            evidence_reference=confirmation_evidence,
        )
        return self.get_opportunity(tracker_id)

    # -- employer responses --------------------------------------------------
    def record_employer_response(
        self, tracker_id: int, response_type: str, *, received_at: str = "", source: str = "",
        summary: str = "", evidence_reference: str = "",
    ) -> dict:
        if response_type not in EMPLOYER_RESPONSE_TYPES:
            raise ValueError(f"Unknown employer response type: {response_type!r}")
        received_at = received_at or _now()
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO employer_responses (tracker_id, response_type, received_at, source, summary, evidence_reference, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tracker_id, response_type, received_at, source, summary, evidence_reference, now),
        )
        self.connection.commit()
        self.append_event(tracker_id, "EMPLOYER_RESPONSE_RECORDED", reason=response_type, evidence_reference=evidence_reference, source=source or "GMAIL")

        stage_for_response = {
            "ACKNOWLEDGEMENT": "ACKNOWLEDGED",
            "RECRUITER_CONTACT": "RECRUITER_RESPONSE",
            "SCREENING_REQUEST": "SCREENING",
        }.get(response_type)
        record = self._require(tracker_id)
        current = record.get("crm_stage") or "DISCOVERED"
        if stage_for_response and stage_for_response in ALLOWED_TRANSITIONS.get(current, frozenset()) and current != stage_for_response:
            self.transition_stage(tracker_id, stage_for_response, reason=f"Employer response: {response_type}", evidence_reference=evidence_reference or response_type)
        elif response_type == "REJECTION":
            self.record_rejection(tracker_id, rejection_reason=summary, rejected_at=received_at)
        elif response_type == "OFFER":
            self.record_offer(tracker_id, offer_date=received_at, details_reference=evidence_reference or summary)

        row = self.connection.execute("SELECT * FROM employer_responses WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    # -- interviews -----------------------------------------------------------
    def record_interview(self, tracker_id: int, stage: str, *, scheduled_at: str = "", notes: str = "") -> dict:
        if stage not in INTERVIEW_STAGES:
            raise ValueError(f"Unknown interview stage: {stage!r}")
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO interviews (tracker_id, stage, scheduled_at, outcome, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tracker_id, stage, scheduled_at, "SCHEDULED", notes, now, now),
        )
        self.connection.commit()
        interview_id = cursor.lastrowid
        self.append_event(tracker_id, "INTERVIEW_RECORDED", reason=stage, evidence_reference=str(interview_id))
        record = self._require(tracker_id)
        current = record.get("crm_stage") or "DISCOVERED"
        if stage in ALLOWED_TRANSITIONS.get(current, frozenset()) and current != stage:
            self.transition_stage(tracker_id, stage, reason=f"Interview scheduled: {stage}", evidence_reference=str(interview_id))
        return self._interview_row(interview_id)

    def update_interview_outcome(self, interview_id: int, outcome: str, *, completed_at: str = "", notes: str = "") -> dict:
        interview = self._interview_row(interview_id)
        if not interview:
            raise ValueError(f"No interview found with ID {interview_id}.")
        now = _now()
        self.connection.execute(
            "UPDATE interviews SET outcome = ?, completed_at = ?, notes = ?, updated_at = ? WHERE id = ?",
            (outcome, completed_at or interview.get("completed_at") or "", notes or interview.get("notes") or "", now, interview_id),
        )
        self.connection.commit()
        self.append_event(interview["tracker_id"], "INTERVIEW_OUTCOME_RECORDED", reason=outcome, evidence_reference=str(interview_id))
        return self._interview_row(interview_id)

    def _interview_row(self, interview_id: int) -> dict | None:
        row = self.connection.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,)).fetchone()
        return dict(row) if row else None

    # -- rejection / offer / hire --------------------------------------------
    def record_rejection(self, tracker_id: int, *, rejection_stage: str = "", rejection_reason: str = "", rejected_at: str = "") -> dict:
        record = self._require(tracker_id)
        current = record.get("crm_stage") or "DISCOVERED"
        if current == "REJECTED":
            return record
        rejected_at = rejected_at or _now()
        self.history.update_record(
            record["job_fingerprint"], rejection_stage=rejection_stage or current,
            rejection_reason=rejection_reason, rejection_at=rejected_at,
        )
        self.transition_stage(tracker_id, "REJECTED", reason=rejection_reason or "Employer rejection", evidence_reference=rejection_reason or "rejection recorded")
        return self.get_opportunity(tracker_id)

    def record_offer(self, tracker_id: int, *, offer_date: str = "", details_reference: str = "") -> dict:
        offer_date = offer_date or _now()
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO offers (tracker_id, offer_date, details_reference, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tracker_id, offer_date, details_reference, OFFER_PENDING, now, now),
        )
        self.connection.commit()
        record = self._require(tracker_id)
        self.history.update_record(record["job_fingerprint"], offer_at=offer_date, offer_reference=details_reference)
        self.append_event(tracker_id, "OFFER_RECORDED", evidence_reference=details_reference)
        self.transition_stage(tracker_id, "OFFER", reason="Offer received", evidence_reference=details_reference or "offer recorded")
        return self._offer_row(cursor.lastrowid)

    def record_offer_decision(self, offer_id: int, decision: str, *, decision_date: str = "") -> dict:
        if decision not in {OFFER_ACCEPTED, OFFER_DECLINED}:
            raise ValueError(f"Offer decision must be {OFFER_ACCEPTED} or {OFFER_DECLINED}, got {decision!r}.")
        offer = self._offer_row(offer_id)
        if not offer:
            raise ValueError(f"No offer found with ID {offer_id}.")
        decision_date = decision_date or _now()
        self.connection.execute(
            "UPDATE offers SET status = ?, decision_date = ?, updated_at = ? WHERE id = ?",
            (decision, decision_date, _now(), offer_id),
        )
        self.connection.commit()
        record = self._require(offer["tracker_id"])
        self.history.update_record(record["job_fingerprint"], offer_decision=decision, offer_decision_at=decision_date)
        self.append_event(offer["tracker_id"], "OFFER_DECISION_RECORDED", reason=decision, evidence_reference=str(offer_id))
        target_stage = "ACCEPTED" if decision == OFFER_ACCEPTED else "DECLINED_OFFER"
        self.transition_stage(offer["tracker_id"], target_stage, reason=f"Offer {decision.lower()}", evidence_reference=str(offer_id))
        return self._offer_row(offer_id)

    def _offer_row(self, offer_id: int) -> dict | None:
        row = self.connection.execute("SELECT * FROM offers WHERE id = ?", (offer_id,)).fetchone()
        return dict(row) if row else None

    def record_hire(self, tracker_id: int, *, hired_at: str = "") -> dict:
        hired_at = hired_at or _now()
        record = self._require(tracker_id)
        self.history.update_record(record["job_fingerprint"], hired_at=hired_at)
        self.append_event(tracker_id, "HIRE_RECORDED", evidence_reference=hired_at)
        # transition_stage enforces the integrity control itself: HIRED is
        # only ever reachable from ACCEPTED.
        self.transition_stage(tracker_id, "HIRED", reason="Hired", evidence_reference=hired_at)
        return self.get_opportunity(tracker_id)

    # -- recruiter / hiring-manager contacts ---------------------------------
    def record_recruiter_contact(
        self, tracker_id: int, *, name: str = "", role: str = "", contact_reference: str = "",
        outreach_status: str = "NOT_CONTACTED", outreach_date: str = "", outreach_channel: str = "", notes: str = "",
    ) -> dict:
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO recruiter_contacts (tracker_id, name, role, contact_reference, outreach_status, outreach_date, outreach_channel, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tracker_id, name, role, contact_reference, outreach_status, outreach_date, outreach_channel, notes, now, now),
        )
        self.connection.commit()
        self.append_event(tracker_id, "RECRUITER_CONTACT_RECORDED", evidence_reference=contact_reference)
        return self._contact_row(cursor.lastrowid)

    _CONTACT_UPDATE_FIELDS = frozenset({"outreach_status", "outreach_date", "outreach_channel", "response_status", "response_date", "notes"})

    def update_recruiter_contact(self, contact_id: int, **fields) -> dict:
        contact = self._contact_row(contact_id)
        if not contact:
            raise ValueError(f"No recruiter contact found with ID {contact_id}.")
        unknown = fields.keys() - self._CONTACT_UPDATE_FIELDS
        if unknown:
            raise ValueError(f"Cannot update unrecognized recruiter_contacts field(s): {sorted(unknown)}")
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields["id"] = contact_id
        self.connection.execute(f"UPDATE recruiter_contacts SET {assignments} WHERE id = :id", fields)
        self.connection.commit()
        return self._contact_row(contact_id)

    def _contact_row(self, contact_id: int) -> dict | None:
        row = self.connection.execute("SELECT * FROM recruiter_contacts WHERE id = ?", (contact_id,)).fetchone()
        return dict(row) if row else None

    # -- queries ----------------------------------------------------------
    def list_opportunities_by_stage(self, stage: str) -> list[dict]:
        if stage not in ALL_STAGES:
            raise ValueError(f"Unknown CRM lifecycle stage: {stage!r}")
        return [
            dict(row) for row in
            self.connection.execute("SELECT * FROM application_history WHERE crm_stage = ? ORDER BY id DESC", (stage,))
        ]

    def get_timeline(self, tracker_id: int) -> list[dict]:
        """The complete, chronologically-ordered history for one
        opportunity: every lifecycle event, blocker, employer response,
        interview, and offer -- normalized to a common shape and merged, not
        just the current `crm_stage`."""
        entries: list[dict] = []
        for row in self.connection.execute("SELECT * FROM opportunity_events WHERE tracker_id = ?", (tracker_id,)):
            item = dict(row)
            entries.append({"kind": "EVENT", "at": item["occurred_at"], "detail": item})
        for row in self.connection.execute("SELECT * FROM human_blockers WHERE tracker_id = ?", (tracker_id,)):
            item = dict(row)
            entries.append({"kind": "BLOCKER", "at": item["created_at"], "detail": item})
        for row in self.connection.execute("SELECT * FROM employer_responses WHERE tracker_id = ?", (tracker_id,)):
            item = dict(row)
            entries.append({"kind": "EMPLOYER_RESPONSE", "at": item["received_at"], "detail": item})
        for row in self.connection.execute("SELECT * FROM interviews WHERE tracker_id = ?", (tracker_id,)):
            item = dict(row)
            entries.append({"kind": "INTERVIEW", "at": item["created_at"], "detail": item})
        for row in self.connection.execute("SELECT * FROM offers WHERE tracker_id = ?", (tracker_id,)):
            item = dict(row)
            entries.append({"kind": "OFFER", "at": item["created_at"], "detail": item})
        entries.sort(key=lambda entry: entry["at"] or "")
        return entries

    def _require(self, tracker_id: int) -> dict:
        record = self.get_opportunity(tracker_id)
        if not record:
            raise ValueError(f"No tracked opportunity found with ID {tracker_id}.")
        return record

    # -- migration ----------------------------------------------------------
    def migrate_legacy_records(self) -> dict[str, int]:
        """One-way, idempotent reconciliation of every pre-existing
        `application_history` row into the new `crm_stage` lifecycle.
        Never fabricates history: a record is mapped from its own real,
        already-persisted `status` (see `LEGACY_STATUS_TO_CRM_STAGE`), using
        its own real timestamps where available and NULL/UNKNOWN otherwise.
        Records that already carry a `crm_stage` (from a previous migration
        run, or created going forward via `create_opportunity`) are skipped
        entirely -- safe to run repeatedly."""
        summary = {"migrated": 0, "already_migrated": 0, "submission_confirmed_backfilled": 0}
        for record in self.history.list_records():
            if record.get("crm_stage"):
                summary["already_migrated"] += 1
                continue
            status = record.get("status") or "DISCOVERED"
            stage = LEGACY_STATUS_TO_CRM_STAGE.get(status, "DISCOVERED")
            updated_at = record.get("processed_at") or record.get("discovered_at") or _now()
            self._set_stage(record["id"], stage, updated_at=updated_at)
            self.append_event(
                record["id"], "MIGRATED_STAGE", new_stage=stage, source="MIGRATION",
                reason=f"Backfilled from legacy status={status}", occurred_at=updated_at,
            )
            summary["migrated"] += 1

            if status == "APPLIED" and record.get("applied_at") and record.get("notes"):
                source = "LINKEDIN_JOB_TRACKER_HUMAN_CONFIRMED" if "LinkedIn Job Tracker" in record["notes"] else "HISTORICAL_NOTES"
                self.history.update_record(
                    record["job_fingerprint"], submission_confirmation_reference="LEGACY_NOTES_EVIDENCE",
                    submission_confirmation_source=source,
                )
                self.append_event(
                    record["id"], "SUBMISSION_CONFIRMED", previous_stage=stage, new_stage="APPLIED", source="MIGRATION",
                    reason="Reconciled from pre-existing application_history.notes first-party confirmation evidence",
                    evidence_reference=record["notes"], occurred_at=record["applied_at"],
                )
                summary["submission_confirmed_backfilled"] += 1
            elif status == "SENT" and record.get("sent_at"):
                self.history.update_record(
                    record["job_fingerprint"], submission_confirmation_reference=record.get("gmail_message_id") or "",
                    submission_confirmation_source="GMAIL",
                )
                self.append_event(
                    record["id"], "SUBMISSION_CONFIRMED", previous_stage=stage, new_stage="APPLIED", source="MIGRATION",
                    reason="Reconciled from Gmail send confirmation (sent_at/gmail_message_id)",
                    evidence_reference=record.get("gmail_message_id") or "sent_at recorded", occurred_at=record["sent_at"],
                )
                summary["submission_confirmed_backfilled"] += 1
        return summary

    # -- dashboard read-model (Task 21.33 builds the UI on top of this) -----
    def funnel_counts(self) -> dict[str, int]:
        total = self.connection.execute("SELECT COUNT(*) FROM application_history").fetchone()[0]

        def reached(stage: str) -> int:
            row = self.connection.execute(
                "SELECT COUNT(DISTINCT tracker_id) FROM opportunity_events WHERE new_stage = ?", (stage,)
            ).fetchone()
            return row[0]

        def distinct(table: str, where: str = "", params: tuple = ()) -> int:
            query = f"SELECT COUNT(DISTINCT tracker_id) FROM {table}"
            if where:
                query += f" WHERE {where}"
            return self.connection.execute(query, params).fetchone()[0]

        return {
            # `application_history` only ever stores de-duplicated rows
            # (job_fingerprint UNIQUE) -- "discovered" and "unique" are the
            # same count here; raw pre-dedup discovery volume isn't
            # persisted anywhere structurally queryable, so it's never
            # fabricated as a distinct figure.
            "discovered": total,
            "unique": total,
            "eligible": reached("ELIGIBLE"),
            "shortlisted": reached("SHORTLISTED"),
            "prepared": reached("PREPARED"),
            "applied": reached("APPLIED"),
            "acknowledged": distinct("employer_responses", "response_type = ?", ("ACKNOWLEDGEMENT",)),
            "responses": distinct("employer_responses"),
            "interviews": distinct("interviews"),
            "offers": distinct("offers"),
            "hired": reached("HIRED"),
        }

    # -- Web App Phase 1.1: corrected, evidence-based cumulative funnel ------
    def cumulative_funnel_counts(self) -> dict[str, int]:
        """Cumulative application-outcome milestones, each backed by
        direct, independently-verifiable evidence -- never inferred merely
        because a LATER crm_stage was reached.

        Phase 1 originally inferred a milestone from a per-tracker
        high-water-mark stage index (reasoning: reaching a later stage
        implies passing the earlier ones). Reconciling that against real
        production data (Task Phase 1.1 audit) proved it wrong: this CRM's
        `ALLOWED_TRANSITIONS` deliberately permits skip-ahead ("do not force
        every opportunity through every stage"), and every one of this
        production database's 157 records either skip-ahead-transitioned
        past ELIGIBLE/SHORTLISTED or was legacy-migrated straight to a later
        stage -- there is not one single literal event, in this database's
        entire history, of any tracker actually stopping at ELIGIBLE or
        SHORTLISTED. The high-water-mark method credited 13 records with
        "reached ELIGIBLE/SHORTLISTED" purely from later crm_stage labels
        (9 legacy MANUAL_WEB_REQUIRED records with NULL remote_eligibility
        AND NULL intelligence_priority, 1 legacy DRAFTED record, and the 3
        real APPLIED records) -- a fabricated milestone this data cannot
        actually support, not a genuine fact about the business process.

        Every figure below instead comes from its own dedicated, directly
        queryable evidence, with no cross-stage inference at all:
          * discovered: every application_history row (trivially true).
          * applied: `applied_at IS NOT NULL` -- set only by
            `record_submission_confirmation` on a confirmed real
            submission, never by a stage label alone.
          * acknowledged / meaningful_response: `employer_responses` rows,
            the same per-tracker evidence `response_quality_counts()` uses.
          * interview / offer: the dedicated `interviews`/`offers` tables.
        "Eligible"/"Shortlisted"/"Prepared" are deliberately NOT included:
        this production CRM has no discrete, verifiable evidence for either
        as a distinct milestone today."""
        quality = self.response_quality_counts()
        return {
            "DISCOVERED": self.connection.execute("SELECT COUNT(*) FROM application_history").fetchone()[0],
            "APPLIED": self.connection.execute(
                "SELECT COUNT(*) FROM application_history WHERE applied_at IS NOT NULL"
            ).fetchone()[0],
            "ACKNOWLEDGED": quality["acknowledgements"],
            "MEANINGFUL_RESPONSE": quality["meaningful_responses"],
            "INTERVIEW": self.connection.execute("SELECT COUNT(DISTINCT tracker_id) FROM interviews").fetchone()[0],
            "OFFER": self.connection.execute("SELECT COUNT(DISTINCT tracker_id) FROM offers").fetchone()[0],
        }

    def application_performance_rates(self) -> dict[str, float | None]:
        """Decision-useful conversion rates computed only from
        `cumulative_funnel_counts()`'s evidence-based figures -- replaces
        Phase 1's shortlisted_to_applied/applied_to_response/... rates,
        which either divided by a denominator this data has never actually
        supported (shortlisted) or conflated an automated acknowledgement
        with a genuine employer response (responses). None (not 0.0) when
        the denominator is zero -- an undefined rate is never a fabricated
        zero."""
        counts = self.cumulative_funnel_counts()

        def ratio(numerator: int, denominator: int) -> float | None:
            return round(numerator / denominator, 4) if denominator else None

        return {
            "applied_to_acknowledged": ratio(counts["ACKNOWLEDGED"], counts["APPLIED"]),
            "applied_to_meaningful_response": ratio(counts["MEANINGFUL_RESPONSE"], counts["APPLIED"]),
            "applied_to_interview": ratio(counts["INTERVIEW"], counts["APPLIED"]),
            "interview_to_offer": ratio(counts["OFFER"], counts["INTERVIEW"]),
        }

    # Every PIPELINE_VIEW_STAGES value must appear in exactly one group --
    # verified at call time in `pipeline_group_counts()` and by
    # `test_pipeline_group_counts_cover_every_stage_exactly_once` -- so the
    # grouped totals are guaranteed to sum to exactly the same total
    # `pipeline_counts()` already does (each opportunity has exactly one
    # current crm_stage, so this is a pure relabeling, never a re-count).
    PIPELINE_GROUPS = (
        ("Screening & Eligibility", ("DISCOVERED", "VERIFIED", "ELIGIBILITY_REVIEW", "ELIGIBLE", "SCORED")),
        ("Shortlisted", ("SHORTLISTED",)),
        ("Preparing Application", ("PREPARED", "READY_FOR_REVIEW")),
        ("Awaiting My Action", ("READY_FOR_HUMAN_SUBMIT",)),
        ("Applied", ("APPLIED",)),
        ("Employer Response", ("ACKNOWLEDGED", "RECRUITER_RESPONSE", "SCREENING")),
        ("Interview", ("INTERVIEW_1", "INTERVIEW_2", "FINAL_INTERVIEW")),
        ("Offer / Hired", ("OFFER", "ACCEPTED", "HIRED")),
        ("On Hold", ("WATCHED",)),
        ("Not Proceeding", ("REJECTED", "INELIGIBLE", "WITHDRAWN", "INVALID_VACANCY", "DUPLICATE", "EXPIRED", "DECLINED_OFFER", "FAILED")),
    )

    def pipeline_group_counts(self) -> list[dict]:
        """The current, mutually-exclusive crm_stage distribution
        (`pipeline_counts()`) relabeled into a small number of
        business-facing groups for the Executive Dashboard -- never
        exposing the full internal CRM state machine. A pure relabeling of
        already-reconciled data (each opportunity contributes to exactly
        one group), so the grouped counts always sum to the same total
        `pipeline_counts()` does."""
        stage_counts = self.pipeline_counts()
        covered = {stage for _, stages in self.PIPELINE_GROUPS for stage in stages}
        missing = set(PIPELINE_VIEW_STAGES) - covered
        if missing:
            raise AssertionError(f"pipeline_group_counts() is missing stage(s) from its grouping: {sorted(missing)}")
        return [
            {"label": label, "count": sum(stage_counts.get(stage, 0) for stage in stages), "stages": stages}
            for label, stages in self.PIPELINE_GROUPS
        ]

    def priority_mix_counts(self) -> dict[str, int]:
        """A/B/C/D/E distribution PLUS an explicit "UNSCORED" bucket for
        any record with no recorded intelligence_priority -- so percentages
        always account for every opportunity. Filtering out a falsy
        intelligence_priority (as the Phase 1 dashboard did) silently
        dropped ~20% of this production database's records from the mix."""
        rows = self.connection.execute(
            "SELECT COALESCE(NULLIF(intelligence_priority, ''), 'UNSCORED') AS value, COUNT(*) AS n "
            "FROM application_history GROUP BY value"
        ).fetchall()
        return {row["value"]: row["n"] for row in rows}

    def response_quality_counts(self) -> dict[str, int]:
        """Distinguishes an automated ACKNOWLEDGEMENT from a genuinely
        meaningful employer/recruiter response (recruiter contact,
        screening request, interview invitation, assessment request,
        rejection, or offer) -- Web App Phase 1: an acknowledgement must
        never be presented as, or counted toward, a recruiter response."""
        meaningful_types = (
            "RECRUITER_CONTACT", "SCREENING_REQUEST", "INTERVIEW_INVITATION",
            "ASSESSMENT_REQUEST", "REJECTION", "OFFER",
        )

        def distinct_where_type_in(types: tuple[str, ...]) -> int:
            placeholders = ",".join("?" * len(types))
            row = self.connection.execute(
                f"SELECT COUNT(DISTINCT tracker_id) FROM employer_responses WHERE response_type IN ({placeholders})",
                types,
            ).fetchone()
            return row[0]

        return {
            "acknowledgements": distinct_where_type_in(("ACKNOWLEDGEMENT",)),
            "meaningful_responses": distinct_where_type_in(meaningful_types),
            "unknown_responses": distinct_where_type_in(("UNKNOWN",)),
        }

    def recent_activity(self, limit: int = 15, tracker_ids: list[int] | None = None) -> list[dict]:
        """Most recent CRM events, newest first -- a read-only activity
        feed; never mutates state. `tracker_ids=None` (the default) spans
        every opportunity; an explicit list (e.g. the current dashboard
        filter's result set) scopes the feed to just those trackers, so a
        filtered view never leaks another opportunity's activity/company
        name back onto the page."""
        query = "SELECT e.*, ah.company, ah.job_title FROM opportunity_events e JOIN application_history ah ON ah.id = e.tracker_id"
        params: tuple = ()
        if tracker_ids is not None:
            if not tracker_ids:
                return []
            placeholders = ",".join("?" * len(tracker_ids))
            query += f" WHERE e.tracker_id IN ({placeholders})"
            params = tuple(tracker_ids)
        query += " ORDER BY e.occurred_at DESC LIMIT ?"
        rows = self.connection.execute(query, params + (limit,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def conversion_rates(counts: dict[str, int] | None = None) -> dict[str, float | None]:
        """Ratios between consecutive funnel milestones. None (not 0.0) when
        the denominator is zero -- an undefined rate is never reported as a
        fabricated zero."""
        def ratio(numerator: int, denominator: int) -> float | None:
            return round(numerator / denominator, 4) if denominator else None

        c = counts
        return {
            "discovery_to_eligible": ratio(c["eligible"], c["discovered"]),
            "eligible_to_shortlisted": ratio(c["shortlisted"], c["eligible"]),
            "shortlisted_to_applied": ratio(c["applied"], c["shortlisted"]),
            "applied_to_response": ratio(c["responses"], c["applied"]),
            "response_to_interview": ratio(c["interviews"], c["responses"]),
            "interview_to_offer": ratio(c["offers"], c["interviews"]),
            "offer_to_hired": ratio(c["hired"], c["offers"]),
        }

    def breakdown_by(self, field: str) -> list[dict[str, Any]]:
        """Distribution of opportunities by one dimension, for later
        dashboard/analysis use. `field` is whitelisted (never interpolated
        from arbitrary caller input beyond that fixed set) since it names a
        raw SQL column."""
        if field not in _ALLOWED_BREAKDOWN_FIELDS:
            raise ValueError(f"Unsupported breakdown field: {field!r}. Allowed: {sorted(_ALLOWED_BREAKDOWN_FIELDS)}")
        rows = self.connection.execute(
            f"SELECT {field} AS value, COUNT(*) AS count FROM application_history GROUP BY {field} ORDER BY count DESC"
        ).fetchall()
        return [{"value": row["value"], "count": row["count"]} for row in rows]

    # -- dashboard (Task 21.33) ---------------------------------------------
    _LIST_FILTER_COLUMNS = frozenset({"crm_stage", "intelligence_priority", "market", "source", "application_portal"})

    def list_opportunities(self, **filters: str) -> list[dict]:
        """Every opportunity, optionally filtered by one or more of
        crm_stage/intelligence_priority/market/source/application_portal
        (an unrecognized filter key is rejected -- `filters` is never
        interpolated into SQL beyond this fixed whitelist). Each row also
        carries `open_blocker_count`, the same underlying check
        `needs_attention()` uses, so the UI never re-derives it."""
        unknown = filters.keys() - self._LIST_FILTER_COLUMNS
        if unknown:
            raise ValueError(f"Unsupported opportunity filter(s): {sorted(unknown)}")
        clauses = [f"{column} = :{column}" for column in filters]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            "SELECT ah.*, "
            "(SELECT COUNT(*) FROM human_blockers hb WHERE hb.tracker_id = ah.id AND hb.status = 'OPEN') AS open_blocker_count "
            f"FROM application_history ah {where} ORDER BY ah.id DESC"
        )
        return [dict(row) for row in self.connection.execute(query, filters)]

    def needs_attention(self) -> list[dict]:
        """Opportunities genuinely waiting on a human right now: either an
        ATTENTION_STAGES crm_stage, or an actually-open `human_blockers` row
        -- never a blocker inferred from a stale execution-session flag
        (e.g. a CAPTCHA/MFA field on an old ApplicationExecutionResult JSON)
        that was never recorded as a live CRM blocker."""
        entries: dict[int, dict] = {}
        for record in self.connection.execute(
            "SELECT * FROM application_history WHERE crm_stage IN ({})".format(
                ",".join("?" * len(ATTENTION_STAGES))
            ),
            tuple(ATTENTION_STAGES),
        ):
            row = dict(record)
            entries[row["id"]] = {
                "tracker_id": row["id"], "company": row.get("company") or "", "job_title": row.get("job_title") or "",
                "crm_stage": row.get("crm_stage"), "crm_stage_updated_at": row.get("crm_stage_updated_at"),
                "intelligence_priority": row.get("intelligence_priority"),
                "reasons": [f"Stage awaiting human action: {row.get('crm_stage')}"],
            }
        for blocker in self.list_open_blockers():
            tracker_id = blocker["tracker_id"]
            if tracker_id not in entries:
                record = self.get_opportunity(tracker_id)
                if not record:
                    continue
                entries[tracker_id] = {
                    "tracker_id": tracker_id, "company": record.get("company") or "", "job_title": record.get("job_title") or "",
                    "crm_stage": record.get("crm_stage"), "crm_stage_updated_at": record.get("crm_stage_updated_at"),
                    "intelligence_priority": record.get("intelligence_priority"),
                    "reasons": [],
                }
            entries[tracker_id]["reasons"].append(f"Open blocker: {blocker['blocker_type']}" + (f" -- {blocker['detail']}" if blocker.get("detail") else ""))
        return sorted(entries.values(), key=lambda entry: entry["tracker_id"], reverse=True)

    # -- Web App Phase 1.1: compact, plain-language attention queue ---------
    _STAGE_PLAIN_LANGUAGE = {
        "ELIGIBILITY_REVIEW": "Awaiting eligibility review",
        "READY_FOR_REVIEW": "Ready for your review",
        "READY_FOR_HUMAN_SUBMIT": "Ready for you to submit",
        "RECRUITER_RESPONSE": "Recruiter has responded",
        "SCREENING": "Screening in progress",
        "INTERVIEW_1": "Interview scheduled",
        "INTERVIEW_2": "Second interview scheduled",
        "FINAL_INTERVIEW": "Final interview scheduled",
        "OFFER": "Offer received",
    }
    _BLOCKER_PLAIN_LANGUAGE = {
        "HUMAN_CAPTCHA_REQUIRED": "CAPTCHA needs solving",
        "HUMAN_MFA_REQUIRED": "Login / verification needed",
        "HUMAN_ELIGIBILITY_REVIEW_REQUIRED": "Eligibility needs your review",
        "HUMAN_SALARY_REVIEW_REQUIRED": "Salary needs your review",
        "HUMAN_ANSWER_APPROVAL_REQUIRED": "An answer needs your approval",
        "READY_FOR_HUMAN_SUBMIT": "Ready for you to submit",
        "OTHER": "Needs your review",
    }
    # Most urgent first -- a live browser blocker outranks a routine
    # human-submit/eligibility wait, which outranks anything uncategorized.
    _URGENCY_ORDER = (
        "HUMAN_CAPTCHA_REQUIRED", "HUMAN_MFA_REQUIRED", "HUMAN_ANSWER_APPROVAL_REQUIRED",
        "HUMAN_ELIGIBILITY_REVIEW_REQUIRED", "HUMAN_SALARY_REVIEW_REQUIRED", "READY_FOR_HUMAN_SUBMIT", "OTHER",
    )
    _PRIORITY_ORDER = ("A", "B", "C", "D", "E")

    @classmethod
    def describe_attention_reason(cls, reason: str) -> str:
        """Plain-business-language translation of one raw `needs_attention()`
        reason string -- an executive view must never show a raw crm_stage
        or blocker-type code."""
        if reason.startswith("Stage awaiting human action: "):
            stage = reason[len("Stage awaiting human action: "):]
            return cls._STAGE_PLAIN_LANGUAGE.get(stage, f"Awaiting action ({stage.replace('_', ' ').title()})")
        if reason.startswith("Open blocker: "):
            blocker_type, _, detail = reason[len("Open blocker: "):].partition(" -- ")
            label = cls._BLOCKER_PLAIN_LANGUAGE.get(blocker_type, blocker_type.replace("_", " ").title())
            return f"{label}: {detail}" if detail else label
        return reason

    def _attention_urgency_rank(self, item: dict) -> int:
        blocker_types = [
            reason[len("Open blocker: "):].split(" -- ")[0]
            for reason in item["reasons"] if reason.startswith("Open blocker: ")
        ]
        if not blocker_types:
            # A stage-only wait (no open human_blockers row) -- e.g. the
            # routine "ready for you to submit" state -- is real but less
            # urgent than an active blocker stopping automated progress.
            return self._URGENCY_ORDER.index("READY_FOR_HUMAN_SUBMIT") if item.get("crm_stage") == "READY_FOR_HUMAN_SUBMIT" else len(self._URGENCY_ORDER)
        ranks = [self._URGENCY_ORDER.index(bt) if bt in self._URGENCY_ORDER else len(self._URGENCY_ORDER) for bt in blocker_types]
        return min(ranks)

    def _attention_priority_rank(self, item: dict) -> int:
        priority = item.get("intelligence_priority")
        return self._PRIORITY_ORDER.index(priority) if priority in self._PRIORITY_ORDER else len(self._PRIORITY_ORDER)

    def attention_queue(self, *, priority: str | None = None, limit: int | None = None) -> list[dict]:
        """`needs_attention()`'s items, ordered by (1) urgency/blocker type,
        (2) intelligence priority, (3) recency (most recent first), with a
        plain-language `plain_reasons` list added to each item -- optionally
        filtered to one A/B/C/D/E `priority` and capped by `limit`. The
        unfiltered, uncapped count is always `len(needs_attention())`."""
        items = self.needs_attention()
        if priority:
            items = [item for item in items if item.get("intelligence_priority") == priority]

        # Stable multi-key sort: recency (desc) first as the tie-breaker,
        # then re-sort by (urgency, priority) ascending -- Python's sort is
        # stable, so ties in the primary key preserve the recency ordering.
        items = sorted(items, key=lambda item: item.get("crm_stage_updated_at") or "", reverse=True)
        items = sorted(items, key=lambda item: (self._attention_urgency_rank(item), self._attention_priority_rank(item)))

        for item in items:
            item["plain_reasons"] = [self.describe_attention_reason(reason) for reason in item["reasons"]]

        return items[:limit] if limit else items

    def attention_priority_distribution(self) -> dict[str, int]:
        """Needs-attention item counts by intelligence priority (A/B/C/D/E,
        plus an explicit UNSCORED bucket) -- powers the Executive
        Dashboard's priority filter chips."""
        counts: dict[str, int] = {}
        for item in self.needs_attention():
            key = item.get("intelligence_priority") or "UNSCORED"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def pipeline_counts(self) -> dict[str, int]:
        """Current crm_stage distribution, with every stage in
        `PIPELINE_VIEW_STAGES` present (0 when nothing is there today) --
        never silently dropping a stage just because it's currently empty."""
        counts = {row["value"]: row["count"] for row in self.breakdown_by("crm_stage") if row["value"]}
        return {stage: counts.get(stage, 0) for stage in PIPELINE_VIEW_STAGES}

    def get_opportunity_detail(self, tracker_id: int) -> dict | None:
        """Everything the dashboard's opportunity-detail view needs, in one
        call -- the core record plus every related table's rows and the
        merged timeline. Returns None (never a fabricated placeholder) when
        the tracker doesn't exist."""
        record = self.get_opportunity(tracker_id)
        if not record:
            return None
        blockers = [dict(row) for row in self.connection.execute("SELECT * FROM human_blockers WHERE tracker_id = ? ORDER BY id", (tracker_id,))]
        recruiter_contacts = [dict(row) for row in self.connection.execute("SELECT * FROM recruiter_contacts WHERE tracker_id = ? ORDER BY id", (tracker_id,))]
        interviews = [dict(row) for row in self.connection.execute("SELECT * FROM interviews WHERE tracker_id = ? ORDER BY id", (tracker_id,))]
        offers = [dict(row) for row in self.connection.execute("SELECT * FROM offers WHERE tracker_id = ? ORDER BY id", (tracker_id,))]
        employer_responses = [dict(row) for row in self.connection.execute("SELECT * FROM employer_responses WHERE tracker_id = ? ORDER BY id", (tracker_id,))]
        return {
            "opportunity": record,
            "blockers": blockers,
            "recruiter_contacts": recruiter_contacts,
            "interviews": interviews,
            "offers": offers,
            "employer_responses": employer_responses,
            "timeline": self.get_timeline(tracker_id),
        }

    def close(self) -> None:
        self.history.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
