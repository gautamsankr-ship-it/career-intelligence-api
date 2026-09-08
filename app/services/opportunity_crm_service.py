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
from datetime import datetime, timedelta, timezone
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
    "crm_stage", "resume_path", "application_method", "work_arrangement",
})

# -- Web App Phase 2: user decisions ----------------------------------------
# A controlled human signal, stored and audited SEPARATELY from the
# intelligence engine's own A/B/C/D/E priority -- recording one here never
# writes to intelligence_priority/crm_stage/any scoring column. Append-only,
# same as opportunity_events: a changed mind is a NEW row, never an edit.
USER_DECISIONS = frozenset({"APPLY", "WATCH", "REJECT"})
USER_DECISION_REASON_CODES = frozenset({
    "SALARY_TOO_LOW", "TOO_JUNIOR", "COMPANY_UNATTRACTIVE", "LOCATION",
    "CAREER_VALUE", "NOT_GENUINELY_REMOTE", "ELIGIBILITY_WORK_RIGHT_CONCERN", "OTHER",
})

# -- Web App Phase 5: human classification of a genuinely UNKNOWN employer
# message (Employer Inbox) -- every real classification EXCEPT "still
# unknown" (reclassifying UNKNOWN as UNKNOWN resolves nothing), plus one
# catch-all for "I looked at this and it needs no action, but it doesn't fit
# a specific category" that GmailOutcomeMonitor's own vocabulary has no need
# for (see record_employer_response_classification).
EMPLOYER_RESPONSE_HUMAN_CLASSIFICATIONS = (EMPLOYER_RESPONSE_TYPES - {"UNKNOWN"}) | {"OTHER_NO_ACTION"}

# -- Web App Phase 7: Analytics & Learning governance -----------------------
# A Proposed Learning is the ONLY mechanism by which Analytics' observations
# may ever point toward a future policy change -- and even then, Accepting
# one in this phase records governance approval only (see
# record_proposed_learning docstring); it never itself rewrites
# intelligence_priority/scoring/eligibility logic. Never delete a row here:
# a changed mind is a NEW status-history entry (same append-only convention
# opportunity_events already uses), never a silent overwrite.
LEARNING_DOMAINS = frozenset({
    "SEARCH_STRATEGY", "MARKET", "PRIORITY", "ELIGIBILITY", "CV_STRATEGY",
    "APPLICATION_STRATEGY", "SCREENING", "EVIDENCE", "AUTOMATION",
})
LEARNING_STATUS_PROPOSED = "PROPOSED"
LEARNING_STATUS_ACCEPTED = "ACCEPTED"
LEARNING_STATUS_NEED_MORE_EVIDENCE = "NEED_MORE_EVIDENCE"
LEARNING_STATUS_REJECTED = "REJECTED"
LEARNING_STATUS_RETIRED = "RETIRED"
LEARNING_STATUSES = frozenset({
    LEARNING_STATUS_PROPOSED, LEARNING_STATUS_ACCEPTED, LEARNING_STATUS_NEED_MORE_EVIDENCE,
    LEARNING_STATUS_REJECTED, LEARNING_STATUS_RETIRED,
})
LEARNING_CONFIDENCE_LEVELS = frozenset({"Emerging", "Moderate", "High"})

