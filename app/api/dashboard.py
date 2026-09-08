"""Task 21.33: minimal operational dashboard over the Application CRM
(Task 21.32's OpportunityCRMService). No new tracking database, no
duplicated business logic -- every number and every row comes straight from
the CRM's own read-model methods.

Launch: `python dashboard.py` from the repo root -> http://127.0.0.1:8000
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.models.application_package import ApplicationPackage
from app.services import analytics_service
from app.services import interview_briefing_service as briefing_service
from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_eligibility_policy import intelligence_priority_gate
from app.services.application_package_orchestrator import PACKAGE_DIR
from app.services.master_profile_service import MasterProfileService
from app.services.opportunity_crm_service import (
    EMPLOYER_RESPONSE_HUMAN_CLASSIFICATIONS,
    LEARNING_STATUS_ACCEPTED,
    LEARNING_STATUS_NEED_MORE_EVIDENCE,
    LEARNING_STATUS_PROPOSED,
    LEARNING_STATUS_REJECTED,
    OpportunityCRMService,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

app = FastAPI(title="Career Intelligence CRM Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates" / "dashboard"))


def _humanize(value: str | None) -> str:
    """Presentation-only rendering of a raw, database-style enum value
    (e.g. "united_states", "VERY_STRONG", "REMOTE") as plain, readable text
    ("United States", "Very Strong", "Remote"). Never touches the stored
    value itself -- callers still filter/persist on the raw string; this is
    purely a Jinja display filter."""
    if not value:
        return "-"
    return value.replace("_", " ").title()


templates.env.filters["humanize"] = _humanize

_PRIORITY_LABELS = {
    "A": "Priority Apply", "B": "Apply", "C": "Human Review", "D": "Watch", "E": "Reject", "UNSCORED": "Not Yet Evaluated",
}
_PRIORITY_ORDER = ("A", "B", "C", "D", "E", "UNSCORED")

# Web App Phase 7.1 (item 13): business-readable KPI definitions, surfaced
# as a plain HTML `title` tooltip on the card itself -- the smallest
# possible "audit detail" affordance, never a SQL/implementation dump.
_KPI_DEFINITIONS = {
    "opportunities_discovered": "Every opportunity ever discovered by the pipeline, regardless of current status.",
    "applications_submitted": "Distinct opportunities with confirmed submission evidence (CRM submission confirmation) -- never a prepared, ready, or attempted-only package.",
    "acknowledged": "Distinct submitted applications with at least one automated employer acknowledgement recorded -- an automated receipt, not a meaningful response.",
    "meaningful_responses": "Distinct submitted applications with at least one genuinely meaningful employer/recruiter response (never an automated acknowledgement).",
    "interviews": "Distinct submitted applications with at least one recorded interview.",
    "offers": "Distinct submitted applications with at least one recorded offer.",
    "hired": "Distinct submitted applications resulting in a recorded hire.",
    "application_rate": "Applications Submitted / Opportunities Discovered.",
    "meaningful_response_rate": "Applications with a meaningful response / Applications Submitted.",
    "interview_rate": "Applications reaching interview / Applications Submitted.",
    "offer_rate": "Applications reaching offer / Applications Submitted.",
    "hire_rate": "Applications resulting in hire / Applications Submitted.",
}

# Every PIPELINE_GROUPS stage relabeled to its business-facing group -- reused
# for the Opportunities workspace's "Current Status" column so it never shows
# a raw internal crm_stage code either.
_STAGE_TO_GROUP_LABEL = {stage: label for label, stages in OpportunityCRMService.PIPELINE_GROUPS for stage in stages}

# -- Web App Phase 2: Eligibility Matrix (reuses existing data/logic only) --
_REMOTE_ELIGIBILITY_BADGE = {"ELIGIBLE": "PASS", "NOT_APPLICABLE": "PASS", "MANUAL_REVIEW": "REVIEW", "INELIGIBLE": "FAIL"}
_REMOTE_ELIGIBILITY_PLAIN = {
    "ELIGIBLE": "Eligible to work remotely in this role's location(s).",
    "NOT_APPLICABLE": "Not a location-restricted remote vacancy.",
    "MANUAL_REVIEW": "Remote vacancy is silent on overseas eligibility -- needs your review, not an automatic pass or fail.",
    "INELIGIBLE": "An explicit work-right/residency restriction rules this out.",
}
_GATE_BADGE = {
    None: "PASS", "INTELLIGENCE_PRIORITY_MISSING": "UNKNOWN", "INTELLIGENCE_HUMAN_REVIEW_REQUIRED": "REVIEW",
    "INTELLIGENCE_WATCH": "REVIEW", "INTELLIGENCE_REJECTED": "FAIL", "INTELLIGENCE_PRIORITY_UNRECOGNIZED": "UNKNOWN",
}
_GATE_PLAIN = {
    None: "Cleared for automated application preparation.",
    "INTELLIGENCE_PRIORITY_MISSING": "Not yet evaluated by the intelligence engine.",
    "INTELLIGENCE_HUMAN_REVIEW_REQUIRED": "Flagged for human review before proceeding.",
    "INTELLIGENCE_WATCH": "Deprioritized -- kept for possible future reconsideration.",
    "INTELLIGENCE_REJECTED": "Rejected by the intelligence engine.",
    "INTELLIGENCE_PRIORITY_UNRECOGNIZED": "Priority value not recognized.",
}
_VALIDITY_BADGE = {"VERIFIED": "PASS", "LIKELY_VALID": "PASS", "UNCERTAIN": "REVIEW", "STALE": "FAIL", "INVALID": "FAIL"}
_VALIDITY_PLAIN = {
    "VERIFIED": "Vacancy verified as genuine and current.",
    "LIKELY_VALID": "Vacancy appears genuine and current.",
    "UNCERTAIN": "Vacancy validity could not be confirmed.",
    "STALE": "Vacancy appears stale/outdated.",
    "INVALID": "Vacancy appears invalid or a duplicate.",
}
_RECOMMENDATION_TEXT = {
    "A": "Priority Apply", "B": "Apply", "C": "Human Review", "D": "Watch", "E": "Not Recommended",
}
_DECISION_REASON_LABELS = {
    "SALARY_TOO_LOW": "Salary too low", "TOO_JUNIOR": "Too junior", "COMPANY_UNATTRACTIVE": "Company unattractive",
    "LOCATION": "Location", "CAREER_VALUE": "Career value", "NOT_GENUINELY_REMOTE": "Not genuinely remote",
    "ELIGIBILITY_WORK_RIGHT_CONCERN": "Eligibility/work-right concern", "OTHER": "Other",
}


def _build_eligibility_matrix(record: dict) -> list[dict]:
    """Reuses existing, unchanged eligibility data/logic only -- never a new
    business rule. UNKNOWN is always distinct from FAIL: a remote vacancy
    silent on overseas eligibility (MANUAL_REVIEW) is REVIEW, never FAIL."""
    remote_eligibility = record.get("remote_eligibility")
    gate_reason = intelligence_priority_gate(record)
    validity = record.get("vacancy_validity")
    return [
        {
            "criterion": "Geographic / Work Authorization",
            "status": _REMOTE_ELIGIBILITY_BADGE.get(remote_eligibility, "UNKNOWN"),
            "explanation": _REMOTE_ELIGIBILITY_PLAIN.get(remote_eligibility, "Not yet assessed."),
        },
        {
            "criterion": "Overall Application Eligibility",
            "status": _GATE_BADGE.get(gate_reason, "UNKNOWN"),
            "explanation": _GATE_PLAIN.get(gate_reason, "Not yet assessed."),
        },
        {
            "criterion": "Vacancy Validity",
            "status": _VALIDITY_BADGE.get(validity, "UNKNOWN"),
            "explanation": _VALIDITY_PLAIN.get(validity, "Not yet assessed."),
        },
    ]


def _build_why_pursue(record: dict) -> list[str]:
    """Plain-language restatement of EXISTING structured dimension values
    only -- never a speculative or generated reason."""
    reasons = []
    if record.get("opportunity_value") in ("HIGH", "MEDIUM"):
        reasons.append(f"Opportunity value assessed as {_humanize(record['opportunity_value'])}.")
    if record.get("candidate_competitiveness") in ("VERY_STRONG", "STRONG", "COMPETITIVE"):
        reasons.append(f"Candidate competitiveness assessed as {_humanize(record['candidate_competitiveness'])}.")
    if record.get("vacancy_validity") in ("VERIFIED", "LIKELY_VALID"):
        reasons.append("Vacancy appears genuine and current.")
    if record.get("intelligence_priority") in ("A", "B"):
        reasons.append("Cleared by the intelligence engine for automated application.")
    return reasons


def _build_risks(record: dict) -> list[str]:
    """Plain-language restatement of EXISTING structured dimension values
    only -- never a speculative or generated risk."""
    risks = []
    remote_eligibility = record.get("remote_eligibility")
    if remote_eligibility in (None, "", "MANUAL_REVIEW"):
        risks.append("Geographic/work-authorization eligibility is not yet confirmed -- needs human review.")
    elif remote_eligibility == "INELIGIBLE":
        risks.append("An explicit work-right/residency restriction applies.")
    if record.get("vacancy_validity") == "UNCERTAIN":
        risks.append("Vacancy validity could not be confirmed.")
    if record.get("candidate_competitiveness") in ("STRETCH", "INSUFFICIENT_DATA", "LOW"):
        risks.append(f"Candidate competitiveness assessed as {_humanize(record['candidate_competitiveness'])}.")
    if record.get("intelligence_priority") == "C":
        risks.append("Flagged by the intelligence engine for human review before proceeding.")
    if record.get("intelligence_priority") == "E":
        risks.append("Rejected by the intelligence engine.")
    return risks


# -- Web App Phase 3: Action Required presentation (all derived, nothing
# persisted here) --------------------------------------------------------
_ACTION_CATEGORY_LABELS = {
    "REVIEW_AND_SUBMIT": "Review & Submit",
    "ANSWER_REQUIRED": "Answer Required",
    "ELIGIBILITY_DECISION": "Eligibility Decision",
    "BROWSER_ACTION": "Browser Action Required",
    "EMPLOYER_ACTION": "Employer Action",
}
_ACTION_CATEGORY_PRIMARY_ACTION = {
    "REVIEW_AND_SUBMIT": "Review Application",
    "ANSWER_REQUIRED": "Answer Question",
    "ELIGIBILITY_DECISION": "Review Eligibility",
    "BROWSER_ACTION": "Open Browser & Continue",
    "EMPLOYER_ACTION": "Review Message",
}
_EMPLOYER_RESPONSE_PLAIN = {
    "ACKNOWLEDGEMENT": "Acknowledgement (automated)",
    "RECRUITER_CONTACT": "Recruiter reached out",
    "SCREENING_REQUEST": "Screening call requested",
    "INTERVIEW_INVITATION": "Interview invitation received",
    "ASSESSMENT_REQUEST": "Assessment requested",
    "REJECTION": "Rejection",
    "OFFER": "Offer received",
    "UNKNOWN": "Employer sent a message that needs your review",
}
# Web App Phase 5: the exact human-classification options item 6 specifies,
# in the CRM's own `employer_responses.response_type` vocabulary (never a
# second, parallel enum) plus one catch-all outside that vocabulary
# (OTHER_NO_ACTION, see EMPLOYER_RESPONSE_HUMAN_CLASSIFICATIONS) for "reviewed,
# needs no action, doesn't fit a specific category."
_CLASSIFICATION_RESOLUTION_LABELS = {
    "ACKNOWLEDGEMENT": "Acknowledgement",
    "RECRUITER_CONTACT": "Recruiter Response",
    "SCREENING_REQUEST": "Screening Request",
    "INTERVIEW_INVITATION": "Interview Invitation",
    "ASSESSMENT_REQUEST": "Assessment Request",
    "REJECTION": "Rejection",
    "OFFER": "Offer",
    "OTHER_NO_ACTION": "Other / No Action",
}
_URGENCY_RANK = {"Critical": 0, "High": 1, "Normal": 2}
# Grouping is for navigation/triage only (Phase 3 section 4): a large,
# homogeneous set of identical eligibility questions is collapsed into one
# expandable row rather than rendered as 100+ separate cards -- never a
# bulk decision, since each remains individually vacancy-specific. Review &
# Submit items are never auto-grouped: each is a distinct, already-ready
# application with its own company/job title, not a repeated homogeneous
# question, and the production count (10) is small enough to review
# individually.
_GROUPING_THRESHOLD = 15
_GROUPABLE_CATEGORIES = {"ELIGIBILITY_DECISION", "ANSWER_REQUIRED"}
# Phase 3.1: display order only (the active queue is organized into one
# section per category, most-actionable first) -- distinct from
# OpportunityCRMService.ACTION_CATEGORIES, whose own order is never changed.
_ACTION_CATEGORY_DISPLAY_ORDER = ("REVIEW_AND_SUBMIT", "ELIGIBILITY_DECISION", "ANSWER_REQUIRED", "BROWSER_ACTION", "EMPLOYER_ACTION")
# Beyond this many individually-rendered rows, a category's active section
# shows only the first N and tucks the rest behind a native <details>
# "View all" disclosure -- the full HTML is still in the response (nothing
# is dropped), only the default-collapsed rendering is compact. Grouped
# rows (see _GROUPABLE_CATEGORIES) already collapse via their own group
# card and are unaffected by this.
_SECTION_PREVIEW_COUNT = 3


def _action_urgency(item: dict) -> str:
    """Presentation-only urgency (Critical/High/Normal), derived purely
    from the action's own category/response-type -- never a new scoring
    system, and never invented from an unknown/absent deadline."""
    category = item["category"]
    if category == "EMPLOYER_ACTION":
        return "Critical" if item.get("response_type") in ("INTERVIEW_INVITATION", "OFFER") else "High"
    if category in ("BROWSER_ACTION", "REVIEW_AND_SUBMIT"):
        return "High"
    return "Normal"  # ANSWER_REQUIRED / ELIGIBILITY_DECISION: non-time-sensitive by default


def _action_primary_label(item: dict) -> str:
    if item["category"] == "EMPLOYER_ACTION" and item.get("response_type") == "INTERVIEW_INVITATION":
        return "Prepare for Interview"
    return _ACTION_CATEGORY_PRIMARY_ACTION.get(item["category"], "Review")


def _action_plain_reason(item: dict) -> str:
    """Translate one action item's raw stored reason into plain business
    language. Reuses OpportunityCRMService's OWN blocker-type vocabulary
    for BLOCKER-sourced items (never a second, divergent translation table)
    and a small, Phase-3-specific mapping for employer responses. Phase 3.1
    housekeeping (item 17): any leading internal reason code (e.g.
    "DIRECT_APPLICATION_ROUTE_NOT_VERIFIED: ...") is humanized here too --
    presentation only, never touching the stored reason or classification."""
    if item["source"] == "BLOCKER" and item.get("blocker_type"):
        label = OpportunityCRMService._BLOCKER_PLAIN_LANGUAGE.get(item["blocker_type"])
        if label:
            reason = f"{label}: {item['reason']}" if item.get("reason") and item["reason"] != item["blocker_type"] else label
            return _humanize_action_reason_prefix(reason)
    if item["source"] == "EMPLOYER_RESPONSE":
        label = _EMPLOYER_RESPONSE_PLAIN.get(item.get("response_type"), "Employer message received")
        detail = item.get("reason") or ""
        reason = f"{label}: {detail}" if detail and detail != item.get("response_type") else label
        return _humanize_action_reason_prefix(reason)
    return _humanize_action_reason_prefix(item.get("reason") or "")


def _decorate_action_item(item: dict) -> dict:
    return {
        **item,
        "category_label": _ACTION_CATEGORY_LABELS[item["category"]],
        "urgency": _action_urgency(item),
        "primary_action": _action_primary_label(item),
        "plain_reason": _action_plain_reason(item),
    }


def _group_action_items(items: list[dict]) -> list[dict]:
    """Groups items sharing the same (category, plain_reason) when the
    group is large -- a navigation/triage aid only. A single vacancy-
    specific decision is still required per opportunity inside the group;
    no bulk action is ever offered for a grouped row."""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in items:
        buckets[(item["category"], item["plain_reason"])].append(item)

    rows: list[dict] = []
    for (category, reason), bucket in buckets.items():
        if category in _GROUPABLE_CATEGORIES and len(bucket) >= _GROUPING_THRESHOLD:
            rows.append({
                "is_group": True, "category": category, "category_label": _ACTION_CATEGORY_LABELS[category],
                "plain_reason": reason, "urgency": bucket[0]["urgency"], "opportunities": bucket, "count": len(bucket),
                "arose_at": max(i.get("arose_at") or "" for i in bucket),
            })
        else:
            rows.extend({"is_group": False, **item} for item in bucket)

    # Stable two-pass sort: most-recent-first within an urgency band, then
    # urgency ascending (Critical first) -- Python's sort is stable, so the
    # recency ordering from the first pass survives within each band.
    rows.sort(key=lambda row: row.get("arose_at") or "", reverse=True)
    rows.sort(key=lambda row: _URGENCY_RANK.get(row["urgency"], 3))
    return rows


def _dashboard_attention_items(service: OpportunityCRMService, *, priority: str = "", limit: int | None = None):
    """Rebuilds the Dashboard's "Needs My Attention" widget from the SAME
    Action Required read model /action-required uses -- replacing the
    Phase 1 `needs_attention()`/`ATTENTION_STAGES`-membership count, which
    the Phase 3 production audit found conflated "the intelligence engine
    may eventually want a look" (bare crm_stage membership, 127 items, ZERO
    backed by an actual open blocker) with "a concrete action is ready
    right now" (see OpportunityCRMService.action_required_items's own
    docstring for the full audit). Returns (rows, total, priority_distribution)."""
    all_items = service.action_required_items()
    distribution: dict[str, int] = {}
    for item in all_items:
        key = item.get("intelligence_priority") or "UNSCORED"
        distribution[key] = distribution.get(key, 0) + 1

    filtered = [i for i in all_items if (i.get("intelligence_priority") or "UNSCORED") == priority] if priority else all_items
    decorated = [_decorate_action_item(item) for item in filtered]
    decorated.sort(key=lambda item: item.get("arose_at") or "", reverse=True)
    decorated.sort(key=lambda item: _URGENCY_RANK.get(item["urgency"], 3))
    rows = [
        {
            "tracker_id": item["tracker_id"], "company": item["company"], "job_title": item["job_title"],
            "intelligence_priority": item.get("intelligence_priority"), "plain_reasons": [item["plain_reason"]],
        }
        for item in decorated
    ]
    return (rows[:limit] if limit else rows), len(all_items), distribution


# Phase 1 web app: navigation placeholders for every approved sidebar section
# beyond the Executive Dashboard and (Phase 2) Opportunities. Each renders
# the shared shell with a short, honest "coming later" message -- no
# fabricated functionality.
_PLACEHOLDER_PAGES = {
    "/automation": (
        "automation", "Automation",
        "Automation controls are coming in a later phase. Run the pipeline today with: "
        "python career_intelligence.py run",
    ),
    "/settings": (
        "settings", "Settings",
        "Settings are coming in a later phase.",
    ),
}


def get_crm_service():
    service = OpportunityCRMService()
    try:
        yield service
    finally:
        service.close()


def _safe_json_list(raw) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [str(raw)]
    return value if isinstance(value, list) else [str(value)]


_RECENT_ACTIVITY_BUSINESS_EVENT_TYPES = (
    "STAGE_TRANSITION", "EMPLOYER_RESPONSE_RECORDED", "INTERVIEW_RECORDED",
    "OFFER_RECORDED", "OFFER_DECISION_RECORDED", "HIRE_RECORDED",
    "BLOCKER_CREATED", "BLOCKER_RESOLVED",
)
# Only stages with NO dedicated recording event of their own (see below) --
# ACKNOWLEDGED/RECRUITER_RESPONSE/SCREENING/INTERVIEW_*/OFFER/ACCEPTED/HIRED
# are each ALSO recorded via their own domain event (EMPLOYER_RESPONSE_
# RECORDED/INTERVIEW_RECORDED/OFFER_RECORDED/OFFER_DECISION_RECORDED/
# HIRE_RECORDED) in the SAME call that fires their STAGE_TRANSITION event --
# including them here would show the same real-world milestone twice.
_RECENT_ACTIVITY_PLAIN_LANGUAGE = {
    "SHORTLISTED": "Opportunity shortlisted",
    "PREPARED": "Application prepared",
    "APPLIED": "Application submitted",
    "REJECTED": "Application rejected",
}


def _describe_activity_event(event: dict) -> str | None:
    """Plain-business-language label for one recent-activity event, or None
    to skip a purely-technical or duplicate transition -- the executive
    view must never show a raw CRM stage code, and never the same
    real-world milestone twice from its two underlying CRM events."""
    event_type = event.get("event_type")
    if event_type == "STAGE_TRANSITION":
        return _RECENT_ACTIVITY_PLAIN_LANGUAGE.get(event.get("new_stage"))
    if event_type == "EMPLOYER_RESPONSE_RECORDED":
        reason = event.get("reason") or ""
        if reason == "ACKNOWLEDGEMENT":
            return "Acknowledgement received"
        if reason in {"RECRUITER_CONTACT", "SCREENING_REQUEST", "INTERVIEW_INVITATION", "ASSESSMENT_REQUEST"}:
            return "Recruiter response received"
        if reason == "REJECTION":
            return "Rejection received"
        if reason == "OFFER":
            return "Offer received"
        return None  # UNKNOWN -- not yet meaningful to an executive
    if event_type == "INTERVIEW_RECORDED":
        return "Interview scheduled"
    if event_type == "OFFER_RECORDED":
        return "Offer received"
    if event_type == "OFFER_DECISION_RECORDED":
        decision = (event.get("reason") or "").upper()
        return {"ACCEPTED": "Offer accepted", "DECLINED": "Offer declined"}.get(decision, "Offer decision recorded")
    if event_type == "HIRE_RECORDED":
        return "Hired"
    if event_type == "BLOCKER_CREATED":
        return "Needs your attention"
    if event_type == "BLOCKER_RESOLVED":
        return "Blocker resolved"
    if event_type == "EMPLOYER_RESPONSE_HUMAN_CLASSIFIED":
        label = _CLASSIFICATION_RESOLUTION_LABELS.get(event.get("reason"), event.get("reason") or "")
        return f"Ambiguous employer message classified as: {label}" if label else "Ambiguous employer message classified"
    return None


def _describe_timeline_entry(entry: dict) -> str | None:
    """Plain-business-language label for one `get_timeline()` entry, for the
    Opportunity History section -- reuses the SAME event-type mapping as
    Recent Activity. Only "EVENT"-kind entries (opportunity_events) are
    translated: the timeline's other kinds (BLOCKER/EMPLOYER_RESPONSE/
    INTERVIEW/OFFER) are the same underlying domain-table rows their
    EVENT-kind counterpart event already describes -- including both would
    show the same real-world milestone twice."""
    if entry.get("kind") != "EVENT":
        return None
    event = entry.get("detail", {})
    if event.get("event_type") == "OPPORTUNITY_CREATED":
        return "Opportunity discovered"
    if event.get("event_type") == "USER_DECISION_RECORDED":
        reason = (event.get("reason") or "").title()
        return f"Decision recorded: {reason}" if reason else "Decision recorded"
    if event.get("event_type") == "APPLICATION_FEEDBACK_RECORDED":
        return None  # shown in its own Human Feedback section, not the milestone timeline
    if event.get("event_type") == "EMPLOYER_ACTION_REVIEWED":
        return None  # an internal review marker, not a business milestone
    return _describe_activity_event(event)


def _collapse_repeated_activity(events: list[dict]) -> list[dict]:
    """Merges repeated same-tracker, same-label activity entries into one
    (with a count) -- e.g. two separate real acknowledgement emails for the
    same opportunity read as "Acknowledgement received (x2)" rather than two
    near-identical lines. Purely a presentation collapse: the underlying
    employer_responses/opportunity_events rows are completely untouched, and
    the most recent occurrence's timestamp/link is what's kept and shown."""
    collapsed: dict[tuple, dict] = {}
    order: list[tuple] = []
    for event in events:
        key = (event["tracker_id"], event["label"])
        if key not in collapsed:
            collapsed[key] = {**event, "count": 1}
            order.append(key)
        else:
            collapsed[key]["count"] += 1
    return [collapsed[key] for key in order]


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    crm_stage: str = "",
    intelligence_priority: str = "",
    attn_priority: str = "",
    show_all_activity: bool = False,
    service: OpportunityCRMService = Depends(get_crm_service),
):
    filters = {field: value for field, value in (("crm_stage", crm_stage), ("intelligence_priority", intelligence_priority)) if value}
    filtered_opportunities = service.list_opportunities(**filters) if filters else []

    cumulative = service.cumulative_funnel_counts()
    rates = service.application_performance_rates()
    pipeline_groups = service.pipeline_group_counts()
    priority_mix = service.priority_mix_counts()
    total_opportunities = cumulative["DISCOVERED"]

    attention_items, attention_total, attention_priority_distribution = _dashboard_attention_items(
        service, priority=attn_priority, limit=5,
    )

    activity_limit = 50 if show_all_activity else 15  # over-fetch: business-event filtering below trims further
    raw_activity = service.recent_activity(limit=activity_limit)
    business_activity = []
    for event in raw_activity:
        label = _describe_activity_event(event)
        if label:
            business_activity.append({**event, "label": label})
    business_activity = _collapse_repeated_activity(business_activity)
    recent_activity = business_activity if show_all_activity else business_activity[:5]
    latest_activity = raw_activity[0]["occurred_at"] if raw_activity else None

    # A pipeline group maps reliably to a filtered Opportunities view only
    # when it corresponds to exactly one crm_stage -- never a forced/
    # ambiguous mapping for a multi-stage group.
    for group in pipeline_groups:
        group["filter_stage"] = group["stages"][0] if len(group["stages"]) == 1 else None

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "active_nav": "dashboard",
            "last_activity_at": latest_activity,
            "total_opportunities": total_opportunities,
            "cumulative": cumulative,
            "rates": rates,
            "pipeline_groups": pipeline_groups,
            "pipeline_group_max": max((g["count"] for g in pipeline_groups), default=0),
            "priority_mix": priority_mix,
            "priority_labels": _PRIORITY_LABELS,
            "attention_items": attention_items,
            "attention_total": attention_total,
            "attention_priority_distribution": attention_priority_distribution,
            "attn_priority": attn_priority,
            "recent_activity": recent_activity,
            "show_all_activity": show_all_activity,
            "filters": filters,
            "filtered_opportunities": filtered_opportunities,
            "selected": {"crm_stage": crm_stage, "intelligence_priority": intelligence_priority},
            "kpi_definitions": _KPI_DEFINITIONS,
        },
    )


@app.get("/opportunities", response_class=HTMLResponse)
def opportunities(
    request: Request,
    search: str = "",
    intelligence_priority: str = "",
    crm_stage: str = "",
    market: str = "",
    work_arrangement: str = "",
    career_track: str = "",
    source: str = "",
    min_score: float | None = None,
    max_score: float | None = None,
    application_state: str = "",
    page: int = 1,
    page_size: int = 25,
    service: OpportunityCRMService = Depends(get_crm_service),
):
    page = max(page, 1)
    page_size = min(max(page_size, 10), 100)
    try:
        result = service.search_opportunities(
            search=search, intelligence_priority=intelligence_priority, crm_stage=crm_stage,
            market=market, work_arrangement=work_arrangement, career_track=career_track, source=source,
            min_score=min_score, max_score=max_score, application_state=application_state,
            page=page, page_size=page_size,
        )
    except ValueError:
        application_state = ""
        result = service.search_opportunities(
            search=search, intelligence_priority=intelligence_priority, crm_stage=crm_stage,
            market=market, work_arrangement=work_arrangement, career_track=career_track, source=source,
            min_score=min_score, max_score=max_score, page=page, page_size=page_size,
        )
    for row in result["results"]:
        row["status_label"] = _STAGE_TO_GROUP_LABEL.get(row.get("crm_stage"), row.get("crm_stage") or "Unknown")
        row["eligibility_badge"] = _REMOTE_ELIGIBILITY_BADGE.get(row.get("remote_eligibility"), "UNKNOWN")

    priority_mix = service.priority_mix_counts()
    total_opportunities = sum(priority_mix.values())
    filter_options = service.opportunity_filter_options()

    return templates.TemplateResponse(
        request,
        "opportunities.html",
        {
            "active_nav": "opportunities",
            "wide_content": True,
            "total_opportunities": total_opportunities,
            "priority_mix": priority_mix,
            "priority_labels": _PRIORITY_LABELS,
            "filter_options": filter_options,
            "result": result,
            "selected": {
                "search": search, "intelligence_priority": intelligence_priority, "crm_stage": crm_stage,
                "market": market, "work_arrangement": work_arrangement, "career_track": career_track,
                "source": source, "min_score": min_score, "max_score": max_score, "page_size": page_size,
                "application_state": application_state,
            },
        },
    )


@app.get("/opportunity/{tracker_id}", response_class=HTMLResponse)
def opportunity_detail(request: Request, tracker_id: int, service: OpportunityCRMService = Depends(get_crm_service)):
    detail = service.get_opportunity_detail(tracker_id)
    if detail is None:
        return templates.TemplateResponse(
            request, "not_found.html", {"tracker_id": tracker_id, "active_nav": "dashboard"}, status_code=404,
        )
    record = detail["opportunity"]
    record["intelligence_priority_reasons_list"] = _safe_json_list(record.get("intelligence_priority_reasons"))
    record["package_gate_reasons_list"] = _safe_json_list(record.get("package_gate_reasons"))

    plain_entries = []
    for entry in detail["timeline"]:
        label = _describe_timeline_entry(entry)
        if label:
            plain_entries.append({"tracker_id": tracker_id, "label": label, "occurred_at": entry.get("at")})
    plain_timeline = _collapse_repeated_activity(plain_entries)

    # Web App Phase 3: a compact "Action Required" indicator, using the SAME
    # read model as /action-required -- never a fresh Priority-C-implies-
    # urgent inference. Absent here means no concrete action exists yet,
    # regardless of intelligence_priority.
    own_actions = [_decorate_action_item(item) for item in service.action_required_items() if item["tracker_id"] == tracker_id]

    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "detail": detail,
            "tracker_id": tracker_id,
            "active_nav": "dashboard",
            "status_label": _STAGE_TO_GROUP_LABEL.get(record.get("crm_stage"), record.get("crm_stage") or "Unknown"),
            "recommendation": _RECOMMENDATION_TEXT.get(record.get("intelligence_priority"), "Not Yet Evaluated"),
            "eligibility_matrix": _build_eligibility_matrix(record),
            "why_pursue": _build_why_pursue(record),
            "risks": _build_risks(record),
            "latest_decision": detail["user_decisions"][0] if detail["user_decisions"] else None,
            "decision_reason_labels": _DECISION_REASON_LABELS,
            "plain_timeline": plain_timeline,
            "own_actions": own_actions,
            # Web App Phase 4: a minimal cross-link, no redesign -- shown
            # only once the opportunity genuinely reached the Applications
            # workspace's own scope (package prepared or later).
            "has_application": record.get("crm_stage") in OpportunityCRMService.APPLICATION_WORKSPACE_STAGES or bool(record.get("applied_at")),
        },
    )


@app.post("/opportunity/{tracker_id}/decision")
def record_decision(
    tracker_id: int,
    decision: str = Form(...),
    reason_code: str = Form(""),
    note: str = Form(""),
    service: OpportunityCRMService = Depends(get_crm_service),
):
    """The one write endpoint this dashboard exposes: a controlled human
    screening signal, stored and audited separately from the intelligence
    engine's own priority (see `OpportunityCRMService.record_user_decision`).
    Never touches intelligence_priority/crm_stage, never triggers browser
    automation or submission -- there is no second application workflow
    here, only an auditable record of what the human decided."""
    if service.get_opportunity(tracker_id) is not None:
        try:
            service.record_user_decision(tracker_id, decision, reason_code=reason_code, note=note, decided_by="USER")
        except ValueError:
            pass  # invalid/tampered form input -- ignored, never crashes or corrupts state
    return RedirectResponse(url=f"/opportunity/{tracker_id}", status_code=303)


@app.get("/action-required", response_class=HTMLResponse)
def action_required(
    request: Request,
    priority: str = "",
    view: str = "active",
    service: OpportunityCRMService = Depends(get_crm_service),
):
    active_counts = service.action_required_counts()
    raw_items = service.action_required_items(include_resolved=(view == "resolved"))
    if view == "resolved":
        raw_items = [item for item in raw_items if item["resolved"]]
    if priority:
        if priority == "UNSCORED":
            raw_items = [item for item in raw_items if not item.get("intelligence_priority")]
        else:
            raw_items = [item for item in raw_items if item.get("intelligence_priority") == priority]

    decorated = [_decorate_action_item(item) for item in raw_items]
    rows = _group_action_items(decorated) if view == "active" else sorted(decorated, key=lambda i: i.get("arose_at") or "", reverse=True)

    # Phase 3.1: presentation-only reorganization of the SAME `rows` list
    # (produced above by the unmodified `_group_action_items()`) into one
    # bucket per category, group rows sorted before individual rows within
    # a category -- so the page reads "Review & Submit section, then
    # Eligibility Decision section" instead of one long urgency-sorted
    # list. Never recomputes membership, counts, or grouping itself.
    rows_by_category: dict[str, list[dict]] = {}
    if view == "active":
        for category in OpportunityCRMService.ACTION_CATEGORIES:
            cat_rows = [row for row in rows if row["category"] == category]
            cat_rows.sort(key=lambda row: not row.get("is_group", False))
            rows_by_category[category] = cat_rows

    # Automation state: the closest truthful signal existing services can
    # give -- there is no live worker/daemon process this page can observe,
    # so it never claims "Running"/"Worker Offline" (which would be
    # fabricated). It reports what IS genuinely knowable: whether the queue
    # is empty (nothing to act on) or non-empty (waiting on a human).
    automation_state = "Waiting for You" if active_counts["TOTAL"] > 0 else "Up to Date"

    return templates.TemplateResponse(
        request,
        "action_required.html",
        {
            "active_nav": "action_required",
            "wide_content": True,
            "counts": active_counts,
            "category_labels": _ACTION_CATEGORY_LABELS,
            "category_order": _ACTION_CATEGORY_DISPLAY_ORDER,
            "rows_by_category": rows_by_category,
            "any_active_rows": any(rows_by_category.values()),
            "section_preview_count": _SECTION_PREVIEW_COUNT,
            "automation_state": automation_state,
            "rows": rows,
            "view": view,
            "priority": priority,
            "priority_labels": _PRIORITY_LABELS,
        },
    )


@app.post("/action-required/blocker/{blocker_id}/resolve")
def resolve_action_blocker(
    blocker_id: int,
    note: str = Form(""),
    service: OpportunityCRMService = Depends(get_crm_service),
):
    """Reuses the EXISTING `resolve_human_blocker()` exactly as-is -- the
    same mechanism `python job_tracker.py`/the CLI already use. Never
    solves/bypasses the underlying CAPTCHA or MFA itself; this only records
    that a human has already handled it outside this page (e.g. in a
    terminal or a live browser session) so the existing automation runner
    can continue from where it paused."""
    try:
        service.resolve_human_blocker(blocker_id, resolution_note=note, resolved_by="USER")
    except ValueError:
        pass
    return RedirectResponse(url="/action-required", status_code=303)


@app.post("/action-required/employer-response/{tracker_id}/{response_id}/review")
def review_employer_action(
    tracker_id: int,
    response_id: int,
    note: str = Form(""),
    service: OpportunityCRMService = Depends(get_crm_service),
):
    """Marks one employer message reviewed -- an audited event, never an
    autonomous reply. Composing/sending any response to the employer is
    never done by this endpoint or anywhere else in this application."""
    try:
        service.mark_employer_response_reviewed(tracker_id, response_id, note=note, actor="USER")
    except ValueError:
        pass
    return RedirectResponse(url="/action-required", status_code=303)


# -- Web App Phase 4: Applications workspace ("digital working-paper file")-
_QUALIFICATION_VAULT_QUESTIONS = {
    "ACCOUNTING_QUALIFICATION": "Are you a Chartered Accountant?",
    "ACCOUNTING_QUALIFICATION_ACA_ACCA": "Are you ACA or ACCA specifically?",
    "ACCOUNTING_QUALIFICATION_OR_EQUIVALENT": "Do you hold an equivalent accounting qualification?",
}
_ANSWER_SOURCE_LABELS = {
    "PROFILE_FACT": "Candidate Fact", "USER_APPROVED_ANSWER": "Human Answer",
    "APPROVED_RULE": "Answer Vault Rule", "MANUAL_REQUIRED": "Human review required",
}
_APPLICATION_NEXT_ACTION_BY_STAGE = {
    "PREPARED": "Complete application preparation.",
    "READY_FOR_REVIEW": "Complete application preparation.",
    "READY_FOR_HUMAN_SUBMIT": "Review & Submit.",
    "APPLIED": "Await employer response.",
    "ACKNOWLEDGED": "Await further employer response.",
    "REJECTED": "Application rejected -- review outcome.",
    "DECLINED_OFFER": "Offer declined -- review outcome.",
    "ACCEPTED": "Offer accepted -- prepare for onboarding.",
    "HIRED": "Hired.",
}
# Phase 3.1 housekeeping (item 17): a real production Action Required
# outlier reason surfaces this raw internal code verbatim -- humanized here,
# presentation-only, never touching the stored reason/blocker logic itself.
_ACTION_REASON_HUMANIZE_PREFIXES = {
    "DIRECT_APPLICATION_ROUTE_NOT_VERIFIED": "No independently verified application route could be established for this vacancy",
}


def _humanize_action_reason_prefix(reason: str) -> str:
    """Presentation-only: if a raw reason string begins with a known
    internal code (e.g. "DIRECT_APPLICATION_ROUTE_NOT_VERIFIED: ..."),
    replace just that leading code with plain business language, keeping
    the rest of the real evidence text intact. Never changes the stored
    value or Action Required's own classification logic."""
    for code, plain in _ACTION_REASON_HUMANIZE_PREFIXES.items():
        if reason.startswith(code):
            rest = reason[len(code):].lstrip(": ")
            return f"{plain}. {rest}" if rest else plain
    return reason


