from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

@dataclass
class FinalReviewArtifact:
    review_id: str = field(default_factory=lambda: uuid4().hex)
    tracker_id: int = 0; package_id: str = ""; execution_id: str = ""
    company: str = ""; job_title: str = ""; market: str = ""; career_track: str = ""
    application_url: str = ""; application_portal: str = "UNKNOWN"
    execution_status: str = ""; review_status: str = "NOT_READY"
    fields_detected: int = 0; fields_filled: int = 0; fields_skipped: int = 0; manual_review_fields: int = 0; unknown_required_fields: int = 0
    resume_path: str = ""; resume_uploaded: bool = False; cover_letter_path: str = ""; cover_letter_uploaded: bool = False
    answer_summary: dict[str, str] = field(default_factory=dict)
    legal_confirmations: list[str] = field(default_factory=list); pending_manual_actions: list[str] = field(default_factory=list)
    final_submit_detected: bool = False; final_submit_label: str = ""
    blocking_reasons: list[str] = field(default_factory=list); fingerprint: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat()); updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat()); reviewed_at: str = ""
    audit: list[dict] = field(default_factory=list)
    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls, value): return cls(**value)
