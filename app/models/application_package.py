"""Portable, preparation-only handoff for a tracked production vacancy."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class ApplicationPackage:
    package_id: str
    tracker_id: int
    company: str = ""
    job_title: str = ""
    market: str = ""
    career_track: str = ""
    career_score: float | None = None
    ats_score: float | None = None
    application_url: str = ""
    application_portal: str = "UNKNOWN"
    route_confidence: str = "UNKNOWN"
    resume_path: str = ""
    resume_pdf_path: str = ""
    resume_status: str = "DOCUMENT_NOT_READY"
    resume_generated_at: str = ""
    resume_vacancy_identity: str = ""
    cover_letter_path: str = ""
    cover_letter_status: str = "NOT_NEEDED"
    answer_vault_status: str = "ANSWER_VAULT_READY"
    answer_counts: dict[str, int] = field(default_factory=dict)
    manual_answer_count: int = 0
    portal_capability: str = "ROUTE_UNRESOLVED"
    application_method: str = ""
    readiness: str = "NOT_APPLICATION_ELIGIBLE"
    blocking_reasons: list[str] = field(default_factory=list)
    vacancy_identity: str = ""
    # Task 21.17D: diagnostic-only record of how the resume/cover-letter
    # documents (if generated this call) were produced -- "PERSISTED_SNAPSHOT"
    # (deterministic, no new OpenAI call), "FRESH_EVALUATION_FALLBACK" (a
    # legacy record with no persisted snapshot, or one that failed to parse),
    # or "" when no document generation was attempted this call at all
    # (e.g. an already-ready resume was reused). Never affects readiness/
    # eligibility -- purely observability.
    evaluation_source: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, value): return cls(**value)
