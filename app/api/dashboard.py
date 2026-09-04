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
    counts = service.funnel_counts()
    rates = service.conversion_rates(counts)
    pipeline = service.pipeline_counts()
    attention = service.needs_attention()
    filter_options = {field: _distinct_values(service, field) for field in _FILTER_FIELDS}
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "counts": counts, "rates": rates, "pipeline": pipeline,
            "attention": attention, "opportunities": opportunities, "filter_options": filter_options,
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
        return templates.TemplateResponse(request, "not_found.html", {"tracker_id": tracker_id}, status_code=404)
    detail["opportunity"]["intelligence_priority_reasons_list"] = _safe_json_list(detail["opportunity"].get("intelligence_priority_reasons"))
    detail["opportunity"]["package_gate_reasons_list"] = _safe_json_list(detail["opportunity"].get("package_gate_reasons"))
    return templates.TemplateResponse(request, "detail.html", {"detail": detail, "tracker_id": tracker_id})
