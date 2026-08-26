"""Normalized, non-executing form-inspection records for browser previews."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ApplicationField:
    field_id: str
    portal: str
    label: str
    question_text: str
    field_type: str
    required: bool
    choices: list[str] = field(default_factory=list)
    maxlength: int | None = None
    section: str = ""
    action: str = "REVIEW"
    concept: str = "UNKNOWN"
    answer: Any = None
    confidence: str = "LOW"
    answer_source: str = "MANUAL_REQUIRED"
    reason: str = ""

    def to_dict(self): return asdict(self)


@dataclass
class ApplicationPlan:
    portal: str
    url: str
    tracker_id: int | None
    company: str = ""
    role: str = ""
    market: str = ""
    fields: list[ApplicationField] = field(default_factory=list)
    authentication: str = "NO"
    mfa: str = "NO"
    captcha: str = "NO"
    final_submit_detected: bool = False
    readiness: str = "READY_FOR_FINAL_REVIEW"
    safe_navigation_detected: bool = False
    document_requirements: list[dict[str, Any]] = field(default_factory=list)
    application_submitted: bool = False
    page_purpose: str = "UNKNOWN"
    route: dict[str, Any] = field(default_factory=dict)
    fields_filled: int = 0
    documents_uploaded: int = 0
    pages_navigated: int = 1

    def summary(self) -> dict[str, Any]:
        fills = sum(x.action == "FILL" for x in self.fields)
        reviews = sum(x.action == "REVIEW" for x in self.fields)
        skips = sum(x.action == "SKIP" for x in self.fields)
        actionable = fills + reviews
        return {"fields_detected": len(self.fields), "auto_fillable_fields": fills,
                "manual_review_fields": reviews, "optional_skipped_fields": skips,
                "automation_coverage_percentage": round(100 * fills / actionable, 1) if actionable else 0.0}

    def to_dict(self):
        data = asdict(self); data["summary"] = self.summary(); return data
