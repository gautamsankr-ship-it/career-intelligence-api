from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

@dataclass
class ApplicationException:
    exception_id: str
    session_id: str
    page_number: int
    page_url: str
    portal: str
    field_label: str
    normalized_concept: str
    exception_type: str
    required: bool
    reason: str
    available_options: list[str] = field(default_factory=list)
    resolution: str = "OPEN"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: str = ""
    def to_dict(self): return asdict(self)

@dataclass
class ApplicationPreparationSession:
    session_id: str
    tracker_id: int | None
    application_url: str
    source_listing_url: str = ""
    portal: str = "UNKNOWN"
    state: str = "CREATED"
    application_date: str = ""
    current_page_number: int = 0
    current_url: str = ""
    page_fingerprints: list[str] = field(default_factory=list)
    pages_processed: int = 0
    fields_detected: int = 0
    fields_filled: int = 0
    fields_skipped: int = 0
    documents_uploaded: int = 0
    navigation_actions: int = 0
    final_review_detected: bool = False
    failure_reason: str = ""
    exceptions: list[ApplicationException] = field(default_factory=list)
    audit: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    def to_dict(self):
        data=asdict(self); return data
    @classmethod
    def from_dict(cls, data):
        data=dict(data); data["exceptions"]=[ApplicationException(**item) for item in data.get("exceptions", [])]; return cls(**data)
