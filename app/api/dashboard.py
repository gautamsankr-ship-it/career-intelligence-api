"""Task 21.33: minimal operational dashboard over the Application CRM
(Task 21.32's OpportunityCRMService). No new tracking database, no
duplicated business logic -- every number and every row comes straight from
the CRM's own read-model methods.

Launch: `python dashboard.py` from the repo root -> http://127.0.0.1:8000
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.services.application_eligibility_policy import intelligence_priority_gate
from app.services.opportunity_crm_service import OpportunityCRMService

app = FastAPI(title="Career Intelligence CRM Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates" / "dashboard"))

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
        reasons.append(f"Opportunity value assessed as {record['opportunity_value'].title()}.")
    if record.get("candidate_competitiveness") in ("VERY_STRONG", "STRONG", "COMPETITIVE"):
        reasons.append(f"Candidate competitiveness assessed as {record['candidate_competitiveness'].replace('_', ' ').title()}.")
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
        risks.append(f"Candidate competitiveness assessed as {record['candidate_competitiveness'].replace('_', ' ').title()}.")
    if record.get("intelligence_priority") == "C":
        risks.append("Flagged by the intelligence engine for human review before proceeding.")
    if record.get("intelligence_priority") == "E":
        risks.append("Rejected by the intelligence engine.")
    return risks


# Phase 1 web app: navigation placeholders for every approved sidebar section
# beyond the Executive Dashboard and (Phase 2) Opportunities. Each renders
# the shared shell with a short, honest "coming later" message -- no
# fabricated functionality.
_PLACEHOLDER_PAGES = {
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
            "show_all_attention": show_all_attention,
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


def _register_placeholder_route(path: str, key: str, label: str, description: str) -> None:
    @app.get(path, response_class=HTMLResponse, name=f"placeholder_{key}")
    def _placeholder(request: Request):
        return templates.TemplateResponse(
            request, "placeholder.html",
            {"active_nav": key, "nav_label": label, "nav_description": description},
        )


for _path, (_key, _label, _description) in _PLACEHOLDER_PAGES.items():
    _register_placeholder_route(_path, _key, _label, _description)