def _load_application_package(tracker_id: int) -> ApplicationPackage | None:
    """Reads the SAME package JSON file `ApplicationPackageOrchestrator`
    already produces/owns (reusing its model and directory constant) --
    read-only, and deliberately avoids constructing the full orchestrator
    (which eagerly builds an ApplicationService/OpenAI client this
    read-only page never needs)."""
    path = Path(PACKAGE_DIR) / f"tracker-{tracker_id}.json"
    if not path.exists():
        return None
    try:
        return ApplicationPackage.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _qualification_vault_reference() -> list[dict]:
    """Current Answer Vault entries for the frozen accounting-qualification
    policy -- explicitly the CURRENT standing answer, never presented as
    verified historical submission text for one specific application (see
    `_build_screening_answers`'s notes-based historical evidence for that
    distinction)."""
    vault = ApplicationAnswerVault()
    rows = []
    for concept, question in _QUALIFICATION_VAULT_QUESTIONS.items():
        answer = vault.get_answer(concept)
        if not answer:
            continue
        rows.append({
            "question": question, "value": answer.value,
            "source_label": _ANSWER_SOURCE_LABELS.get(answer.answer_source, answer.answer_source),
            "status": answer.status, "evidence_reference": answer.evidence_reference,
        })
    return rows


def _build_documents(record: dict, package: ApplicationPackage | None) -> list[dict]:
    """Submitted/prepared documents, evidence-backed only -- a document is
    only offered for download when the actual local file still exists
    (never a broken/fabricated link), and is only labeled "Submitted"
    (rather than "Prepared") once the opportunity's own applied_at
    confirms a real submission occurred."""
    if not package:
        return []
    submitted = bool(record.get("applied_at"))

    def _entry(doc_type: str, raw_path: str, status: str, date: str, version_seed: str) -> dict | None:
        if not raw_path:
            return None
        resolved = (PROJECT_ROOT / raw_path).resolve()
        exists = resolved.is_file() and PROJECT_ROOT.resolve() in resolved.parents
        return {
            "type": doc_type, "filename": Path(raw_path).name,
            "status": "Submitted" if submitted else status, "date": date,
            "version": (version_seed or "")[:12], "downloadable": exists,
            "download_key": doc_type.lower().replace(" ", "_"),
        }

    docs = []
    # PDF preferred as the submitted artifact where it exists (item 7).
    resume = _entry("Resume (PDF)", package.resume_pdf_path, package.resume_status, package.resume_generated_at, package.resume_vacancy_identity) \
        or _entry("Resume", package.resume_path, package.resume_status, package.resume_generated_at, package.resume_vacancy_identity)
    if resume:
        docs.append(resume)
    cover_letter = _entry("Cover Letter", package.cover_letter_path, package.cover_letter_status, package.updated_at, package.vacancy_identity)
    if cover_letter:
        docs.append(cover_letter)
    return docs


