"""Task 21.33: minimal operational dashboard over the Application CRM
(Task 21.32's OpportunityCRMService). No new tracking database, no
duplicated business logic -- every number and every row comes straight from
the CRM's own read-model methods.

Launch: `python dashboard.py` from the repo root -> http://127.0.0.1:8000
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.opportunity_crm_service import OpportunityCRMService

app = FastAPI(title="Career Intelligence CRM Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates" / "dashboard"))

_PRIORITY_LABELS = {
    "A": "Priority Apply", "B": "Apply", "C": "Human Review", "D": "Watch", "E": "Reject", "UNSCORED": "Not Yet Evaluated",
}
_PRIORITY_ORDER = ("A", "B", "C", "D", "E", "UNSCORED")

# Phase 1 web app: navigation placeholders for every approved sidebar section
# beyond the Executive Dashboard. Each renders the shared shell with a short,
# honest "coming later" message -- no fabricated functionality.
_PLACEHOLDER_PAGES = {
    "/opportunities": (
        "opportunities", "Opportunities",
        "A dedicated, filterable Opportunities workspace is coming in a later phase. "
        "For now, use the Opportunities snapshot and priority breakdown on the Dashboard.",
    ),
    "/applications": (
        "applications", "Applications",
        "A dedicated Applications tracker is coming in a later phase. "
        "For now, see Applications Submitted and Application Performance on the Dashboard.",
    ),
    "/action-required": (
        "action_required", "Action Required",
        "A dedicated Action Required queue is coming in a later phase. "
        "For now, see “Needs My Attention” on the Dashboard.",
    ),
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


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    crm_stage: str = "",
    intelligence_priority: str = "",
    attn_priority: str = "",
    show_all_attention: bool = False,
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

    attention_total = len(service.needs_attention())
    attention_priority_distribution = service.attention_priority_distribution()
    attention_limit = None if show_all_attention else 5
    attention_items = service.attention_queue(priority=attn_priority or None, limit=attention_limit)

    activity_limit = 50 if show_all_activity else 15  # over-fetch: business-event filtering below trims further
    raw_activity = service.recent_activity(limit=activity_limit)
    business_activity = []
    for event in raw_activity:
        label = _describe_activity_event(event)
        if label:
            business_activity.append({**event, "label": label})
    recent_activity = business_activity if show_all_activity else business_activity[:5]
    latest_activity = raw_activity[0]["occurred_at"] if raw_activity else None

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
            "show_all_attention": show_all_attention,
            "recent_activity": recent_activity,
            "show_all_activity": show_all_activity,
            "filters": filters,
            "filtered_opportunities": filtered_opportunities,
            "selected": {"crm_stage": crm_stage, "intelligence_priority": intelligence_priority},
        },
    )


@app.get("/opportunity/{tracker_id}", response_class=HTMLResponse)
def opportunity_detail(request: Request, tracker_id: int, service: OpportunityCRMService = Depends(get_crm_service)):
    detail = service.get_opportunity_detail(tracker_id)
    if detail is None:
        return templates.TemplateResponse(
            request, "not_found.html", {"tracker_id": tracker_id, "active_nav": "dashboard"}, status_code=404,
        )
    detail["opportunity"]["intelligence_priority_reasons_list"] = _safe_json_list(detail["opportunity"].get("intelligence_priority_reasons"))
    detail["opportunity"]["package_gate_reasons_list"] = _safe_json_list(detail["opportunity"].get("package_gate_reasons"))
    return templates.TemplateResponse(
        request, "detail.html", {"detail": detail, "tracker_id": tracker_id, "active_nav": "dashboard"},
    )


def _register_placeholder_route(path: str, key: str, label: str, description: str) -> None:
    @app.get(path, response_class=HTMLResponse, name=f"placeholder_{key}")
    def _placeholder(request: Request):
        return templates.TemplateResponse(
            request, "placeholder.html",
            {"active_nav": key, "nav_label": label, "nav_description": description},
        )


for _path, (_key, _label, _description) in _PLACEHOLDER_PAGES.items():
    _register_placeholder_route(_path, _key, _label, _description)
