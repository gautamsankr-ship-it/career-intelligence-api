"""Persistent application history and deterministic vacancy fingerprints."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import APPLICATION_HISTORY_DB


HISTORY_STATUSES = {
    "DISCOVERED",
    "SCREENED",
    "SKIPPED",
    "REVIEW",
    "ELIGIBLE",
    "DRAFTED",
    "SENT",
    "APPLIED",
    "INTERVIEW",
    "OFFER",
    "REJECTED",
    "WITHDRAWN",
    "MANUAL_WEB_REQUIRED",
    "REMOTE_INELIGIBLE",
    "REMOTE_ELIGIBILITY_REVIEW",
    # Task 21.14E: a JobIntelligence Priority.REJECT outcome NOT already
    # covered by REMOTE_INELIGIBLE above (i.e. an invalid/stale vacancy or a
    # proven hard requirement gap). Deliberately distinct from the existing
    # post-application "REJECTED" (an employer outcome, grouped with
    # APPLIED/INTERVIEW/OFFER/WITHDRAWN as a completed-lifecycle status) --
    # reusing that value here would conflate "we never applied" with "we
    # applied and were declined", losing a distinction future funnel
    # reporting will want.
    "INTELLIGENCE_REJECTED",
    "FAILED",
}

LIFECYCLE_STATUSES = HISTORY_STATUSES - {"ELIGIBLE", "SENT"}


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _normalize_url(url: str | None) -> str:
    value = (url or "").strip()
    if not value:
        return ""

    parsed = urlsplit(value)
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            query,
            "",
        )
    )


def job_fingerprint(
    *,
    source: str = "",
    external_job_id: str = "",
    job_url: str = "",
    company: str = "",
    job_title: str = "",
    location: str = "",
    description: str = "",
) -> str:
    """Return a stable SHA-256 fingerprint using the preferred identity."""
    normalized_source = _normalize_text(source)
    normalized_external_id = _normalize_text(external_job_id)
    normalized_url = _normalize_url(job_url)

    if normalized_source and normalized_external_id:
        identity = f"source-id|{normalized_source}|{normalized_external_id}"
    elif normalized_url:
        identity = f"url|{normalized_url}"
    else:
        description_hash = hashlib.sha256(
            _normalize_text(description).encode("utf-8")
        ).hexdigest()
        identity = "fallback|{}|{}|{}|{}".format(
            _normalize_text(company),
            _normalize_text(job_title),
            _normalize_text(location),
            description_hash,
        )

    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def fingerprint_for_opportunity(opportunity) -> str:
    metadata = opportunity.metadata or {}
    return job_fingerprint(
        source=opportunity.source,
        external_job_id=opportunity.id or metadata.get("id", ""),
        job_url=opportunity.job_url,
        company=opportunity.company,
        job_title=opportunity.job_title,
        location=opportunity.location,
        description=opportunity.job_description,
    )


class ApplicationHistoryService:
    """Small SQLite source of truth for processed application vacancies."""

    def __init__(self, db_path: str | Path = APPLICATION_HISTORY_DB) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS application_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_fingerprint TEXT NOT NULL UNIQUE,
                source TEXT,
                external_job_id TEXT,
                job_url TEXT,
                company TEXT,
                job_title TEXT,
                location TEXT,
                career_score REAL,
                ats_score REAL,
                decision TEXT,
                application_method TEXT,
                recipient_email TEXT,
                resume_path TEXT,
                cover_letter_path TEXT,
                status TEXT NOT NULL,
                discovered_at TEXT,
                processed_at TEXT,
                sent_at TEXT,
                gmail_message_id TEXT,
                error_message TEXT
            )
            """
        )
        additions = {
            "application_status": "TEXT",
            "application_url": "TEXT",
            "market": "TEXT",
            "work_arrangement": "TEXT",
            "posted_date": "TEXT",
            "screened_at": "TEXT",
            "applied_at": "TEXT",
            "interview_stage": "TEXT",
            "interview_date": "TEXT",
            "follow_up_date": "TEXT",
            "outcome_date": "TEXT",
            "notes": "TEXT",
            "remote_eligibility": "TEXT",
            "remote_eligibility_reason": "TEXT",
            "remote_eligibility_evidence": "TEXT",
            "remote_eligibility_source": "TEXT",
            "remote_eligibility_previous": "TEXT",
            "remote_eligibility_overridden_at": "TEXT",
            "remote_eligibility_override_note": "TEXT",
            "manual_review_action": "TEXT",
            "manual_reviewed_at": "TEXT",
            "manual_review_note": "TEXT",
            "job_description": "TEXT",
            "career_track": "TEXT",
            "opportunity_themes": "TEXT",
            "source_listing_url": "TEXT",
            "application_url_type": "TEXT",
            "application_url_source": "TEXT",
            "application_portal": "TEXT",
            "application_route_confidence": "TEXT",
            "application_route_resolved_at": "TEXT",
            "application_route_status": "TEXT",
            # Task 21.14E: the authoritative JobIntelligence result, persisted
            # so downstream consumers (ApplicationPackageOrchestrator, a
            # future dashboard) can reuse it rather than recomputing a
            # parallel decision. hard_eligibility and application_alignment
            # are deliberately NOT duplicated here -- they already map onto
            # the existing remote_eligibility* and ats_score columns above.
            "intelligence_priority": "TEXT",
            "intelligence_priority_reasons": "TEXT",
            "vacancy_validity": "TEXT",
            "opportunity_value": "TEXT",
            "candidate_competitiveness": "TEXT",
            # Task 21.17D: the two genuinely OpenAI-derived artifacts
            # (job_analysis, employer) from the ONE evaluate_job() call that
            # also produced intelligence_priority above -- persisted as a
            # single JSON blob so ApplicationPackageOrchestrator can
            # deterministically reconstruct the SAME JobEvaluation for
            # document generation (career_decision/ats_result/recruiter are
            # pure local recomputations of these two fields, never persisted
            # themselves) instead of making a second, non-deterministic
            # OpenAI call. Contains no secrets, cookies, session state, or
            # browser data -- only the same structured vacancy/employer
            # analysis fields already used throughout this table.
            "evaluation_snapshot": "TEXT",
            # Task 21.24C: JobIntelligenceService's own, narrow
            # prepare-for-human-review distinction (see
            # job_intelligence_service._package_preparation_gate) --
            # persisted alongside intelligence_priority above so
            # ApplicationPackageOrchestrator can consult it without
            # recomputing/guessing. Empty for every A/B/D/E record and for
            # any C that does not qualify; never changes intelligence_priority
            # itself and is never consulted by execution/FinalReview/
            # submission, which continue to gate purely on intelligence_priority.
            "package_gate": "TEXT",
            "package_gate_reasons": "TEXT",
        }
        existing = {row[1] for row in self.connection.execute("PRAGMA table_info(application_history)")}
        for column, definition in additions.items():
            if column not in existing:
                self.connection.execute(f"ALTER TABLE application_history ADD COLUMN {column} {definition}")
        self.connection.execute(
            "UPDATE application_history SET application_status = status "
            "WHERE application_status IS NULL OR application_status = ''"
        )
        self.connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def claim_job(self, job_fingerprint_value: str, **fields) -> bool:
        """Atomically claim a new vacancy; return False for an existing record."""
        status = fields.pop("status", "ELIGIBLE")
        if status not in HISTORY_STATUSES:
            raise ValueError(f"Unsupported application history status: {status}")

        values = {
            "job_fingerprint": job_fingerprint_value,
            "status": status,
            "discovered_at": fields.pop("discovered_at", self._now()),
            **fields,
        }
        values.setdefault("application_status", status)
        columns = [
            "job_fingerprint",
            "source",
            "external_job_id",
            "job_url",
            "application_url",
            "source_listing_url",
            "application_url_type",
            "application_url_source",
            "application_portal",
            "application_route_confidence",
            "application_route_resolved_at",
            "application_route_status",
            "company",
            "job_title",
            "location",
            "market",
            "work_arrangement",
            "posted_date",
            "job_description",
            "career_track",
            "opportunity_themes",
            "career_score",
            "ats_score",
            "decision",
            "application_method",
            "recipient_email",
            "remote_eligibility",
            "remote_eligibility_reason",
            "remote_eligibility_evidence",
            "remote_eligibility_source",
            "remote_eligibility_previous",
            "remote_eligibility_overridden_at",
            "remote_eligibility_override_note",
            "manual_review_action",
            "manual_reviewed_at",
            "manual_review_note",
            "resume_path",
            "cover_letter_path",
            "status",
            "application_status",
            "discovered_at",
            "processed_at",
            "screened_at",
            "sent_at",
            "applied_at",
            "interview_stage",
            "interview_date",
            "follow_up_date",
            "outcome_date",
            "notes",
            "gmail_message_id",
            "error_message",
        ]
        try:
            self.connection.execute(
                f"INSERT INTO application_history ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + column for column in columns)})",
                {column: values.get(column) for column in columns},
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            self.connection.rollback()
            existing = self.get_record(job_fingerprint_value)
            if existing and existing["status"] == "FAILED":
                self.update_record(
                    job_fingerprint_value,
                    **{
                        key: value
                        for key, value in values.items()
                        if key != "job_fingerprint"
                    },
                )
                return True
            return False

    def record_evaluation(
        self,
        opportunity,
        evaluation,
        status: str,
        *,
        application_method: str | None = None,
        recipient_email: str | None = None,
        remote_eligibility: str | None = None,
        remote_eligibility_reason: str | None = None,
        remote_eligibility_evidence: str | None = None,
    ) -> tuple[str, bool]:
        fingerprint = fingerprint_for_opportunity(opportunity)
        ats_score = (evaluation.ats_result.get("ats_score") or {}).get(
            "overall_score"
        )
        accepted = self.claim_job(
            fingerprint,
            source=opportunity.source,
            external_job_id=opportunity.id or (opportunity.metadata or {}).get("id"),
            job_url=opportunity.job_url,
            company=opportunity.company,
            job_title=opportunity.job_title,
            location=opportunity.location,
            career_score=evaluation.career_decision.overall_score,
            ats_score=ats_score,
            decision=evaluation.screening_decision,
            application_method=application_method,
            recipient_email=recipient_email,
            remote_eligibility=remote_eligibility,
            remote_eligibility_reason=remote_eligibility_reason,
            remote_eligibility_evidence=remote_eligibility_evidence,
            remote_eligibility_source="AUTOMATED" if remote_eligibility else None,
            status=status,
            application_url=getattr(opportunity, "application_url", ""),
            source_listing_url=getattr(opportunity, "source_listing_url", "") or opportunity.job_url,
            application_url_type=getattr(opportunity, "application_url_type", ""),
            application_url_source=getattr(opportunity, "application_url_source", ""),
            application_portal=getattr(opportunity, "application_portal", ""),
            application_route_confidence=getattr(opportunity, "application_route_confidence", ""),
            application_route_resolved_at=getattr(opportunity, "application_route_resolved_at", ""),
            application_route_status=getattr(opportunity, "application_route_status", ""),
            market=getattr(opportunity, "market", ""),
            work_arrangement=getattr(opportunity, "work_arrangement", ""),
            posted_date=getattr(opportunity, "posted_date", ""),
            job_description=getattr(opportunity, "job_description", ""),
            career_track=(getattr(opportunity, "metadata", {}) or {}).get("career_track", "UNKNOWN"),
            opportunity_themes=",".join((getattr(opportunity, "metadata", {}) or {}).get("opportunity_themes", [])),
            processed_at=self._now(),
            screened_at=self._now(),
        )
        return fingerprint, accepted

    def update_record(self, job_fingerprint_value: str, **fields) -> None:
        if "status" in fields and fields["status"] not in HISTORY_STATUSES:
            raise ValueError(f"Unsupported application history status: {fields['status']}")
        if "status" in fields and "application_status" not in fields:
            fields["application_status"] = fields["status"]
        if not fields:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields["job_fingerprint"] = job_fingerprint_value
        self.connection.execute(
            f"UPDATE application_history SET {assignments} "
            "WHERE job_fingerprint = :job_fingerprint",
            fields,
        )
        self.connection.commit()

    def get_record(self, job_fingerprint_value: str):
        row = self.connection.execute(
            "SELECT * FROM application_history WHERE job_fingerprint = ?",
            (job_fingerprint_value,),
        ).fetchone()
        return dict(row) if row else None

    def get_record_by_id(self, record_id: int):
        row = self.connection.execute(
            "SELECT * FROM application_history WHERE id = ?", (record_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_records(self, status: str | None = None):
        query = "SELECT * FROM application_history"
        values: tuple = ()
        if status:
            query += " WHERE application_status = ?"
            values = (status,)
        query += " ORDER BY id DESC"
        return [dict(row) for row in self.connection.execute(query, values).fetchall()]

    def list_ready_records(self):
        """Return current, human-actionable vacancies without changing their
        lifecycle. Task 21.14E: `intelligence_priority` (A/B = ready) is now
        the authoritative gate when present; the previous raw decision/
        remote_eligibility check is kept only as a fallback for records
        persisted before this field existed, and never overrides
        `intelligence_priority` when it's set."""
        terminal = {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "FAILED", "INTELLIGENCE_REJECTED"}
        return [
            record for record in self.list_records()
            if self._is_ready_for_preparation(record)
            and record.get("application_method") in {"EMAIL", "WEB"}
            and (record.get("application_status") or record.get("status")) not in terminal
        ]

    @staticmethod
    def _is_ready_for_preparation(record) -> bool:
        priority = record.get("intelligence_priority")
        if priority:
            return priority in ("A", "B")
        # Legacy fallback for records predating Task 21.14E's
        # intelligence_priority field. NOT_APPLICABLE is included alongside
        # ELIGIBLE (Task 21.14B: a non-remote vacancy is a "no blocker"
        # state, not a rejection) for consistency with the same fix already
        # applied to ApplicationPackageOrchestrator._eligibility_reason().
        return record.get("decision") == "AUTO_APPLY" and record.get("remote_eligibility") in ("ELIGIBLE", "NOT_APPLICABLE")

    def set_manual_eligibility(self, record_id: int, eligibility: str, note: str) -> dict:
        """Persist an explicit human eligibility decision without rescoring."""
        if eligibility not in {"ELIGIBLE", "INELIGIBLE", "MANUAL_REVIEW"}:
            raise ValueError(f"Unsupported eligibility decision: {eligibility}")
        if not note or not note.strip():
            raise ValueError("A manual eligibility decision requires --note.")
        record = self.get_record_by_id(record_id)
        if not record:
            raise ValueError(f"No tracked vacancy found with ID {record_id}.")
        if (record.get("application_status") or record.get("status")) in {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "FAILED"}:
            raise ValueError("Cannot change remote eligibility for a completed application lifecycle record.")
        fields = {
            "remote_eligibility_previous": record.get("remote_eligibility"),
            "remote_eligibility": eligibility,
            "remote_eligibility_reason": note.strip(),
            "remote_eligibility_evidence": "manual decision",
            "remote_eligibility_source": "MANUAL",
            "remote_eligibility_override_note": note.strip(),
            "remote_eligibility_overridden_at": self._now(),
            "processed_at": self._now(),
        }
        if record.get("decision") == "AUTO_APPLY":
            if eligibility == "INELIGIBLE":
                fields.update(status="REMOTE_INELIGIBLE", application_status="REMOTE_INELIGIBLE")
            elif eligibility == "MANUAL_REVIEW":
                fields.update(status="REMOTE_ELIGIBILITY_REVIEW", application_status="REMOTE_ELIGIBILITY_REVIEW")
            elif record.get("application_url") or record.get("job_url"):
                # A human has cleared eligibility; retain the safe, manual WEB
                # route rather than generating documents or emailing.
                fields.update(status="MANUAL_WEB_REQUIRED", application_status="MANUAL_WEB_REQUIRED", application_method="WEB")
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields["id"] = record_id
        self.connection.execute(f"UPDATE application_history SET {assignments} WHERE id = :id", fields)
        self.connection.commit()
        return self.get_record_by_id(record_id)

    def set_manual_review_action(self, record_id: int, action: str, note: str | None = None) -> dict:
        """Record a human choice while retaining the original REVIEW decision."""
        action = action.upper()
        if action not in {"PROCEED", "SKIP"}:
            raise ValueError("Review action must be PROCEED or SKIP.")
        record = self.get_record_by_id(record_id)
        if not record:
            raise ValueError(f"No tracked vacancy found with ID {record_id}.")
        if record.get("decision") != "REVIEW":
            raise ValueError("Manual review actions are available only for CareerDecision REVIEW records.")
        fields = {
            "manual_review_action": action,
            "manual_reviewed_at": self._now(),
            "manual_review_note": note or "",
            "processed_at": self._now(),
        }
        if action == "SKIP":
            fields.update(status="SKIPPED", application_status="SKIPPED")
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields["id"] = record_id
        self.connection.execute(f"UPDATE application_history SET {assignments} WHERE id = :id", fields)
        self.connection.commit()
        return self.get_record_by_id(record_id)

    def backfill_remote_eligibility(self, classifier) -> dict[str, int]:
        """Classify only missing eligibility metadata; never re-screen or apply."""
        result = {"classified": 0, "already_classified": 0, "insufficient_evidence": 0}
        for record in self.list_records():
            if record.get("remote_eligibility"):
                result["already_classified"] += 1
                continue
            description = record.get("job_description") or ""
            arrangement = record.get("work_arrangement") or ""
            if not description or arrangement.upper() != "REMOTE":
                result["insufficient_evidence"] += 1
                continue
            from types import SimpleNamespace

            eligibility = classifier.classify(SimpleNamespace(
                job_title=record.get("job_title") or "",
                job_description=description,
                work_arrangement=arrangement,
                remote_status=True,
            ))
            if eligibility.decision == "NOT_APPLICABLE":
                result["insufficient_evidence"] += 1
                continue
            self.connection.execute(
                "UPDATE application_history SET remote_eligibility = ?, "
                "remote_eligibility_reason = ?, remote_eligibility_evidence = ? WHERE id = ?",
                (eligibility.decision, eligibility.reason, eligibility.evidence, record["id"]),
            )
            result["classified"] += 1
        self.connection.commit()
        return result

    def update_lifecycle(self, record_id: int, application_status: str, **fields):
        if application_status not in LIFECYCLE_STATUSES:
            raise ValueError(f"Unsupported lifecycle status: {application_status}")
        record = self.get_record_by_id(record_id)
        if not record:
            raise ValueError(f"No tracked vacancy found with ID {record_id}.")
        current = record.get("application_status") or record.get("status")
        allowed = {
            "MANUAL_WEB_REQUIRED": {"APPLIED", "WITHDRAWN"},
            "DRAFTED": {"APPLIED", "WITHDRAWN"},
            "APPLIED": {"APPLIED", "INTERVIEW", "REJECTED", "WITHDRAWN"},
            "INTERVIEW": {"INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN"},
        }
        if current != application_status and current in allowed and application_status not in allowed[current]:
            raise ValueError(f"Cannot change {current} to {application_status} without a manual correction.")
        if current not in allowed and current != application_status:
            raise ValueError(f"Cannot change {current} to {application_status}.")
        now = self._now()
        if application_status == "APPLIED":
            fields.setdefault("applied_at", now)
        if application_status in {"OFFER", "REJECTED", "WITHDRAWN"}:
            fields.setdefault("outcome_date", now)
        fields.update(status=application_status, application_status=application_status, processed_at=now)
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        fields["id"] = record_id
        self.connection.execute(f"UPDATE application_history SET {assignments} WHERE id = :id", fields)
        self.connection.commit()
        return self.get_record_by_id(record_id)

    def is_duplicate(self, job_fingerprint_value: str) -> bool:
        record = self.get_record(job_fingerprint_value)
        return bool(record and record["status"] != "FAILED")

    def duplicate_record_for_opportunity(self, opportunity):
        """Find an existing non-retryable record without weakening IDs.

        The normal source-ID fingerprint remains authoritative.  This small
        additional check is only for a confidently shared job/application URL
        when the same vacancy is rediscovered from an employer ATS instead of
        LinkedIn or Indeed.
        """
        direct = self.get_record(fingerprint_for_opportunity(opportunity))
        if direct and direct["status"] != "FAILED":
            return direct
        application_url = getattr(opportunity, "application_url", "")
        if not application_url:
            return None
        parsed = urlsplit(application_url)
        canonical_application_url = urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
        )
        if not canonical_application_url:
            return None
        rows = self.connection.execute(
            "SELECT * FROM application_history WHERE status != 'FAILED'"
        ).fetchall()
        for row in rows:
            record = dict(row)
            # Do not collapse two distinct vacancies from the same board just
            # because that board uses a generic apply URL in test or source
            # data. This fallback is deliberately cross-source only.
            if (record.get("source") or "").casefold() == (getattr(opportunity, "source", "") or "").casefold():
                continue
            existing_application_url = record.get("application_url") or ""
            if not existing_application_url:
                continue
            existing = urlsplit(existing_application_url)
            canonical_existing = urlunsplit(
                (existing.scheme.lower(), existing.netloc.lower(), existing.path.rstrip("/"), "", "")
            )
            if canonical_application_url == canonical_existing:
                return record
        return None

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