_DOCUMENT_DOWNLOAD_FIELDS = {
    "resume_(pdf)": "resume_pdf_path", "resume": "resume_path", "cover_letter": "cover_letter_path",
}


def _build_screening_answers(record: dict, package: ApplicationPackage | None) -> dict:
    """Audit-critical (item 9): shows only what is genuinely evidenced.
    `answer_counts`/`manual_answer_count` are real, package-level evidence
    (the actual field-resolution categories recorded when this specific
    package was generated). Historical notes (when present) are the
    tracker's own recorded evidence of what was actually approved/submitted
    -- e.g. Tracker 81's notes literally confirm "ACA-ACCA=No" was the
    approved answer for that application. The qualification vault reference
    is ALWAYS labeled as the CURRENT standing answer, never claimed as the
    verified historical submission text unless the notes field says so."""
    return {
        "answer_counts": dict(package.answer_counts) if package else {},
        "manual_answer_count": package.manual_answer_count if package else None,
        "has_package_evidence": package is not None,
        "historical_notes": record.get("notes") or "",
        "qualification_reference": _qualification_vault_reference(),
    }


def _next_action(tracker_id: int, record: dict, service: OpportunityCRMService) -> dict:
    """Reuses the SAME Action Required read model (item 14) -- never a
    second blocker/action classification. An active, concrete Action
    Required item for this tracker always takes precedence over the
    generic per-stage fallback text."""
    own_actions = [item for item in service.action_required_items() if item["tracker_id"] == tracker_id]
    if own_actions:
        decorated = _decorate_action_item(own_actions[0])
        return {"text": decorated["primary_action"], "link": "/action-required", "urgency": decorated["urgency"]}
    text = _APPLICATION_NEXT_ACTION_BY_STAGE.get(record.get("crm_stage"), "No further action currently recorded.")
    return {"text": text, "link": None, "urgency": None}


