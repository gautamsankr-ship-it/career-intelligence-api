"""Typed, application-facing answer-vault records.

These records deliberately contain no browser or portal behaviour.  They are
the small contract consumed by a future application-form client.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ApplicationAnswer:
    answer_id: str
    concept: str
    value: Any = None
    answer_type: str = "TEXT"
    automation_policy: str = "MANUAL_REVIEW"
    confidence: str = "LOW"
    answer_source: str = "MANUAL_REQUIRED"
    evidence_reference: str = ""
    applicable_markets: list[str] = field(default_factory=list)
    sensitivity: str = "STANDARD"
    status: str = "DRAFT"
    question_patterns: list[str] = field(default_factory=list)
    notes: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApplicationAnswer":
        return cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})


@dataclass
class ApplicationRule:
    rule_id: str
    concept: str
    conditions: dict[str, Any]
    result: Any
    priority: int = 100
    automation_policy: str = "AUTO_FILL_WITH_RULES"
    confidence: str = "HIGH"
    answer_source: str = "APPROVED_RULE"
    status: str = "APPROVED"
    explanation: str = ""
    sensitivity: str = "CONTEXTUAL"
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApplicationRule":
        return cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})


@dataclass
class AnswerDecision:
    concept: str
    answer: Any
    automation_policy: str
    confidence: str
    answer_source: str
    reason: str
    manual_review: bool
    sensitivity: str = "STANDARD"
    evidence_reference: str = ""
    choices_value: Any = None
