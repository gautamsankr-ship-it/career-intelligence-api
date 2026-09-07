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
from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_eligibility_policy import intelligence_priority_gate
from app.services.application_package_orchestrator import PACKAGE_DIR
from app.services.opportunity_crm_service import OpportunityCRMService

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
    "/employer-inbox": (
        "employer_inbox", "Employer Inbox",
        "A dedicated Employer Inbox is coming in a later phase. Employer/recruiter "
        "responses are already tracked per opportunity and summarized on the Dashboard.",
    ),
    "/interviews": (
        "interviews", "Interviews",
        "A dedicated Interviews view is coming in a later phase.",
    ),
    "/analytics": (
        "analytics", "Analytics & Learning",
        "Deeper analytics are coming in a later phase. Conversion rates are already "
        "available on the Dashboard.",
    ),
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
    page: int = 1,
    page_size: int = 25,
    service: OpportunityCRMService = Depends(get_crm_service),
):
    page = max(page, 1)
    page_size = min(max(page_size, 10), 100)
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
            "latest_feedback": service.get_latest_application_feedback(tracker_id),
            "feedback_history": service.list_application_feedback(tracker_id),
            "worth_pursuing_options": sorted(OpportunityCRMService.APPLICATION_FEEDBACK_WORTH_PURSUING),
            "interest_change_options": sorted(OpportunityCRMService.APPLICATION_FEEDBACK_INTEREST_CHANGE),
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


def _register_placeholder_route(path: str, key: str, label: str, description: str) -> None:
    @app.get(path, response_class=HTMLResponse, name=f"placeholder_{key}")
    def _placeholder(request: Request):
        return templates.TemplateResponse(
            request, "placeholder.html",
            {"active_nav": key, "nav_label": label, "nav_description": description},
        )


for _path, (_key, _label, _description) in _PLACEHOLDER_PAGES.items():
    _register_placeholder_route(_path, _key, _label, _description)