# -- Web App Phase 4.1: event-triggered feedback only ------------------------
# Operational objective (see app/models/crm.py): minimize human
# interventions. An ordinary Applied -> automated-acknowledgement -> waiting
# application must never prompt for feedback -- a prominent prompt is
# surfaced only for the four genuinely high-information events below, and
# only once (a later view after feedback was already recorded for that same
# event is suppressed, though the record itself is always still visible/
# auditable and the user can always add more via the secondary, collapsed
# "Add my feedback" control regardless).
_MAJOR_OUTCOME_STAGES = ("OFFER", "ACCEPTED", "DECLINED_OFFER", "REJECTED", "HIRED")


def _application_feedback_trigger(record: dict, detail: dict, service: OpportunityCRMService) -> dict | None:
    """Returns the single highest-priority trigger (or None for the
    ordinary case), each carrying `arose_at` so the caller can tell whether
    feedback already recorded since then has addressed it."""
    # 1. An interview reached a real outcome -- the compact interest-level
    # prompt, not the general worth-pursuing one.
    completed_interviews = [i for i in detail["interviews"] if i.get("outcome") and i["outcome"] not in ("", "SCHEDULED")]
    if completed_interviews:
        arose_at = max((i.get("completed_at") or i.get("updated_at") or "") for i in completed_interviews)
        return {"type": "INTERVIEW_COMPLETED", "mode": "interest_only", "arose_at": arose_at,
                "prompt": "How interested are you after the interview?"}

    # 2. The user's own decision overrode the system's recommendation --
    # informational only; the reason is captured via the EXISTING My
    # Decision mechanism on Opportunity Detail, never a second form.
    latest_decision = detail["user_decisions"][0] if detail["user_decisions"] else None
    priority = record.get("intelligence_priority")
    if latest_decision:
        decision = latest_decision["decision"]
        overrode = (priority in ("A", "B") and decision in ("WATCH", "REJECT")) or (priority in ("D", "E") and decision == "APPLY")
        if overrode:
            return {
                "type": "OVERRIDE", "mode": "reuse_decision", "arose_at": latest_decision["decided_at"],
                "prompt": f"You chose {decision.title()} where the system recommended {_RECOMMENDATION_TEXT.get(priority, priority)}.",
                "already_explained": bool(latest_decision.get("reason_code") or latest_decision.get("note")),
            }

    # 3. A genuinely ambiguous employer message the system could not
    # confidently classify -- EXCLUDING one already resolved via the
    # Employer Inbox (Web App Phase 5: that page is now the proper place to
    # resolve these; re-nudging here after it's already been handled there
    # would be a redundant, competing prompt for the same fact).
    unknown_responses = [
        r for r in detail["employer_responses"]
        if r["response_type"] == "UNKNOWN" and not service.is_employer_response_reviewed(r["id"])
    ]
    if unknown_responses:
        arose_at = max(r["received_at"] for r in unknown_responses)
        return {"type": "AMBIGUOUS_EMPLOYER", "mode": "worth_pursuing", "arose_at": arose_at,
                "prompt": "The system could not confidently classify a recent employer message -- your read?"}

    # 4. A major, system-unobservable outcome (offer, withdrawal, rejection).
    offer_responses = [r for r in detail["employer_responses"] if r["response_type"] == "OFFER"]
    if record.get("crm_stage") in _MAJOR_OUTCOME_STAGES or offer_responses:
        arose_at = record.get("crm_stage_updated_at") or (offer_responses[0]["received_at"] if offer_responses else "")
        return {"type": "MAJOR_OUTCOME", "mode": "worth_pursuing", "arose_at": arose_at,
                "prompt": "A major outcome was reached for this application -- was it worth pursuing?"}

    return None


