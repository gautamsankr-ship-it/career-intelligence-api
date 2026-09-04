"""Task 21.32: typed constants and lightweight records for the Application
CRM / Tracking Module.

The CRM's canonical opportunity record is the EXISTING
`application_history` row (one row per `job_fingerprint`, addressed by its
integer `id` == "tracker_id") -- no second, competing opportunity table is
introduced. `crm_stage` is an additive column on that same row: a richer
superset lifecycle that also covers post-application stages (employer
response, interviews, offer, hire) the legacy `status`/`application_status`
vocabulary was never designed to express. The legacy fields are left
completely unchanged so every existing consumer (CareerAgent,
ApplicationPackageOrchestrator, FinalReviewService, ApplicationSubmission
Service, application_eligibility_policy) keeps working exactly as before.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Lifecycle stage -------------------------------------------------------
# A literal, ordered list -- not a formula -- so every allowed transition is a
# deliberate, inspectable choice (see opportunity_crm_service.ALLOWED_TRANSITIONS).
ACTIVE_FORWARD_ORDER = (
    "DISCOVERED", "VERIFIED", "ELIGIBILITY_REVIEW", "ELIGIBLE", "SCORED",
    "SHORTLISTED", "PREPARED", "READY_FOR_REVIEW", "READY_FOR_HUMAN_SUBMIT",
    "APPLIED", "ACKNOWLEDGED", "RECRUITER_RESPONSE", "SCREENING",
    "INTERVIEW_1", "INTERVIEW_2", "FINAL_INTERVIEW", "OFFER", "ACCEPTED", "HIRED",
)

# Terminal/branch stages. WATCHED is deliberately NOT fully terminal -- a
# Priority-D "kept for future reconsideration" opportunity (see
# career_agent.py) can legitimately resume into the funnel later, unlike a
# genuine rejection/withdrawal/duplicate. FAILED is a pipeline-processing
# failure (distinct from REJECTED, an employer outcome, and from
# INVALID_VACANCY, a vacancy-quality finding) -- added because the legacy
# `status` vocabulary already needs it and stage mapping must not fabricate
# a false stage for a real existing status.
TERMINAL_BRANCHES = (
    "INELIGIBLE", "REJECTED", "WITHDRAWN", "EXPIRED", "DUPLICATE",
    "INVALID_VACANCY", "DECLINED_OFFER", "WATCHED", "FAILED",
)

ALL_STAGES = frozenset(ACTIVE_FORWARD_ORDER) | frozenset(TERMINAL_BRANCHES)

# Every terminal end-state (no outgoing transition, including HIRED itself --
# a successful terminal, not a branch).
TERMINAL_STAGES = frozenset(TERMINAL_BRANCHES) | {"HIRED"}

# Interview-family stages, in order, reused by record_interview().
INTERVIEW_STAGES = ("SCREENING", "INTERVIEW_1", "INTERVIEW_2", "FINAL_INTERVIEW")

# --- Legacy status -> crm_stage migration mapping ---------------------------
# One deliberate, disclosed choice per legacy HISTORY_STATUSES value (see
# application_history_service.py). Never invents a stage the legacy status
# cannot honestly support; the generic "INTERVIEW" bucket (no round
# granularity in the legacy schema) conservatively lands on the earliest
# interview-family stage rather than guessing which round.
LEGACY_STATUS_TO_CRM_STAGE = {
    "DISCOVERED": "DISCOVERED",
    "SCREENED": "SCORED",
    "SKIPPED": "WATCHED",
    "REVIEW": "ELIGIBILITY_REVIEW",
    "ELIGIBLE": "ELIGIBLE",
    "DRAFTED": "PREPARED",
    "SENT": "APPLIED",
    "APPLIED": "APPLIED",
    "INTERVIEW": "SCREENING",
    "OFFER": "OFFER",
    "REJECTED": "REJECTED",
    "WITHDRAWN": "WITHDRAWN",
    "MANUAL_WEB_REQUIRED": "READY_FOR_HUMAN_SUBMIT",
    "REMOTE_INELIGIBLE": "INELIGIBLE",
    "REMOTE_ELIGIBILITY_REVIEW": "ELIGIBILITY_REVIEW",
    "INTELLIGENCE_REJECTED": "INVALID_VACANCY",
    "FAILED": "FAILED",
}


# --- Human blockers ---------------------------------------------------------
# Distinct from `intelligence_priority` (a career-fit/funnel signal) -- a
# human blocker records a concrete reason a *human*, not the pipeline, must
# act before this opportunity can advance.
HUMAN_ANSWER_APPROVAL_REQUIRED = "HUMAN_ANSWER_APPROVAL_REQUIRED"
HUMAN_ELIGIBILITY_REVIEW_REQUIRED = "HUMAN_ELIGIBILITY_REVIEW_REQUIRED"
HUMAN_SALARY_REVIEW_REQUIRED = "HUMAN_SALARY_REVIEW_REQUIRED"
HUMAN_CAPTCHA_REQUIRED = "HUMAN_CAPTCHA_REQUIRED"
HUMAN_MFA_REQUIRED = "HUMAN_MFA_REQUIRED"
READY_FOR_HUMAN_SUBMIT_BLOCKER = "READY_FOR_HUMAN_SUBMIT"
HUMAN_BLOCKER_TYPES = frozenset({
    HUMAN_ANSWER_APPROVAL_REQUIRED, HUMAN_ELIGIBILITY_REVIEW_REQUIRED,
    HUMAN_SALARY_REVIEW_REQUIRED, HUMAN_CAPTCHA_REQUIRED, HUMAN_MFA_REQUIRED,
    READY_FOR_HUMAN_SUBMIT_BLOCKER, "OTHER",
})
BLOCKER_OPEN = "OPEN"
BLOCKER_RESOLVED = "RESOLVED"

# --- Employer responses (schema ready now; Gmail monitoring is future work) -
EMPLOYER_RESPONSE_TYPES = frozenset({
    "ACKNOWLEDGEMENT", "RECRUITER_CONTACT", "SCREENING_REQUEST",
    "INTERVIEW_INVITATION", "ASSESSMENT_REQUEST", "REJECTION", "OFFER", "UNKNOWN",
})

OFFER_PENDING, OFFER_ACCEPTED, OFFER_DECLINED = "PENDING", "ACCEPTED", "DECLINED"


@dataclass(frozen=True)
class OpportunityEvent:
    """One immutable row of the append-only lifecycle audit trail. Never
    updated or deleted once written -- corrections happen via a NEW event,
    same as the rest of this codebase's audit-trail conventions
    (FinalReviewArtifact.audit, SubmissionReceipt.audit)."""
    tracker_id: int
    event_type: str
    previous_stage: str = ""
    new_stage: str = ""
    occurred_at: str = field(default_factory=_now)
    source: str = "SYSTEM"
    reason: str = ""
    evidence_reference: str = ""
    actor: str = ""
    id: int = 0

    def to_dict(self):
        return asdict(self)
