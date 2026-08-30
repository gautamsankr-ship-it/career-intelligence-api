"""Task 21.17C: single authoritative A-E eligibility policy shared by
ApplicationPackageOrchestrator and ApplicationExecutionOrchestrator, so the
two gates (package preparation and browser/ATS execution) can never silently
diverge on which Job Intelligence priorities may proceed to an application
action. Neither caller's own status/reason vocabulary is changed by this
module -- it only centralizes the priority-to-permission mapping itself.
"""
from __future__ import annotations

from app.models.job_intelligence import Priority

# Only these two priorities may proceed to package preparation or execution.
ALLOWED_PRIORITIES = {Priority.PRIORITY_APPLY.value, Priority.APPLY.value}

# A recognized, blocking priority maps to its own specific reason code.
BLOCKED_PRIORITY_REASONS = {
    Priority.REJECT.value: "INTELLIGENCE_REJECTED",
    Priority.HUMAN_REVIEW.value: "INTELLIGENCE_HUMAN_REVIEW_REQUIRED",
    Priority.WATCH.value: "INTELLIGENCE_WATCH",
}

# Returned when intelligence_priority was never recorded on the tracker
# record at all (a record predating Task 21.14E, or a malformed/missing
# value). Callers decide for themselves how to treat this: package
# preparation falls back to the legacy decision/remote_eligibility fields for
# such pre-existing records (unchanged behavior); execution -- the closer-to-
# real-action boundary -- never does, and fails closed instead (Task 21.17C).
INTELLIGENCE_PRIORITY_MISSING = "INTELLIGENCE_PRIORITY_MISSING"


def intelligence_priority_gate(record: dict) -> str | None:
    """Returns None when the record's intelligence_priority authorizes
    application preparation/execution to proceed (PRIORITY_APPLY or APPLY).
    Otherwise returns a specific blocking reason code -- including
    INTELLIGENCE_PRIORITY_MISSING when no priority was recorded, and
    INTELLIGENCE_PRIORITY_UNRECOGNIZED for any other unexpected value. Never
    fabricates permission from a missing or unrecognized value."""
    priority = record.get("intelligence_priority")
    if not priority:
        return INTELLIGENCE_PRIORITY_MISSING
    if priority in ALLOWED_PRIORITIES:
        return None
    return BLOCKED_PRIORITY_REASONS.get(priority, "INTELLIGENCE_PRIORITY_UNRECOGNIZED")