def _should_show_prominent_feedback_prompt(trigger: dict | None, latest_feedback: dict | None) -> bool:
    if not trigger or trigger["mode"] == "reuse_decision":
        return False
    if not latest_feedback:
        return True
    arose_at = trigger.get("arose_at") or ""
    return latest_feedback["created_at"] < arose_at


@app.get("/applications", response_class=HTMLResponse)
def applications(
    request: Request,
    tab: str = "",
    page: int = 1,
    page_size: int = 25,
    service: OpportunityCRMService = Depends(get_crm_service),
):
    page = max(page, 1)
    page_size = min(max(page_size, 10), 100)
    try:
        result = service.applications_register(tab=tab, page=page, page_size=page_size)
    except ValueError:
        tab = ""
        result = service.applications_register(page=page, page_size=page_size)

    # Same accepted Dashboard definitions, reused verbatim (item 2).
    cumulative = service.cumulative_funnel_counts()
    ready_for_submit = service.action_required_counts()["REVIEW_AND_SUBMIT"]

    own_actions_by_tracker: dict[int, dict] = {}
    for item in service.action_required_items():
        own_actions_by_tracker.setdefault(item["tracker_id"], _decorate_action_item(item))

    for row in result["results"]:
        tracker_id = row["id"]
        row["status_label"] = _STAGE_TO_GROUP_LABEL.get(row.get("crm_stage"), row.get("crm_stage") or "Unknown")
        latest_response = service.latest_employer_response(tracker_id)
        row["latest_outcome"] = _EMPLOYER_RESPONSE_PLAIN.get(latest_response["response_type"], latest_response["response_type"]) if latest_response else "None yet"
        active_action = own_actions_by_tracker.get(tracker_id)
        if active_action:
            row["next_action"] = active_action["primary_action"]
            row["next_action_link"] = "/action-required"
        else:
            row["next_action"] = _APPLICATION_NEXT_ACTION_BY_STAGE.get(row.get("crm_stage"), "-")
            row["next_action_link"] = None

    return templates.TemplateResponse(
        request,
        "applications.html",
        {
            "active_nav": "applications",
            "wide_content": True,
            "cumulative": cumulative,
            "ready_for_submit": ready_for_submit,
            "result": result,
            "tab": tab,
            "page_size": page_size,
        },
    )


