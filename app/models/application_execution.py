"""Safe, compact browser-preparation outcome; it never contains form values."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


@dataclass
class ApplicationExecutionResult:
    execution_id: str = field(default_factory=lambda: uuid4().hex)
    tracker_id: int = 0
    package_id: str = ""
    portal: str = "UNKNOWN"
    application_url: str = ""
    mode: str = "INSPECT_ONLY"
    pages_processed: int = 0
    fields_detected: int = 0
    fields_resolved: int = 0
    fields_filled: int = 0
    fields_skipped: int = 0
    manual_review_fields: int = 0
    unknown_required_fields: int = 0
    resume_uploaded: bool = False
    cover_letter_uploaded: bool = False
    captcha_detected: bool = False
    auth_required: bool = False
    mfa_required: bool = False
    account_creation_required: bool = False
    final_submit_detected: bool = False
    navigation_actions: int = 0
    page_fingerprint: str = ""
    status: str = "FAILED"
    audit: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self): return asdict(self)