# A genuinely meaningful employer/recruiter response, as opposed to an
# automated ACKNOWLEDGEMENT -- the ONE definition `response_quality_counts()`
# and (Web App Phase 7) `cumulative_funnel_counts_since()` both reuse, so
# Analytics can never silently diverge from the Dashboard's own figure.
MEANINGFUL_RESPONSE_TYPES = (
    "RECRUITER_CONTACT", "SCREENING_REQUEST", "INTERVIEW_INVITATION", "ASSESSMENT_REQUEST", "REJECTION", "OFFER",
)


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
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER NOT NULL,
                decision TEXT NOT NULL,
                reason_code TEXT,
                note TEXT,
                decided_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS application_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER NOT NULL,
                worth_pursuing TEXT NOT NULL,
                interest_change TEXT,
                note TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS employer_response_classifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracker_id INTEGER NOT NULL,
                employer_response_id INTEGER NOT NULL,
                resolved_type TEXT NOT NULL,
                note TEXT,
                classified_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                actor TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS proposed_learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                domain TEXT NOT NULL,
                title TEXT NOT NULL,
                observation TEXT NOT NULL,
                proposed_change TEXT NOT NULL,
                evidence_summary TEXT NOT NULL,
                sample_size INTEGER NOT NULL,
                confidence TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                review_note TEXT,
                actor TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS proposed_learning_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposed_learning_id INTEGER NOT NULL,
                previous_status TEXT,
                new_status TEXT NOT NULL,
                note TEXT,
                actor TEXT,
                occurred_at TEXT NOT NULL
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
        elif response_type == "INTERVIEW_INVITATION":
            self._maybe_auto_create_interview_from_invitation(tracker_id, evidence_reference=evidence_reference, summary=summary)

        row = self.connection.execute("SELECT * FROM employer_responses WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def _maybe_auto_create_interview_from_invitation(self, tracker_id: int, *, evidence_reference: str = "", summary: str = "") -> None:
        """Web App Phase 6: turns a genuinely detected interview invitation
        into a real interview record automatically -- the operational
        objective's "assemble the preparation workspace automatically", not
        a second interview-creation mechanism. Never invents a date/round the
        evidence doesn't support (`scheduled_at` stays blank until a real
        time is confirmed), and only fires once per tracker: if ANY interview
        row already exists, a further round is left to be recorded through
        the SAME existing `record_interview()` this always uses -- never a
        second, competing automatic path."""
        existing = self.connection.execute(
            "SELECT 1 FROM interviews WHERE tracker_id = ? LIMIT 1", (tracker_id,)
        ).fetchone()
        if existing:
            return
        note = "Auto-created from a detected employer interview invitation."
        if evidence_reference:
            note += f" Evidence: {evidence_reference}."
        if summary:
            note += f" \"{summary}\""
        self.record_interview(tracker_id, "INTERVIEW_1", notes=note)

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

    def update_interview_schedule(self, interview_id: int, scheduled_at: str, *, notes: str = "") -> dict:
        """The one genuinely consequential interview-record write this
        workspace exposes beyond outcome/notes: confirming a real date/time
        (item 17 -- "confirming an interview time if employer proposes
        alternatives" is explicitly a human decision, never inferred or
        auto-accepted)."""
        if not scheduled_at:
            raise ValueError("update_interview_schedule requires a non-empty scheduled_at.")
        interview = self._interview_row(interview_id)
        if not interview:
            raise ValueError(f"No interview found with ID {interview_id}.")
        now = _now()
        self.connection.execute(
            "UPDATE interviews SET scheduled_at = ?, notes = ?, updated_at = ? WHERE id = ?",
            (scheduled_at, notes or interview.get("notes") or "", now, interview_id),
        )
        self.connection.commit()
        self.append_event(interview["tracker_id"], "INTERVIEW_SCHEDULE_CONFIRMED", reason=scheduled_at, evidence_reference=str(interview_id))
        return self._interview_row(interview_id)

    def update_interview_notes(self, interview_id: int, notes: str) -> dict:
        """Optional user notes (item 14) -- never required. The smallest
        additive change: reuses the interviews table's own existing `notes`
        column rather than a new table, while still recording the change on
        the SAME append-only `opportunity_events` audit trail so a note
        history remains reconstructable even though the live column itself
        is overwritten (the same pattern `resolve_human_blocker`'s
        resolution_note already uses)."""
        interview = self._interview_row(interview_id)
        if not interview:
            raise ValueError(f"No interview found with ID {interview_id}.")
        now = _now()
        self.connection.execute(
            "UPDATE interviews SET notes = ?, updated_at = ? WHERE id = ?", (notes, now, interview_id),
        )
        self.connection.commit()
        self.append_event(
            interview["tracker_id"], "INTERVIEW_NOTES_UPDATED", reason=(notes[:200] if notes else ""),
            evidence_reference=str(interview_id),
        )
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

    # `employer_responses.received_at` is Gmail-sourced and, in at least one
    # real production row, was stored as the raw RFC-2822 header text
    # ("Tue, 01 Sep 2026 ...") rather than normalized ISO-8601 -- a plain
    # `>=` string comparison against an ISO cutoff would misplace that row
    # (an ASCII letter always sorts after a digit). This GLOB guard trusts
    # only genuinely ISO-shaped values for period math; a non-ISO row is
    # simply excluded from period slicing (it is still counted in the
    # All-Time figures, which do no date comparison at all) -- never a
    # rewrite of that stored Gmail evidence.
    _ISO_DATE_GLOB = "[0-9][0-9][0-9][0-9]-*"

    def cumulative_funnel_counts_since(self, since_iso: str) -> dict[str, int]:
        """Web App Phase 7: the SAME evidence definitions as
        `cumulative_funnel_counts()`, scoped to a period -- each stage
        filtered by its OWN genuine event timestamp (discovered_at/
        applied_at/received_at/created_at), never a fabricated historical
        series built by interpolating from current totals."""
        placeholders = ",".join("?" * len(MEANINGFUL_RESPONSE_TYPES))
        return {
            "DISCOVERED": self.connection.execute(
                "SELECT COUNT(*) FROM application_history WHERE discovered_at >= ?", (since_iso,)
            ).fetchone()[0],
            "APPLIED": self.connection.execute(
                "SELECT COUNT(*) FROM application_history WHERE applied_at IS NOT NULL AND applied_at >= ?", (since_iso,)
            ).fetchone()[0],
            "ACKNOWLEDGED": self.connection.execute(
                "SELECT COUNT(DISTINCT tracker_id) FROM employer_responses "
                "WHERE response_type = 'ACKNOWLEDGEMENT' AND received_at GLOB ? AND received_at >= ?",
                (self._ISO_DATE_GLOB, since_iso),
            ).fetchone()[0],
            "MEANINGFUL_RESPONSE": self.connection.execute(
                f"SELECT COUNT(DISTINCT tracker_id) FROM employer_responses "
                f"WHERE response_type IN ({placeholders}) AND received_at GLOB ? AND received_at >= ?",
                (*MEANINGFUL_RESPONSE_TYPES, self._ISO_DATE_GLOB, since_iso),
            ).fetchone()[0],
            "INTERVIEW": self.connection.execute(
                "SELECT COUNT(DISTINCT tracker_id) FROM interviews WHERE created_at >= ?", (since_iso,)
            ).fetchone()[0],
            "OFFER": self.connection.execute(
                "SELECT COUNT(DISTINCT tracker_id) FROM offers WHERE created_at >= ?", (since_iso,)
            ).fetchone()[0],
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
        def distinct_where_type_in(types: tuple[str, ...]) -> int:
            placeholders = ",".join("?" * len(types))
            row = self.connection.execute(
                f"SELECT COUNT(DISTINCT tracker_id) FROM employer_responses WHERE response_type IN ({placeholders})",
                types,
            ).fetchone()
            return row[0]

        return {
            "acknowledgements": distinct_where_type_in(("ACKNOWLEDGEMENT",)),
            "meaningful_responses": distinct_where_type_in(MEANINGFUL_RESPONSE_TYPES),
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

    def performance_by_dimension(self, field: str) -> list[dict]:
        """Web App Phase 7 (Performance Drivers): opportunities/applications/
        meaningful responses/interviews/offers per distinct value of `field`
        -- every count reuses the SAME evidence definitions
        `cumulative_funnel_counts()`/`response_quality_counts()` already use
        (applied_at IS NOT NULL, `MEANINGFUL_RESPONSE_TYPES`, the
        interviews/offers tables), never a second, parallel definition.
        Bucket `"__UNSET__"` groups every NULL/blank value -- labeling it is
        left to the caller (e.g. "Unscored" for priority)."""
        if field not in _ALLOWED_BREAKDOWN_FIELDS:
            raise ValueError(f"Unsupported breakdown field: {field!r}. Allowed: {sorted(_ALLOWED_BREAKDOWN_FIELDS)}")
        placeholders = ",".join("?" * len(MEANINGFUL_RESPONSE_TYPES))
        rows = self.connection.execute(
            f"""
            SELECT
                COALESCE(NULLIF(ah.{field}, ''), '__UNSET__') AS bucket,
                COUNT(DISTINCT ah.id) AS opportunities,
                COUNT(DISTINCT CASE WHEN ah.applied_at IS NOT NULL THEN ah.id END) AS applications,
                COUNT(DISTINCT CASE WHEN er.response_type IN ({placeholders}) THEN ah.id END) AS meaningful_responses,
                COUNT(DISTINCT iv.tracker_id) AS interviews,
                COUNT(DISTINCT ofr.tracker_id) AS offers
            FROM application_history ah
            LEFT JOIN employer_responses er ON er.tracker_id = ah.id
            LEFT JOIN interviews iv ON iv.tracker_id = ah.id
            LEFT JOIN offers ofr ON ofr.tracker_id = ah.id
            GROUP BY bucket
            ORDER BY opportunities DESC
            """,
            MEANINGFUL_RESPONSE_TYPES,
        ).fetchall()
        return [dict(row) for row in rows]

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

    # -- Web App Phase 3: Action Required read model --------------------------
    # Critical product principle (see Phase 3 audit): Priority C
    # (HUMAN_REVIEW) is an intelligence/application-priority signal, NOT
    # proof of a concrete, ready-to-act human intervention. The Phase 1
    # `needs_attention()`/`ATTENTION_STAGES` membership check treats bare
    # crm_stage membership as "needs attention" -- for this production
    # database that produced 127 items, but a read-only audit found ZERO of
    # them backed by an actual OPEN `human_blockers` row, and the 118
    # ELIGIBILITY_REVIEW-stage records include 9 that were never even
    # eligibility-assessed (remote_eligibility NULL) and 4 already marked
    # ELIGIBLE (stuck there only for an unrelated borderline-score review,
    # per LEGACY_STATUS_TO_CRM_STAGE's shared "REVIEW"/"REMOTE_ELIGIBILITY_
    # REVIEW" -> ELIGIBILITY_REVIEW mapping) -- i.e. that count conflates
    # "the intelligence engine will eventually want a human look" with "a
    # concrete question is ready for you right now".
    #
    # `action_required_items()` instead builds the queue ONLY from already-
    # recorded, independently-verifiable facts, one path per category:
    #   REVIEW_AND_SUBMIT   -- crm_stage genuinely reached PREPARED/
    #                          READY_FOR_REVIEW/READY_FOR_HUMAN_SUBMIT (a
    #                          package or route was actually resolved).
    #   ANSWER_REQUIRED /
    #   BROWSER_ACTION /
    #   (an eligibility blocker, if one is ever recorded)
    #                       -- an OPEN human_blockers row, mapped by its
    #                          own blocker_type (never fabricated).
    #   ELIGIBILITY_DECISION -- remote_eligibility == "MANUAL_REVIEW" (the
    #                          eligibility CLASSIFIER's own "a human must
    #                          decide" value -- see remote_work_eligibility.py
    #                          -- never bare ELIGIBILITY_REVIEW stage
    #                          membership) AND crm_stage is still
    #                          ELIGIBILITY_REVIEW (excludes the 3 records
    #                          that already progressed to
    #                          ACKNOWLEDGED/APPLIED, the 1 already
    #                          INVALID_VACANCY, and the 2 already WATCHED --
    #                          the question is moot for all of those).
    #   EMPLOYER_ACTION     -- an employer_responses row whose response_type
    #                          is genuinely meaningful (reuses
    #                          `response_quality_counts()`'s own
    #                          acknowledgement-is-never-meaningful rule),
    #                          not yet marked reviewed.
    #
    # Resolution reuses existing mechanisms wherever one already exists --
    # no parallel/competing state machine is introduced:
    #   BLOCKER-sourced      -> the existing `resolve_human_blocker()`.
    #   REVIEW_AND_SUBMIT    -> resolves itself once crm_stage progresses
    #                          past those three stages, or a user_decision
    #                          (existing, Phase 2) is recorded.
    #   ELIGIBILITY_DECISION -> resolves once ANY user_decision (existing,
    #                          Phase 2 Apply/Watch/Reject) is recorded for
    #                          that tracker -- reuses the SAME decision
    #                          mechanism the Opportunity Detail page already
    #                          writes to, rather than a second one.
    #   EMPLOYER_ACTION      -> the one case with no existing "reviewed"
    #                          concept; resolved via a new
    #                          EMPLOYER_ACTION_REVIEWED entry in the
    #                          EXISTING, already-immutable opportunity_events
    #                          audit trail (see `mark_employer_response_
    #                          reviewed()`) -- no new table.

    ACTION_CATEGORIES = ("REVIEW_AND_SUBMIT", "ANSWER_REQUIRED", "ELIGIBILITY_DECISION", "BROWSER_ACTION", "EMPLOYER_ACTION")

    _BLOCKER_TYPE_TO_ACTION_CATEGORY = {
        "READY_FOR_HUMAN_SUBMIT": "REVIEW_AND_SUBMIT",
        "HUMAN_ANSWER_APPROVAL_REQUIRED": "ANSWER_REQUIRED",
        "HUMAN_SALARY_REVIEW_REQUIRED": "ANSWER_REQUIRED",
        "HUMAN_ELIGIBILITY_REVIEW_REQUIRED": "ELIGIBILITY_DECISION",
        "HUMAN_CAPTCHA_REQUIRED": "BROWSER_ACTION",
        "HUMAN_MFA_REQUIRED": "BROWSER_ACTION",
        # A conservative default: an unclassified blocker still genuinely
        # needs a human look, so it is never silently dropped from the
        # queue -- it lands in Answer Required rather than a 6th category.
        "OTHER": "ANSWER_REQUIRED",
    }
    _EMPLOYER_ACTION_RESPONSE_TYPES = (
        "RECRUITER_CONTACT", "SCREENING_REQUEST", "INTERVIEW_INVITATION", "ASSESSMENT_REQUEST", "OFFER", "UNKNOWN",
    )
    _REVIEW_AND_SUBMIT_STAGES = ("PREPARED", "READY_FOR_REVIEW", "READY_FOR_HUMAN_SUBMIT")

    def action_required_items(self, *, include_resolved: bool = False) -> list[dict]:
        """The concrete, evidence-backed Action Required queue. Each item:
        tracker_id, company, job_title, intelligence_priority, career_score,
        crm_stage, category, source ("BLOCKER"/"STAGE"/"ELIGIBILITY"/
        "EMPLOYER_RESPONSE"), reason (plain-language-ready raw fact),
        arose_at, resolved (bool), and blocker_id/employer_response_id
        where applicable. `include_resolved=True` also returns resolved
        items, for the Action Required history view."""
        items: list[dict] = []
        seen_tracker_category: set[tuple[int, str]] = set()

        def base_fields(record: dict) -> dict:
            return {
                "tracker_id": record["id"], "company": record.get("company") or "",
                "job_title": record.get("job_title") or "", "intelligence_priority": record.get("intelligence_priority"),
                "career_score": record.get("career_score"), "crm_stage": record.get("crm_stage"),
            }

        # 1. OPEN (and, if requested, RESOLVED) human_blockers.
        blocker_rows = self.connection.execute(
            "SELECT * FROM human_blockers" + ("" if include_resolved else " WHERE status = 'OPEN'")
        ).fetchall()
        for blocker in blocker_rows:
            blocker = dict(blocker)
            record = self.get_opportunity(blocker["tracker_id"])
            if not record:
                continue
            category = self._BLOCKER_TYPE_TO_ACTION_CATEGORY.get(blocker["blocker_type"], "ANSWER_REQUIRED")
            seen_tracker_category.add((blocker["tracker_id"], category))
            items.append({
                **base_fields(record), "category": category, "source": "BLOCKER",
                "reason": blocker.get("detail") or blocker["blocker_type"], "arose_at": blocker["created_at"],
                "resolved": blocker["status"] == BLOCKER_RESOLVED, "blocker_id": blocker["id"],
                "blocker_type": blocker["blocker_type"], "employer_response_id": None, "response_type": None,
            })

        # 2. REVIEW_AND_SUBMIT: a package/route genuinely reached readiness.
        for record in self.connection.execute(
            "SELECT * FROM application_history WHERE crm_stage IN ({})".format(
                ",".join("?" * len(self._REVIEW_AND_SUBMIT_STAGES))
            ),
            self._REVIEW_AND_SUBMIT_STAGES,
        ):
            record = dict(record)
            key = (record["id"], "REVIEW_AND_SUBMIT")
            if key in seen_tracker_category:
                continue
            latest_decision = self.get_latest_user_decision(record["id"])
            resolved = latest_decision is not None and latest_decision["decision"] in ("WATCH", "REJECT")
            if resolved and not include_resolved:
                continue
            items.append({
                **base_fields(record), "category": "REVIEW_AND_SUBMIT", "source": "STAGE",
                "reason": "Application prepared and ready for your review and submission.",
                "arose_at": record.get("crm_stage_updated_at") or record.get("processed_at") or record.get("discovered_at"),
                "resolved": resolved, "blocker_id": None, "blocker_type": None,
                "employer_response_id": None, "response_type": None,
            })

        # 3. ELIGIBILITY_DECISION: the classifier's own "a human must
        #    decide" value, still in the active ELIGIBILITY_REVIEW stage.
        for record in self.connection.execute(
            "SELECT * FROM application_history WHERE remote_eligibility = 'MANUAL_REVIEW' AND crm_stage = 'ELIGIBILITY_REVIEW'"
        ):
            record = dict(record)
            key = (record["id"], "ELIGIBILITY_DECISION")
            if key in seen_tracker_category:
                continue
            latest_decision = self.get_latest_user_decision(record["id"])
            resolved = latest_decision is not None
            if resolved and not include_resolved:
                continue
            items.append({
                **base_fields(record), "category": "ELIGIBILITY_DECISION", "source": "ELIGIBILITY",
                "reason": record.get("remote_eligibility_reason") or "Remote role is silent on overseas eligibility -- needs your review.",
                "arose_at": record.get("crm_stage_updated_at") or record.get("discovered_at"),
                "resolved": resolved, "blocker_id": None, "blocker_type": None,
                "employer_response_id": None, "response_type": None,
            })

        # 4. EMPLOYER_ACTION: a genuinely meaningful (never merely an
        #    automated acknowledgement) employer response.
        placeholders = ",".join("?" * len(self._EMPLOYER_ACTION_RESPONSE_TYPES))
        for row in self.connection.execute(
            f"SELECT * FROM employer_responses WHERE response_type IN ({placeholders}) ORDER BY id", self._EMPLOYER_ACTION_RESPONSE_TYPES,
        ):
            row = dict(row)
            record = self.get_opportunity(row["tracker_id"])
            if not record:
                continue
            resolved = self._is_employer_response_reviewed(row["id"])
            if resolved and not include_resolved:
                continue
            items.append({
                **base_fields(record), "category": "EMPLOYER_ACTION", "source": "EMPLOYER_RESPONSE",
                "reason": row.get("summary") or row["response_type"], "arose_at": row["received_at"],
                "resolved": resolved, "blocker_id": None, "blocker_type": None,
                "employer_response_id": row["id"], "response_type": row["response_type"],
            })

        items.sort(key=lambda item: item.get("arose_at") or "", reverse=True)
        return items

    def action_required_counts(self) -> dict[str, int]:
        """Active (unresolved) Action Required counts by category, plus
        "TOTAL" -- always the sum of the five, so the summary cards
        reconcile exactly to the active queue by construction."""
        items = self.action_required_items()
        counts = {category: 0 for category in self.ACTION_CATEGORIES}
        for item in items:
            counts[item["category"]] += 1
        counts["TOTAL"] = len(items)
        return counts

    def mark_employer_response_reviewed(self, tracker_id: int, employer_response_id: int, note: str = "", actor: str = "USER") -> dict:
        """The one Action Required resolution with no pre-existing
        mechanism to reuse -- recorded as a new, immutable event on the
        SAME `opportunity_events` audit trail every other CRM action
        already uses (never a new table, never mutating the
        employer_responses row itself)."""
        self._require(tracker_id)
        row = self.connection.execute("SELECT * FROM employer_responses WHERE id = ?", (employer_response_id,)).fetchone()
        if not row or row["tracker_id"] != tracker_id:
            raise ValueError(f"No employer_responses row {employer_response_id} found for tracker {tracker_id}.")
        return self.append_event(
            tracker_id, "EMPLOYER_ACTION_REVIEWED", reason=note, evidence_reference=str(employer_response_id), actor=actor,
        )

    def _is_employer_response_reviewed(self, employer_response_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM opportunity_events WHERE event_type = 'EMPLOYER_ACTION_REVIEWED' AND evidence_reference = ? LIMIT 1",
            (str(employer_response_id),),
        ).fetchone()
        return row is not None

    # -- Web App Phase 4: Applications workspace read model -----------------
    # An "application" (business sense) is any opportunity that reached
    # actual package preparation or later -- a superset of "submitted"
    # (item 15: Preparing/Ready records belong here too, since a package
    # not yet submitted is still an application in progress). Business-
    # facing tab -> the real, unchanged crm_stage values that belong to it;
    # never a new lifecycle, purely a display grouping over the existing
    # CRM stages (same pattern as PIPELINE_GROUPS).
    APPLICATION_TABS = {
        "preparing": ("PREPARED", "READY_FOR_REVIEW"),
        "ready": ("READY_FOR_HUMAN_SUBMIT",),
        "applied": ("APPLIED",),
        "response": ("ACKNOWLEDGED", "RECRUITER_RESPONSE", "SCREENING", "REJECTED", "DECLINED_OFFER"),
        "interview": ("INTERVIEW_1", "INTERVIEW_2", "FINAL_INTERVIEW", "OFFER", "ACCEPTED", "HIRED"),
    }
    APPLICATION_WORKSPACE_STAGES = tuple(stage for stages in APPLICATION_TABS.values() for stage in stages)
    # Default ordering (spec: active employer/interview action, then recent
    # submissions/responses, then preparation work, then older records) --
    # a display-ranking concern only, never a scoring system.
    _APPLICATION_RANK_SQL = (
        "CASE crm_stage "
        "WHEN 'RECRUITER_RESPONSE' THEN 0 WHEN 'SCREENING' THEN 0 WHEN 'INTERVIEW_1' THEN 0 WHEN 'INTERVIEW_2' THEN 0 "
        "WHEN 'FINAL_INTERVIEW' THEN 0 WHEN 'OFFER' THEN 0 WHEN 'ACCEPTED' THEN 0 "
        "WHEN 'APPLIED' THEN 1 WHEN 'ACKNOWLEDGED' THEN 1 "
        "WHEN 'PREPARED' THEN 2 WHEN 'READY_FOR_REVIEW' THEN 2 WHEN 'READY_FOR_HUMAN_SUBMIT' THEN 2 "
        "ELSE 3 END"
    )

    # Web App Phase 7.1: a reserved tab value, deliberately NOT a crm_stage
    # grouping like the business tabs above -- "applied" (a APPLICATION_TABS
    # entry) means "currently sitting at the APPLIED stage right now" (1
    # record today, since 61/81 have since moved on to ACKNOWLEDGED), which
    # is a genuinely different, smaller population than "ever confirmed
    # submitted" (3 records: 61, 81, 103) -- the SAME evidence
    # (`applied_at IS NOT NULL`) `cumulative_funnel_counts()`'s own APPLIED
    # figure and the Dashboard/Analytics "Applications Submitted" KPI use.
    # This is the one drill-through URL that reconciles exactly with that
    # KPI; never conflated with the "applied" business-stage tab.
    SUBMITTED_TAB = "submitted"

    def applications_register(self, *, tab: str = "", page: int = 1, page_size: int = 25) -> dict:
        """Paginated Applications workspace list, scoped to the business
        tabs above. `tab=""` ("All") still scopes to the application
        workspace (never every raw opportunity) -- crm_stage in one of the
        known tabs' stages, OR applied_at is set (belt-and-suspenders for
        any legacy record whose current stage moved outside that set, e.g.
        WATCHED-but-once-applied)."""
        if page < 1:
            raise ValueError("page must be >= 1")
        if page_size < 1:
            raise ValueError("page_size must be >= 1")
        if tab == self.SUBMITTED_TAB:
            where = "WHERE applied_at IS NOT NULL"
            params: tuple = ()
        elif tab:
            stages = self.APPLICATION_TABS.get(tab)
            if not stages:
                raise ValueError(f"Unknown applications tab: {tab!r}. Allowed: {sorted(self.APPLICATION_TABS)}")
            placeholders = ",".join("?" * len(stages))
            where = f"WHERE crm_stage IN ({placeholders})"
            params = stages
        else:
            placeholders = ",".join("?" * len(self.APPLICATION_WORKSPACE_STAGES))
            where = f"WHERE crm_stage IN ({placeholders}) OR applied_at IS NOT NULL"
            params = self.APPLICATION_WORKSPACE_STAGES

        total = self.connection.execute(f"SELECT COUNT(*) FROM application_history {where}", params).fetchone()[0]
        offset = (page - 1) * page_size
        rows = self.connection.execute(
            f"SELECT * FROM application_history {where} "
            f"ORDER BY {self._APPLICATION_RANK_SQL} ASC, crm_stage_updated_at DESC, id DESC "
            "LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
        return {
            "results": [dict(row) for row in rows],
            "total": total, "page": page, "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }

    def latest_employer_response(self, tracker_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM employer_responses WHERE tracker_id = ? ORDER BY id DESC LIMIT 1", (tracker_id,)
        ).fetchone()
        return dict(row) if row else None

    # -- Web App Phase 4: human feedback (post-outcome learning signal) -----
    # Deliberately a SEPARATE append-only structure from `user_decisions`
    # (Phase 2's pre-application Apply/Watch/Reject screening triage): this
    # is a post-outcome reflection signal ("was this worth pursuing,
    # in hindsight"), for later Analytics learning -- never conflated with
    # the earlier screening decision, and never altering intelligence_
    # priority/crm_stage.
    APPLICATION_FEEDBACK_WORTH_PURSUING = frozenset({"YES", "MAYBE", "NO"})
    APPLICATION_FEEDBACK_INTEREST_CHANGE = frozenset({"HIGHER", "SAME", "LOWER"})

    def record_application_feedback(
        self, tracker_id: int, worth_pursuing: str, *, interest_change: str = "", note: str = "", actor: str = "USER",
    ) -> dict:
        if worth_pursuing not in self.APPLICATION_FEEDBACK_WORTH_PURSUING:
            raise ValueError(f"Unknown worth_pursuing value: {worth_pursuing!r}. Allowed: {sorted(self.APPLICATION_FEEDBACK_WORTH_PURSUING)}")
        if interest_change and interest_change not in self.APPLICATION_FEEDBACK_INTEREST_CHANGE:
            raise ValueError(f"Unknown interest_change value: {interest_change!r}. Allowed: {sorted(self.APPLICATION_FEEDBACK_INTEREST_CHANGE)}")
        self._require(tracker_id)
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO application_feedback (tracker_id, worth_pursuing, interest_change, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (tracker_id, worth_pursuing, interest_change, note, now),
        )
        self.connection.commit()
        self.append_event(
            tracker_id, "APPLICATION_FEEDBACK_RECORDED", reason=worth_pursuing,
            evidence_reference=interest_change or "", actor=actor,
        )
        return self._application_feedback_row(cursor.lastrowid)

    def list_application_feedback(self, tracker_id: int) -> list[dict]:
        return [
            dict(row) for row in
            self.connection.execute("SELECT * FROM application_feedback WHERE tracker_id = ? ORDER BY id DESC", (tracker_id,))
        ]

    def get_latest_application_feedback(self, tracker_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM application_feedback WHERE tracker_id = ? ORDER BY id DESC LIMIT 1", (tracker_id,)
        ).fetchone()
        return dict(row) if row else None

    def _application_feedback_row(self, feedback_id: int) -> dict | None:
        row = self.connection.execute("SELECT * FROM application_feedback WHERE id = ?", (feedback_id,)).fetchone()
        return dict(row) if row else None

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
            "user_decisions": self.list_user_decisions(tracker_id),
        }

    # -- Web App Phase 2: user decisions (controlled human signal) ----------
    def record_user_decision(
        self, tracker_id: int, decision: str, *, reason_code: str = "", note: str = "", decided_by: str = "USER",
    ) -> dict:
        """Records a human screening decision (Apply/Watch/Reject) as its
        own append-only fact, separate from the intelligence engine's
        A/B/C/D/E priority -- never writes to intelligence_priority,
        crm_stage, or any scoring column. A changed mind is a new row, the
        same append-only convention `opportunity_events` already uses."""
        if decision not in USER_DECISIONS:
            raise ValueError(f"Unknown user decision: {decision!r}. Allowed: {sorted(USER_DECISIONS)}")
        if reason_code and reason_code not in USER_DECISION_REASON_CODES:
            raise ValueError(f"Unknown decision reason code: {reason_code!r}. Allowed: {sorted(USER_DECISION_REASON_CODES)}")
        self._require(tracker_id)
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO user_decisions (tracker_id, decision, reason_code, note, decided_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tracker_id, decision, reason_code, note, now, now),
        )
        self.connection.commit()
        self.append_event(
            tracker_id, "USER_DECISION_RECORDED", reason=decision,
            evidence_reference=reason_code or (note[:200] if note else ""), actor=decided_by,
        )
        return self._user_decision_row(cursor.lastrowid)

    def get_latest_user_decision(self, tracker_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM user_decisions WHERE tracker_id = ? ORDER BY id DESC LIMIT 1", (tracker_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_user_decisions(self, tracker_id: int) -> list[dict]:
        return [
            dict(row) for row in
            self.connection.execute("SELECT * FROM user_decisions WHERE tracker_id = ? ORDER BY id DESC", (tracker_id,))
        ]

    def _user_decision_row(self, decision_id: int) -> dict | None:
        row = self.connection.execute("SELECT * FROM user_decisions WHERE id = ?", (decision_id,)).fetchone()
        return dict(row) if row else None

    # -- Web App Phase 2: Opportunities workspace read model -----------------
    _PRIORITY_RANK_SQL = "CASE intelligence_priority WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 WHEN 'D' THEN 3 WHEN 'E' THEN 4 ELSE 5 END"

    # Web App Phase 7.1: an "opportunity" here means the raw discovery
    # population -- `applied` == `applied_at IS NOT NULL` (the SAME evidence
    # `cumulative_funnel_counts()`'s APPLIED figure uses), `not_applied` its
    # exact complement. Never a second definition of "applied".
    APPLICATION_STATES = frozenset({"applied", "not_applied"})

    def search_opportunities(
        self, *, search: str = "", intelligence_priority: str = "", crm_stage: str = "",
        market: str = "", work_arrangement: str = "", career_track: str = "", source: str = "",
        min_score: float | None = None, max_score: float | None = None, application_state: str = "",
        page: int = 1, page_size: int = 25,
    ) -> dict:
        """Paginated, filterable, searchable Opportunities workspace list --
        reuses `application_history` exactly as `list_opportunities()` does;
        adds only presentation-layer search/pagination/ordering concerns, no
        new business/scoring logic. Default ordering surfaces actionable,
        higher-value opportunities first (A/B/C/D/E priority rank, then
        career_score) rather than raw tracker id."""
        if page < 1:
            raise ValueError("page must be >= 1")
        if page_size < 1:
            raise ValueError("page_size must be >= 1")
        if application_state and application_state not in self.APPLICATION_STATES:
            raise ValueError(f"Unknown application_state: {application_state!r}. Allowed: {sorted(self.APPLICATION_STATES)}")
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if search:
            clauses.append("(company LIKE :search OR job_title LIKE :search)")
            params["search"] = f"%{search}%"
        if intelligence_priority:
            if intelligence_priority == "UNSCORED":
                clauses.append("(intelligence_priority IS NULL OR intelligence_priority = '')")
            else:
                clauses.append("intelligence_priority = :intelligence_priority")
                params["intelligence_priority"] = intelligence_priority
        for column, value in (
            ("crm_stage", crm_stage), ("market", market), ("work_arrangement", work_arrangement),
            ("career_track", career_track), ("source", source),
        ):
            if value:
                clauses.append(f"{column} = :{column}")
                params[column] = value
        if application_state == "applied":
            clauses.append("applied_at IS NOT NULL")
        elif application_state == "not_applied":
            clauses.append("applied_at IS NULL")
        if min_score is not None:
            clauses.append("career_score >= :min_score")
            params["min_score"] = min_score
        if max_score is not None:
            clauses.append("career_score <= :max_score")
            params["max_score"] = max_score
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        total = self.connection.execute(f"SELECT COUNT(*) FROM application_history {where}", params).fetchone()[0]
        offset = (page - 1) * page_size
        rows = self.connection.execute(
            f"SELECT * FROM application_history {where} "
            f"ORDER BY {self._PRIORITY_RANK_SQL} ASC, career_score DESC, id DESC "
            "LIMIT :limit OFFSET :offset",
            {**params, "limit": page_size, "offset": offset},
        ).fetchall()
        return {
            "results": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }

    def opportunity_filter_options(self) -> dict[str, list[str]]:
        """Distinct real values for the Opportunities workspace's filter
        dropdowns -- never a fabricated/static filter dimension."""
        fields = ("market", "work_arrangement", "career_track", "source", "crm_stage")
        return {field: sorted({row["value"] for row in self.breakdown_by(field) if row["value"]}) for field in fields}

    # -- Web App Phase 5: Employer Inbox -------------------------------------
    # Communication workspace/evidence ONLY -- built entirely on the EXISTING
    # `employer_responses` table GmailOutcomeMonitor already populates (no
    # second email/message table) and the SAME resolution fact
    # `action_required_items()`'s EMPLOYER_ACTION category already computes
    # (`is_employer_response_reviewed`, a public wrapper around the method
    # that category already calls) -- so Employer Inbox and Action Required
    # can never disagree about whether one employer message is resolved.
    # Presentation (labels, the actionable/required-action rule) is left to
    # the caller, matching the same fact/presentation split
    # action_required_items()/`_decorate_action_item` already use.
    def get_employer_response(self, response_id: int) -> dict | None:
        row = self.connection.execute("SELECT * FROM employer_responses WHERE id = ?", (response_id,)).fetchone()
        return dict(row) if row else None

    def is_employer_response_reviewed(self, employer_response_id: int) -> bool:
        return self._is_employer_response_reviewed(employer_response_id)

    def employer_inbox_items(self) -> list[dict]:
        """One row per real, recorded employer message -- never fabricated.
        Each carries its own resolution fact and, for a genuinely UNKNOWN
        message, any human classification already recorded for it."""
        items: list[dict] = []
        for row in self.connection.execute("SELECT * FROM employer_responses ORDER BY id DESC"):
            row = dict(row)
            record = self.get_opportunity(row["tracker_id"])
            if not record:
                continue
            classification = (
                self.get_employer_response_classification(row["id"]) if row["response_type"] == "UNKNOWN" else None
            )
            items.append({
                "employer_response_id": row["id"], "tracker_id": row["tracker_id"],
                "company": record.get("company") or "", "job_title": record.get("job_title") or "",
                "intelligence_priority": record.get("intelligence_priority"), "crm_stage": record.get("crm_stage"),
                "applied_at": record.get("applied_at"),
                "response_type": row["response_type"], "received_at": row["received_at"],
                "summary": row.get("summary") or "", "evidence_reference": row.get("evidence_reference") or "",
                "source": row.get("source") or "",
                "resolved": self.is_employer_response_reviewed(row["id"]),
                "classification": classification,
            })
        return items

    def employer_inbox_summary(self) -> dict:
        """KPI counts -- each reuses an EXISTING evidence-based definition
        where one already exists (`needs_action`/`meaningful_responses`)
        rather than a second, subtly different count that could quietly
        disagree with Action Required or the Dashboard."""
        def count_where_type(response_type: str) -> int:
            return self.connection.execute(
                "SELECT COUNT(*) FROM employer_responses WHERE response_type = ?", (response_type,)
            ).fetchone()[0]

        return {
            "employer_messages": self.connection.execute("SELECT COUNT(*) FROM employer_responses").fetchone()[0],
            "needs_action": self.action_required_counts()["EMPLOYER_ACTION"],
            "meaningful_responses": self.response_quality_counts()["meaningful_responses"],
            "screening_requests": count_where_type("SCREENING_REQUEST"),
            "interviews": count_where_type("INTERVIEW_INVITATION"),
            "assessments": count_where_type("ASSESSMENT_REQUEST"),
            "rejections": count_where_type("REJECTION"),
            "offers": count_where_type("OFFER"),
        }

    def record_employer_response_classification(
        self, tracker_id: int, employer_response_id: int, resolved_type: str, *, note: str = "", actor: str = "USER",
    ) -> dict:
        """Resolves a genuinely UNKNOWN employer message with an explicit
        human judgment call. Append-only and auditable, and NEVER rewrites
        the original `employer_responses` row -- the real Gmail-sourced
        evidence stays exactly as `classify_email()` left it; the human's
        reading is recorded as a SEPARATE, additive fact (the same
        dedicated-table-plus-audit-event pattern `record_user_decision`/
        `record_application_feedback` already use), never as a silent
        mutation of history. Also reuses `mark_employer_response_reviewed`
        so this resolution is immediately consistent with Action Required's
        own EMPLOYER_ACTION category -- never a second, competing "resolved"
        concept. Restricted to a genuinely UNKNOWN message only: reclassifying
        an already-confident classification is out of scope for this
        conservative first cut."""
        if resolved_type not in EMPLOYER_RESPONSE_HUMAN_CLASSIFICATIONS:
            raise ValueError(
                f"Unknown resolved_type: {resolved_type!r}. Allowed: {sorted(EMPLOYER_RESPONSE_HUMAN_CLASSIFICATIONS)}"
            )
        self._require(tracker_id)
        row = self.get_employer_response(employer_response_id)
        if not row or row["tracker_id"] != tracker_id:
            raise ValueError(f"No employer_responses row {employer_response_id} found for tracker {tracker_id}.")
        if row["response_type"] != "UNKNOWN":
            raise ValueError("Human classification is only for a genuinely UNKNOWN employer message.")
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO employer_response_classifications "
            "(tracker_id, employer_response_id, resolved_type, note, classified_at, created_at, actor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tracker_id, employer_response_id, resolved_type, note, now, now, actor),
        )
        self.connection.commit()
        self.append_event(
            tracker_id, "EMPLOYER_RESPONSE_HUMAN_CLASSIFIED", reason=resolved_type,
            evidence_reference=str(employer_response_id), actor=actor,
        )
        note_summary = f"Human-classified as {resolved_type}" + (f": {note}" if note else "")
        self.mark_employer_response_reviewed(tracker_id, employer_response_id, note=note_summary, actor=actor)
        if resolved_type == "INTERVIEW_INVITATION":
            self._maybe_auto_create_interview_from_invitation(
                tracker_id, evidence_reference=str(employer_response_id), summary=row.get("summary") or ""
            )
        return self._employer_response_classification_row(cursor.lastrowid)

    def get_employer_response_classification(self, employer_response_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM employer_response_classifications WHERE employer_response_id = ? ORDER BY id DESC LIMIT 1",
            (employer_response_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_employer_response_classifications(self, tracker_id: int) -> list[dict]:
        return [
            dict(row) for row in self.connection.execute(
                "SELECT * FROM employer_response_classifications WHERE tracker_id = ? ORDER BY id DESC", (tracker_id,)
            )
        ]

    def _employer_response_classification_row(self, classification_id: int) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM employer_response_classifications WHERE id = ?", (classification_id,)
        ).fetchone()
        return dict(row) if row else None

    # -- Web App Phase 6: Interviews workspace read model --------------------
    # The ONE interview-creation path remains `record_interview()`
    # (unchanged, called either automatically -- see
    # `_maybe_auto_create_interview_from_invitation` -- or explicitly). This
    # section only reads what's already there: no second pipeline, no
    # duplicated employer/application data.
    def get_interview(self, interview_id: int) -> dict | None:
        return self._interview_row(interview_id)

    def list_interviews(self, *, tracker_id: int | None = None) -> list[dict]:
        if tracker_id is not None:
            rows = self.connection.execute(
                "SELECT * FROM interviews WHERE tracker_id = ? ORDER BY id DESC", (tracker_id,)
            )
        else:
            rows = self.connection.execute("SELECT * FROM interviews ORDER BY id DESC")
        return [dict(row) for row in rows]

    _INTERVIEW_RECENT_COMPLETION_WINDOW_DAYS = 14

    def _interview_bucket(self, interview: dict) -> str:
        """One of upcoming / needs_time / recently_completed / historical --
        a pure fact about the stored `scheduled_at`/`outcome`/`completed_at`
        values, never a new scoring system (item 4's default ordering)."""
        outcome = interview.get("outcome") or ""
        if outcome == "SCHEDULED" or not outcome:
            return "upcoming" if interview.get("scheduled_at") else "needs_time"
        completed_at = interview.get("completed_at") or ""
        if completed_at:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self._INTERVIEW_RECENT_COMPLETION_WINDOW_DAYS)).isoformat()
            if completed_at >= cutoff:
                return "recently_completed"
        return "historical"

    def interview_register(self) -> list[dict]:
        """Every real interview record, joined with its opportunity, each
        carrying its bucket (see `_interview_bucket`) so the caller can order
        upcoming-by-date, then needing a time confirmed, then recently
        completed, then historical (item 4) without recomputing anything."""
        rows = []
        for interview in self.list_interviews():
            record = self.get_opportunity(interview["tracker_id"])
            if not record:
                continue
            rows.append({
                **interview,
                "company": record.get("company") or "", "job_title": record.get("job_title") or "",
                "intelligence_priority": record.get("intelligence_priority"), "crm_stage": record.get("crm_stage"),
                "bucket": self._interview_bucket(interview),
            })
        bucket_order = {"upcoming": 0, "needs_time": 1, "recently_completed": 2, "historical": 3}
        rows.sort(key=lambda r: r.get("scheduled_at") or r.get("completed_at") or "")
        rows.sort(key=lambda r: bucket_order.get(r["bucket"], 4))
        return rows

    def interviews_summary(self) -> dict:
        """KPI counts -- `offers` reuses `cumulative_funnel_counts()`'s own
        OFFER definition (the `offers` table) so this workspace can never
        disagree with the Dashboard/Applications about what counts as an
        offer."""
        all_interviews = self.list_interviews()
        upcoming = [i for i in all_interviews if self._interview_bucket(i) in ("upcoming", "needs_time")]
        completed = [i for i in all_interviews if self._interview_bucket(i) in ("recently_completed", "historical")]
        final_stage = [i for i in all_interviews if i.get("stage") == "FINAL_INTERVIEW"]
        upcoming_with_date = sorted((i for i in upcoming if i.get("scheduled_at")), key=lambda i: i["scheduled_at"])
        next_interview = upcoming_with_date[0] if upcoming_with_date else None

        def _has_prep_evidence(interview: dict) -> bool:
            record = self.get_opportunity(interview["tracker_id"]) or {}
            return bool(record.get("evaluation_snapshot") or record.get("job_description"))

        return {
            "upcoming": len(upcoming),
            "completed": len(completed),
            "final_stage": len(final_stage),
            "offers": self.cumulative_funnel_counts()["OFFER"],
            "next_interview": next_interview,
            "preparation_ready": sum(1 for i in upcoming if _has_prep_evidence(i)),
        }

    # -- Web App Phase 7: Analytics & Learning governance --------------------
    def record_proposed_learning(
        self, key: str, *, domain: str, title: str, observation: str, proposed_change: str,
        evidence_summary: str, sample_size: int, confidence: str,
        status: str = LEARNING_STATUS_PROPOSED, review_note: str = "", actor: str = "SYSTEM",
    ) -> dict:
        """Creates the ONE persisted row for a deterministic learning
        candidate (identified by its stable `key`), idempotently -- a second
        call with the same key updates its status instead of duplicating it
        (see update_proposed_learning_status). "Accepting" a learning here
        records governance approval ONLY; it never itself rewrites
        intelligence_priority, scoring, or eligibility logic -- there is no
        code path anywhere in this service that reads `proposed_learnings`
        back into scoring."""
        if domain not in LEARNING_DOMAINS:
            raise ValueError(f"Unknown learning domain: {domain!r}. Allowed: {sorted(LEARNING_DOMAINS)}")
        if status not in LEARNING_STATUSES:
            raise ValueError(f"Unknown learning status: {status!r}. Allowed: {sorted(LEARNING_STATUSES)}")
        if confidence not in LEARNING_CONFIDENCE_LEVELS:
            raise ValueError(f"Unknown confidence level: {confidence!r}. Allowed: {sorted(LEARNING_CONFIDENCE_LEVELS)}")
        existing = self.get_proposed_learning_by_key(key)
        if existing:
            if existing["status"] == status:
                return existing  # already recorded at this status -- no redundant history entry
            return self.update_proposed_learning_status(existing["id"], status, review_note=review_note, actor=actor)
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO proposed_learnings "
            "(key, domain, title, observation, proposed_change, evidence_summary, sample_size, confidence, "
            "status, created_at, reviewed_at, review_note, actor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key, domain, title, observation, proposed_change, evidence_summary, sample_size, confidence,
                status, now, now if status != LEARNING_STATUS_PROPOSED else None, review_note, actor,
            ),
        )
        self.connection.commit()
        learning_id = cursor.lastrowid
        self.connection.execute(
            "INSERT INTO proposed_learning_status_history "
            "(proposed_learning_id, previous_status, new_status, note, actor, occurred_at) VALUES (?, ?, ?, ?, ?, ?)",
            (learning_id, None, status, review_note, actor, now),
        )
        self.connection.commit()
        return self._proposed_learning_row(learning_id)

    def update_proposed_learning_status(
        self, learning_id: int, status: str, *, review_note: str = "", actor: str = "USER",
    ) -> dict:
        """The one governance write (item 19): Accept / Need More Evidence /
        Reject, and (programmatically) Retire. Every transition is appended
        to `proposed_learning_status_history` -- a changed mind later is a
        NEW history row, never a silent overwrite of the trail, matching the
        append-only convention every other CRM governance table already
        uses."""
        if status not in LEARNING_STATUSES:
            raise ValueError(f"Unknown learning status: {status!r}. Allowed: {sorted(LEARNING_STATUSES)}")
        row = self._proposed_learning_row(learning_id)
        if not row:
            raise ValueError(f"No proposed learning found with ID {learning_id}.")
        now = _now()
        self.connection.execute(
            "UPDATE proposed_learnings SET status = ?, reviewed_at = ?, review_note = ?, actor = ? WHERE id = ?",
            (status, now, review_note, actor, learning_id),
        )
        self.connection.commit()
        self.connection.execute(
            "INSERT INTO proposed_learning_status_history "
            "(proposed_learning_id, previous_status, new_status, note, actor, occurred_at) VALUES (?, ?, ?, ?, ?, ?)",
            (learning_id, row["status"], status, review_note, actor, now),
        )
        self.connection.commit()
        return self._proposed_learning_row(learning_id)

    def get_proposed_learning_by_key(self, key: str) -> dict | None:
        row = self.connection.execute("SELECT * FROM proposed_learnings WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def list_proposed_learnings(self, *, status: str | None = None) -> list[dict]:
        if status:
            rows = self.connection.execute(
                "SELECT * FROM proposed_learnings WHERE status = ? ORDER BY id DESC", (status,)
            )
        else:
            rows = self.connection.execute("SELECT * FROM proposed_learnings ORDER BY id DESC")
        return [dict(row) for row in rows]

    def list_proposed_learning_status_history(self, learning_id: int) -> list[dict]:
        return [
            dict(row) for row in self.connection.execute(
                "SELECT * FROM proposed_learning_status_history WHERE proposed_learning_id = ? ORDER BY id DESC",
                (learning_id,),
            )
        ]

    def _proposed_learning_row(self, learning_id: int) -> dict | None:
        row = self.connection.execute("SELECT * FROM proposed_learnings WHERE id = ?", (learning_id,)).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self.history.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