@app.get("/application/{tracker_id}", response_class=HTMLResponse)
def application_detail(request: Request, tracker_id: int, service: OpportunityCRMService = Depends(get_crm_service)):
    detail = service.get_opportunity_detail(tracker_id)
    if detail is None:
        return templates.TemplateResponse(
            request, "not_found.html", {"tracker_id": tracker_id, "active_nav": "applications"}, status_code=404,
        )
    record = detail["opportunity"]
    # This working-paper view is only meaningful once an opportunity
    # genuinely reached package preparation or later -- never fabricate an
    # empty application file for something still just discovered/scored.
    if record.get("crm_stage") not in OpportunityCRMService.APPLICATION_WORKSPACE_STAGES and not record.get("applied_at"):
        return RedirectResponse(url=f"/opportunity/{tracker_id}", status_code=303)

    record["intelligence_priority_reasons_list"] = _safe_json_list(record.get("intelligence_priority_reasons"))
    package = _load_application_package(tracker_id)

    plain_entries = []
    for entry in detail["timeline"]:
        label = _describe_timeline_entry(entry)
        if label:
            plain_entries.append({"tracker_id": tracker_id, "label": label, "occurred_at": entry.get("at")})
    plain_timeline = _collapse_repeated_activity(plain_entries)

    employer_feedback = [
        {
            **response,
            "classification_label": _humanize_action_reason_prefix(_EMPLOYER_RESPONSE_PLAIN.get(response["response_type"], response["response_type"])),
            "is_meaningful": response["response_type"] not in ("ACKNOWLEDGEMENT", "UNKNOWN"),
        }
        for response in detail["employer_responses"]
    ]

    latest_feedback = service.get_latest_application_feedback(tracker_id)
    feedback_trigger = _application_feedback_trigger(record, detail, service)

    return templates.TemplateResponse(
        request,
        "application_detail.html",
        {
            "detail": detail,
            "tracker_id": tracker_id,
            "active_nav": "applications",
            "status_label": _STAGE_TO_GROUP_LABEL.get(record.get("crm_stage"), record.get("crm_stage") or "Unknown"),
            "why_pursue": _build_why_pursue(record),
            "risks": [_humanize_action_reason_prefix(r) for r in _build_risks(record)],
            "package": package,
            "documents": _build_documents(record, package),
            "screening_answers": _build_screening_answers(record, package),
            "employer_feedback": employer_feedback,
            "plain_timeline": plain_timeline,
            "next_action": _next_action(tracker_id, record, service),
            "latest_feedback": latest_feedback,
            "feedback_history": service.list_application_feedback(tracker_id),
            "worth_pursuing_options": sorted(OpportunityCRMService.APPLICATION_FEEDBACK_WORTH_PURSUING),
            "interest_change_options": sorted(OpportunityCRMService.APPLICATION_FEEDBACK_INTEREST_CHANGE),
            "feedback_trigger": feedback_trigger,
            "show_prominent_feedback_prompt": _should_show_prominent_feedback_prompt(feedback_trigger, latest_feedback),
            # Item 20: the smallest contextual link -- only shown once a
            # real interview record exists, never a fabricated one.
            "latest_interview_id": detail["interviews"][-1]["id"] if detail["interviews"] else None,
        },
    )


@app.get("/application/{tracker_id}/document/{doc_key}")
def download_application_document(tracker_id: int, doc_key: str):
    """Serves ONLY a file path already recorded in this tracker's OWN
    package JSON (never an arbitrary user-supplied path) -- `doc_key` is a
    fixed selector, not a filesystem path."""
    field_name = _DOCUMENT_DOWNLOAD_FIELDS.get(doc_key)
    if not field_name:
        raise HTTPException(status_code=404)
    package = _load_application_package(tracker_id)
    if package is None:
        raise HTTPException(status_code=404)
    raw_path = getattr(package, field_name, "") or ""
    if not raw_path:
        raise HTTPException(status_code=404)
    resolved = (PROJECT_ROOT / raw_path).resolve()
    if PROJECT_ROOT.resolve() not in resolved.parents or not resolved.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(resolved, filename=resolved.name)


@app.post("/application/{tracker_id}/feedback")
def record_application_feedback(
    tracker_id: int,
    worth_pursuing: str = Form(...),
    interest_change: str = Form(""),
    note: str = Form(""),
    service: OpportunityCRMService = Depends(get_crm_service),
):
    """Append-only human learning signal (item 13) -- separate from the
    Phase 2 pre-application user_decisions table, and never alters
    intelligence_priority/crm_stage."""
    if service.get_opportunity(tracker_id) is not None:
        try:
            service.record_application_feedback(tracker_id, worth_pursuing, interest_change=interest_change, note=note, actor="USER")
        except ValueError:
            pass
    return RedirectResponse(url=f"/application/{tracker_id}", status_code=303)


# -- Web App Phase 5: Employer Inbox -----------------------------------------
# Communication workspace/evidence ONLY (item 9) -- Action Required stays the
# one consolidated "what must I actually do" queue. Every fact here comes
# from OpportunityCRMService.employer_inbox_items()/employer_inbox_summary();
# this module only adds plain-language labels and the actionable/required-
# action presentation rule (item 5), the same fact/presentation split
# _decorate_action_item already uses for Action Required.
_EMPLOYER_INBOX_ACTIONABLE_TYPES = frozenset({
    "RECRUITER_CONTACT", "SCREENING_REQUEST", "INTERVIEW_INVITATION", "ASSESSMENT_REQUEST", "OFFER",
})
_EMPLOYER_INBOX_REQUIRED_ACTION = {
    "RECRUITER_CONTACT": "Answer employer",
    "SCREENING_REQUEST": "Complete screening",
    "INTERVIEW_INVITATION": "Review interview invitation",
    "ASSESSMENT_REQUEST": "Complete assessment",
    "OFFER": "Review offer",
}


def _decorate_employer_inbox_item(item: dict) -> dict:
    """Non-actionable by design: ACKNOWLEDGEMENT and a confidently-classified
    REJECTION (item 5's own examples) -- neither response_type is even in
    `_EMPLOYER_INBOX_ACTIONABLE_TYPES`, so no evidence is ever needed to
    prove "no response required" for them. A genuinely UNKNOWN message is
    actionable ("Human classification required") until -- and only until --
    a human classification is recorded for it (never blindly forever, and
    never blindly resolved either). Every other type is actionable exactly
    until it is marked reviewed (the SAME resolved fact Action Required's
    own EMPLOYER_ACTION category uses) -- so "do not blindly treat every
    RECRUITER_RESPONSE as actionable" is satisfied by the same existing
    resolution mechanism, never a second, invented heuristic."""
    response_type = item["response_type"]
    classification = item.get("classification")
    if response_type == "UNKNOWN":
        actionable = classification is None
        required_action = "Human classification required" if actionable else "No action required"
    elif response_type in _EMPLOYER_INBOX_ACTIONABLE_TYPES:
        actionable = not item["resolved"]
        required_action = _EMPLOYER_INBOX_REQUIRED_ACTION[response_type] if actionable else "No action required"
    else:  # ACKNOWLEDGEMENT, REJECTION
        actionable = False
        required_action = "No action required"
    return {
        **item,
        "classification_label": _EMPLOYER_RESPONSE_PLAIN.get(response_type, response_type),
        "is_meaningful": response_type not in ("ACKNOWLEDGEMENT", "UNKNOWN"),
        "actionable": actionable,
        "required_action": required_action,
        "resolved_classification_label": (
            _CLASSIFICATION_RESOLUTION_LABELS.get(classification["resolved_type"], classification["resolved_type"])
            if classification else None
        ),
    }


_EMPLOYER_INBOX_FILTERS = {
    "needs_action": lambda item: item["actionable"],
    "meaningful": lambda item: item["is_meaningful"],
    "interview": lambda item: item["response_type"] == "INTERVIEW_INVITATION",
    "assessment": lambda item: item["response_type"] == "ASSESSMENT_REQUEST",
    "rejected": lambda item: item["response_type"] == "REJECTION",
    "offer": lambda item: item["response_type"] == "OFFER",
    "acknowledgement": lambda item: item["response_type"] == "ACKNOWLEDGEMENT",
    "unknown": lambda item: item["response_type"] == "UNKNOWN",
}


@app.get("/employer-inbox", response_class=HTMLResponse)
def employer_inbox(
    request: Request,
    filter: str = "",
    search: str = "",
    priority: str = "",
    service: OpportunityCRMService = Depends(get_crm_service),
):
    summary = service.employer_inbox_summary()
    items = [_decorate_employer_inbox_item(item) for item in service.employer_inbox_items()]

    if filter:
        predicate = _EMPLOYER_INBOX_FILTERS.get(filter)
        if predicate:
            items = [item for item in items if predicate(item)]
        else:
            filter = ""
    if search:
        needle = search.lower()
        items = [
            item for item in items
            if needle in (item["company"] or "").lower() or needle in (item["job_title"] or "").lower()
        ]
    if priority:
        if priority == "UNSCORED":
            items = [item for item in items if not item.get("intelligence_priority")]
        else:
            items = [item for item in items if item.get("intelligence_priority") == priority]

    # Default ordering (item 2): unresolved actionable communications first,
    # rather than pure chronological noise -- a display-ranking concern
    # only, never a new scoring system. Python's sort is stable, so recency
    # from the first pass survives within the actionable/not-actionable split.
    items.sort(key=lambda item: item.get("received_at") or "", reverse=True)
    items.sort(key=lambda item: not item["actionable"])

    return templates.TemplateResponse(
        request,
        "employer_inbox.html",
        {
            "active_nav": "employer_inbox",
            "wide_content": True,
            "summary": summary,
            "items": items,
            "filter": filter,
            "search": search,
            "priority": priority,
            "priority_labels": _PRIORITY_LABELS,
        },
    )


