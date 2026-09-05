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

_FILTER_FIELDS = ("crm_stage", "intelligence_priority", "market", "source", "application_portal")

_PRIORITY_LABELS = {
    "A": "Priority Apply", "B": "Apply", "C": "Human Review", "D": "Watch", "E": "Reject",
}

# Phase 1 web app: navigation placeholders for every approved sidebar section
# beyond the Executive Dashboard. Each renders the shared shell with a short,
# honest "coming later" message -- no fabricated functionality.
_PLACEHOLDER_PAGES = {
    "/opportunities": (
        "opportunities", "Opportunities",
        "A dedicated, filterable Opportunities workspace is coming in a later phase. "
        "For now, use the filterable table on the Dashboard.",
    ),
    "/applications": (
        "applications", "Applications",
        "A dedicated Applications tracker is coming in a later phase. "
        "For now, filter the Dashboard by a stage of APPLIED or later.",
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


def _distinct_values(service: OpportunityCRMService, field: str) -> list[str]:
    return sorted({row["value"] for row in service.breakdown_by(field) if row["value"]})


def _safe_json_list(raw) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [str(raw)]
    return value if isinstance(value, list) else [str(value)]


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    crm_stage: str = "",
    intelligence_priority: str = "",
    market: str = "",
    source: str = "",
    application_portal: str = "",
    service: OpportunityCRMService = Depends(get_crm_service),
):
    filters = {
        field: value
        for field, value in (
            ("crm_stage", crm_stage), ("intelligence_priority", intelligence_priority), ("market", market),
            ("source", source), ("application_portal", application_portal),
        )
        if value
    }
    opportunities = service.list_opportunities(**filters)

    # Task 21.32's `funnel_counts()` walks literal per-stage
    # STAGE_TRANSITION/MIGRATED_STAGE events -- correct for
    # discovered/applied/acknowledged/responses/interviews/offers/hired
    # (each backed by its own dedicated table or an event always recorded
    # exactly at that stage), but it under-counts eligible/shortlisted/
    # prepared for any record that legitimately skipped straight past them
    # (e.g. a legacy-migrated APPLIED record with no discrete earlier-stage
    # event) -- the "0 eligible / 3 applied" presentation. Overriding just
    # those three with `cumulative_funnel_counts()`'s high-water-mark
    # computation fixes that without touching the accurate figures.
    counts = service.funnel_counts()
    cumulative_milestones = service.cumulative_funnel_counts()
    counts = {
        **counts,
        "eligible": cumulative_milestones["ELIGIBLE"],
        "shortlisted": cumulative_milestones["SHORTLISTED"],
        "prepared": cumulative_milestones["PREPARED"],
    }
    rates = service.conversion_rates(counts)
    pipeline = service.pipeline_counts()
    cumulative = {"DISCOVERED": counts["discovered"], **cumulative_milestones}
    response_quality = service.response_quality_counts()
    priority_mix = [row for row in service.breakdown_by("intelligence_priority") if row["value"]]
    attention = service.needs_attention()
    # Scoped to the active filter (if any) so a filtered view never surfaces
    # another, filtered-out opportunity's company/activity on the page; the
    # topbar's system-wide "last activity" is always unfiltered.
    recent_activity = service.recent_activity(limit=15, tracker_ids=[o["id"] for o in opportunities] if filters else None)
    latest_activity = service.recent_activity(limit=1)
    filter_options = {field: _distinct_values(service, field) for field in _FILTER_FIELDS}
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "active_nav": "dashboard",
            "last_activity_at": latest_activity[0]["occurred_at"] if latest_activity else None,
            "counts": counts, "rates": rates, "pipeline": pipeline,
            "pipeline_max": max(pipeline.values()) if pipeline else 0,
            "cumulative": cumulative,
            "cumulative_max": max(cumulative.values()) if cumulative else 0,
            "response_quality": response_quality,
            "priority_mix": priority_mix,
            "priority_labels": _PRIORITY_LABELS,
            "attention": attention, "opportunities": opportunities, "filter_options": filter_options,
            "recent_activity": recent_activity,
            "selected": {
                "crm_stage": crm_stage, "intelligence_priority": intelligence_priority, "market": market,
                "source": source, "application_portal": application_portal,
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