@app.get("/employer-inbox/{tracker_id}/{response_id}", response_class=HTMLResponse)
def employer_inbox_detail(
    request: Request,
    tracker_id: int,
    response_id: int,
    service: OpportunityCRMService = Depends(get_crm_service),
):
    detail = service.get_opportunity_detail(tracker_id)
    response = service.get_employer_response(response_id)
    if detail is None or response is None or response["tracker_id"] != tracker_id:
        return templates.TemplateResponse(
            request, "not_found.html", {"tracker_id": tracker_id, "active_nav": "employer_inbox"}, status_code=404,
        )
    record = detail["opportunity"]
    classification = (
        service.get_employer_response_classification(response_id) if response["response_type"] == "UNKNOWN" else None
    )
    item = _decorate_employer_inbox_item({
        "employer_response_id": response["id"], "tracker_id": tracker_id,
        "company": record.get("company") or "", "job_title": record.get("job_title") or "",
        "intelligence_priority": record.get("intelligence_priority"), "crm_stage": record.get("crm_stage"),
        "applied_at": record.get("applied_at"),
        "response_type": response["response_type"], "received_at": response["received_at"],
        "summary": response.get("summary") or "", "evidence_reference": response.get("evidence_reference") or "",
        "source": response.get("source") or "",
        "resolved": service.is_employer_response_reviewed(response_id),
        "classification": classification,
    })

    # A short, relevant employer/application milestone list -- deliberately
    # NOT the full Application working paper (item 4: "avoid duplicating");
    # reuses the SAME plain-language timeline builder Application Detail
    # already uses, scoped to this tracker.
    plain_entries = []
    for entry in detail["timeline"]:
        label = _describe_timeline_entry(entry)
        if label:
            plain_entries.append({"tracker_id": tracker_id, "label": label, "occurred_at": entry.get("at")})
    plain_timeline = _collapse_repeated_activity(plain_entries)

    return templates.TemplateResponse(
        request,
        "employer_communication_detail.html",
        {
            "active_nav": "employer_inbox",
            "detail": detail,
            "tracker_id": tracker_id,
            "item": item,
            "status_label": _STAGE_TO_GROUP_LABEL.get(record.get("crm_stage"), record.get("crm_stage") or "Unknown"),
            "plain_timeline": plain_timeline,
            # Item 19 (Phase 6): link to the real Interview workspace once a
            # genuine interview record exists for this tracker -- never a
            # fabricated one invented solely for this presentation.
            "has_application": record.get("crm_stage") in OpportunityCRMService.APPLICATION_WORKSPACE_STAGES or bool(record.get("applied_at")),
            "has_interview_record": bool(detail["interviews"]),
            "interview_id": detail["interviews"][-1]["id"] if detail["interviews"] else None,
            "classification_options": list(_CLASSIFICATION_RESOLUTION_LABELS.keys()),
            "classification_labels": _CLASSIFICATION_RESOLUTION_LABELS,
            "classification_history": service.list_employer_response_classifications(tracker_id),
        },
    )


@app.post("/employer-inbox/{tracker_id}/{response_id}/classify")
def classify_employer_response(
    tracker_id: int,
    response_id: int,
    resolved_type: str = Form(...),
    note: str = Form(""),
    service: OpportunityCRMService = Depends(get_crm_service),
):
    """The one write endpoint for resolving a genuinely UNKNOWN employer
    message (item 6) -- an explicit, audited human judgment call. NEVER
    rewrites the original Gmail-sourced employer_responses row, and never
    sends/drafts/modifies/labels anything in Gmail itself -- this only calls
    the CRM's own append-only classification record."""
    if resolved_type not in EMPLOYER_RESPONSE_HUMAN_CLASSIFICATIONS:
        return RedirectResponse(url=f"/employer-inbox/{tracker_id}/{response_id}", status_code=303)
    try:
        service.record_employer_response_classification(tracker_id, response_id, resolved_type, note=note, actor="USER")
    except ValueError:
        pass
    return RedirectResponse(url=f"/employer-inbox/{tracker_id}/{response_id}", status_code=303)


# -- Web App Phase 6: Interviews ---------------------------------------------
# The interview record itself is created exactly ONE way --
# OpportunityCRMService.record_interview(), called automatically from a
# genuinely detected INTERVIEW_INVITATION (see
# _maybe_auto_create_interview_from_invitation) or explicitly -- never a
# second pipeline here. Preparation material is assembled read-only from
# EXISTING evidence via interview_briefing_service; nothing here writes a
# new candidate/job fact.
_INTERVIEW_STAGE_LABELS = {
    "SCREENING": "Screening", "INTERVIEW_1": "First Interview",
    "INTERVIEW_2": "Technical / Case", "FINAL_INTERVIEW": "Final Interview",
}
_INTERVIEW_BUCKET_LABELS = {
    "upcoming": "Upcoming", "needs_time": "Needs a Confirmed Time",
    "recently_completed": "Recently Completed", "historical": "Historical",
}


def _interview_current_status(interview: dict) -> str:
    stage_label = _INTERVIEW_STAGE_LABELS.get(interview.get("stage"), interview.get("stage") or "Unknown")
    outcome = interview.get("outcome") or ""
    if not outcome or outcome == "SCHEDULED":
        return f"{stage_label} -- Scheduled" if interview.get("scheduled_at") else f"{stage_label} -- Time Not Yet Confirmed"
    return f"{stage_label} -- {_humanize(outcome)}"


def _interview_preparation_status(record: dict) -> str:
    return "Ready" if (record.get("evaluation_snapshot") or record.get("job_description")) else "Limited Evidence"


def _interview_next_action(interview: dict, record: dict, service: OpportunityCRMService) -> dict:
    """Reuses the SAME Action Required read model (item 18) -- an active,
    concrete item for this tracker always takes precedence. Otherwise a
    small, honest, interview-specific fallback -- never a fabricated one."""
    own_actions = [item for item in service.action_required_items() if item["tracker_id"] == interview["tracker_id"]]
    if own_actions:
        decorated = _decorate_action_item(own_actions[0])
        return {"text": decorated["primary_action"], "link": "/action-required"}
    if not interview.get("scheduled_at") and (interview.get("outcome") or "SCHEDULED") == "SCHEDULED":
        return {"text": "Confirm interview time", "link": f"/interview/{interview['id']}"}
    outcome = interview.get("outcome") or ""
    if outcome and outcome != "SCHEDULED":
        return {"text": "Review preparation for the next round, or add an optional debrief", "link": f"/interview/{interview['id']}"}
    return {"text": "Review preparation", "link": f"/interview/{interview['id']}"}


@app.get("/interviews", response_class=HTMLResponse)
def interviews(request: Request, service: OpportunityCRMService = Depends(get_crm_service)):
    summary = service.interviews_summary()
    register = service.interview_register()

    rows = []
    for interview in register[:100]:  # bounded rendering (item 4)
        record = service.get_opportunity(interview["tracker_id"]) or {}
        next_action = _interview_next_action(interview, record, service)
        rows.append({
            **interview,
            "stage_label": _INTERVIEW_STAGE_LABELS.get(interview.get("stage"), interview.get("stage") or "Unknown"),
            "bucket_label": _INTERVIEW_BUCKET_LABELS.get(interview["bucket"], interview["bucket"]),
            "current_status": _interview_current_status(interview),
            "preparation_status": _interview_preparation_status(record),
            "next_action_text": next_action["text"],
            "next_action_link": next_action["link"],
        })

    next_interview = summary.get("next_interview")
    if next_interview:
        next_record = service.get_opportunity(next_interview["tracker_id"]) or {}
        next_interview = {**next_interview, "company": next_record.get("company"), "job_title": next_record.get("job_title")}

    return templates.TemplateResponse(
        request,
        "interviews.html",
        {
            "active_nav": "interviews",
            "wide_content": True,
            "summary": summary,
            "next_interview": next_interview,
            "rows": rows,
        },
    )


@app.get("/interview/{interview_id}", response_class=HTMLResponse)
def interview_workspace(request: Request, interview_id: int, service: OpportunityCRMService = Depends(get_crm_service)):
    interview = service.get_interview(interview_id)
    if interview is None:
        return templates.TemplateResponse(
            request, "not_found.html", {"tracker_id": interview_id, "active_nav": "interviews"}, status_code=404,
        )
    tracker_id = interview["tracker_id"]
    detail = service.get_opportunity_detail(tracker_id)
    record = detail["opportunity"]
    package = _load_application_package(tracker_id)
    profile = MasterProfileService().load()

    readiness = briefing_service.build_readiness_matrix(record, profile)
    why_pursue = _build_why_pursue(record)
    risks = [_humanize_action_reason_prefix(r) for r in _build_risks(record)]
    briefing = briefing_service.build_briefing(record, profile, readiness, risks, why_pursue)
    my_evidence = briefing_service.build_my_evidence(readiness, profile)
    answer_prep = briefing_service.build_answer_preparation(readiness, profile)
    likely_questions = briefing_service.build_likely_questions(record, readiness, risks)
    company_intelligence = briefing_service.build_company_intelligence(record)
    employer_questions = briefing_service.build_employer_questions(record, readiness)

    latest_feedback = service.get_latest_application_feedback(tracker_id)
    feedback_trigger = _application_feedback_trigger(record, detail, service)

    interview_invitation = next(
        (r for r in detail["employer_responses"] if r["response_type"] == "INTERVIEW_INVITATION"), None
    )

    other_interviews = [i for i in detail["interviews"] if i["id"] != interview_id]

    return templates.TemplateResponse(
        request,
        "interview_workspace.html",
        {
            "active_nav": "interviews",
            "detail": detail,
            "tracker_id": tracker_id,
            "interview": interview,
            "other_interviews": other_interviews,
            "stage_label": _INTERVIEW_STAGE_LABELS.get(interview.get("stage"), interview.get("stage") or "Unknown"),
            "current_status": _interview_current_status(interview),
            "status_label": _STAGE_TO_GROUP_LABEL.get(record.get("crm_stage"), record.get("crm_stage") or "Unknown"),
            "briefing": briefing,
            "readiness": readiness,
            "my_evidence": my_evidence,
            "answer_prep": answer_prep,
            "likely_questions": likely_questions,
            "company_intelligence": company_intelligence,
            "employer_questions": employer_questions,
            "screening_answers": _build_screening_answers(record, package),
            "has_application": record.get("crm_stage") in OpportunityCRMService.APPLICATION_WORKSPACE_STAGES or bool(record.get("applied_at")),
            "employer_inbox_link": (f"/employer-inbox/{tracker_id}/{interview_invitation['id']}" if interview_invitation else None),
            "latest_feedback": latest_feedback,
            "feedback_trigger": feedback_trigger,
            "show_prominent_feedback_prompt": _should_show_prominent_feedback_prompt(feedback_trigger, latest_feedback),
            "worth_pursuing_options": sorted(OpportunityCRMService.APPLICATION_FEEDBACK_WORTH_PURSUING),
        },
    )


@app.post("/interview/{interview_id}/schedule")
def confirm_interview_schedule(
    interview_id: int,
    scheduled_at: str = Form(...),
    service: OpportunityCRMService = Depends(get_crm_service),
):
    """The one genuinely consequential write this workspace exposes beyond
    notes -- confirming a real date/time (item 17). Never auto-accepts a
    time; a human must submit this form."""
    try:
        service.update_interview_schedule(interview_id, scheduled_at)
    except ValueError:
        pass
    return RedirectResponse(url=f"/interview/{interview_id}", status_code=303)


@app.post("/interview/{interview_id}/notes")
def update_interview_notes(
    interview_id: int,
    notes: str = Form(""),
    service: OpportunityCRMService = Depends(get_crm_service),
):
    """Optional notes (item 14) -- never required for preparation."""
    try:
        service.update_interview_notes(interview_id, notes)
    except ValueError:
        pass
    return RedirectResponse(url=f"/interview/{interview_id}", status_code=303)


# -- Web App Phase 7: Analytics & Learning -----------------------------------
# Every figure reuses an EXISTING CRM read model (see analytics_service.py's
# own module docstring) -- this section only adds presentation labels and
# the Learning Center's governance wiring. No code path here writes to
# intelligence_priority/scoring/eligibility/candidate facts/the Answer
# Vault; the ONLY writes are governance decisions on a proposed_learnings
# row (see _govern_learning), which record human approval only.
_LEARNING_DOMAIN_LABELS = {
    "SEARCH_STRATEGY": "Search Strategy", "MARKET": "Market", "PRIORITY": "Priority",
    "ELIGIBILITY": "Eligibility", "CV_STRATEGY": "CV Strategy", "APPLICATION_STRATEGY": "Application Strategy",
    "SCREENING": "Screening", "EVIDENCE": "Evidence", "AUTOMATION": "Automation",
}
_LEARNING_STATUS_LABELS = {
    "PROPOSED": "Proposed", "ACCEPTED": "Accepted", "NEED_MORE_EVIDENCE": "Need More Evidence",
    "REJECTED": "Rejected", "RETIRED": "Retired",
}
_FUNNEL_STAGE_LABELS = (
    ("DISCOVERED", "Discovered"), ("APPLIED", "Applied"), ("MEANINGFUL_RESPONSE", "Meaningful Response"),
    ("INTERVIEW", "Interview"), ("OFFER", "Offer"),
)


def _learning_candidates_by_key(service: OpportunityCRMService) -> dict[str, dict]:
    return {c["key"]: c for c in analytics_service.generate_learning_candidates(service)}


def _govern_learning(service: OpportunityCRMService, key: str, status: str, review_note: str) -> None:
    """The one write path for every Learning Center governance action
    (item 19). Never mutates intelligence_priority/scoring/eligibility --
    only records the human's decision, on the SAME deterministic candidate
    `generate_learning_candidates()` would compute right now, so what gets
    persisted always matches what was actually shown on screen."""
    candidate = _learning_candidates_by_key(service).get(key)
    existing = service.get_proposed_learning_by_key(key)
    if not candidate and not existing:
        return  # unknown/stale key -- ignore, never crash
    if existing:
        service.update_proposed_learning_status(existing["id"], status, review_note=review_note, actor="USER")
        return
    service.record_proposed_learning(
        key, domain=candidate["domain"], title=candidate["title"], observation=candidate["observation"],
        proposed_change=candidate["proposed_change"], evidence_summary=candidate["evidence_summary"],
        sample_size=candidate["sample_size"], confidence=candidate["confidence"],
        status=status, review_note=review_note, actor="USER",
    )


@app.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request, period: str = "all", service: OpportunityCRMService = Depends(get_crm_service)):
    overview = analytics_service.performance_overview(service)
    intervention = analytics_service.human_intervention_metrics(service)
    trends = analytics_service.funnel_and_trends(service, period)
    drivers = {
        "Priority": analytics_service.priority_effectiveness(service),
        "Market": analytics_service.market_performance(service),
        "Source": analytics_service.source_performance(service),
        "Job Family": analytics_service.career_track_performance(service),
        "Work Arrangement": analytics_service.work_arrangement_performance(service),
    }
    cv_strategy = analytics_service.cv_strategy_performance(service)
    screening = analytics_service.screening_eligibility_intelligence(service)
    rejections = analytics_service.rejection_intelligence(service)
    employer_feedback = analytics_service.employer_feedback_intelligence(service)
    observations = analytics_service.generate_observations(service)
    reconciliation = analytics_service.reconciliation_checks(service)

    candidates = analytics_service.generate_learning_candidates(service)
    persisted = {row["key"]: row for row in service.list_proposed_learnings()}
    learning_items = []
    seen_keys = set()
    for candidate in candidates:
        key = candidate["key"]
        seen_keys.add(key)
        existing = persisted.get(key)
        if existing:
            learning_items.append({**existing, "is_persisted": True})
        else:
            learning_items.append({
                **candidate, "id": None, "status": LEARNING_STATUS_PROPOSED,
                "is_persisted": False, "reviewed_at": None, "review_note": "",
            })
    for key, row in persisted.items():
        if key not in seen_keys:
            learning_items.append({**row, "is_persisted": True})

    learning_by_status = {
        status: [item for item in learning_items if item["status"] == status]
        for status in (LEARNING_STATUS_PROPOSED, LEARNING_STATUS_ACCEPTED, LEARNING_STATUS_NEED_MORE_EVIDENCE, LEARNING_STATUS_REJECTED)
    }

    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "active_nav": "analytics",
            "wide_content": True,
            "overview": overview,
            "intervention": intervention,
            "trends": trends,
            "funnel_stage_labels": _FUNNEL_STAGE_LABELS,
            "period": period,
            "drivers": drivers,
            "cv_strategy": cv_strategy,
            "screening": screening,
            "rejections": rejections,
            "employer_feedback": employer_feedback,
            "observations": observations,
            "reconciliation": reconciliation,
            "kpi_definitions": _KPI_DEFINITIONS,
            "learning_by_status": learning_by_status,
            "domain_labels": _LEARNING_DOMAIN_LABELS,
            "status_labels": _LEARNING_STATUS_LABELS,
        },
    )


@app.get("/analytics/rate/{metric}", response_class=HTMLResponse)
def analytics_rate_detail(request: Request, metric: str, service: OpportunityCRMService = Depends(get_crm_service)):
    """Web App Phase 7.1 item 11/12: the "Summary -> Calculation ->
    Population -> Underlying records" drill-down for one rate KPI."""
    try:
        detail = analytics_service.rate_detail(service, metric)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Unknown rate metric: {metric!r}")
    return templates.TemplateResponse(
        request,
        "analytics_rate_detail.html",
        {"active_nav": "analytics", "detail": detail},
    )


@app.post("/analytics/learning/{key}/accept")
def accept_learning(key: str, review_note: str = Form(""), service: OpportunityCRMService = Depends(get_crm_service)):
    _govern_learning(service, key, LEARNING_STATUS_ACCEPTED, review_note)
    return RedirectResponse(url="/analytics", status_code=303)


@app.post("/analytics/learning/{key}/need-more-evidence")
def need_more_evidence_learning(key: str, review_note: str = Form(""), service: OpportunityCRMService = Depends(get_crm_service)):
    _govern_learning(service, key, LEARNING_STATUS_NEED_MORE_EVIDENCE, review_note)
    return RedirectResponse(url="/analytics", status_code=303)


@app.post("/analytics/learning/{key}/reject")
def reject_learning(key: str, review_note: str = Form(""), service: OpportunityCRMService = Depends(get_crm_service)):
    _govern_learning(service, key, LEARNING_STATUS_REJECTED, review_note)
    return RedirectResponse(url="/analytics", status_code=303)


def _register_placeholder_route(path: str, key: str, label: str, description: str) -> None:
    @app.get(path, response_class=HTMLResponse, name=f"placeholder_{key}")
    def _placeholder(request: Request):
        return templates.TemplateResponse(
            request, "placeholder.html",
            {"active_nav": key, "nav_label": label, "nav_description": description},
        )


for _path, (_key, _label, _description) in _PLACEHOLDER_PAGES.items():
    _register_placeholder_route(_path, _key, _label, _description)
