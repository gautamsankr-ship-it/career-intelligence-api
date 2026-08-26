"""Controlled ApplicationPackage-to-browser handoff with a hard no-submit boundary."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.models.application_execution import ApplicationExecutionResult
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_package_orchestrator import ApplicationPackageOrchestrator, TERMINAL
from app.services.application_route_resolver import ApplicationRouteResolver

EXECUTION_DIR = Path("app/data/application_executions")
MODES = {"INSPECT_ONLY", "AUTOFILL_PREVIEW", "PREPARE", "PROGRESS"}


class ApplicationExecutionOrchestrator:
    """Uses the existing browser service; this class has no submit operation."""
    def __init__(self, package_service=None, browser=None, execution_dir=EXECUTION_DIR):
        self.package_service = package_service or ApplicationPackageOrchestrator()
        self.history = self.package_service.history
        self.browser = browser or ApplicationBrowserService()
        self.routes = ApplicationRouteResolver()
        self.execution_dir = Path(execution_dir)

    def execute(self, tracker_id, mode="INSPECT_ONLY", *, headed=False, application_date=None):
        mode = mode.upper()
        if mode not in MODES: raise ValueError(f"Unsupported execution mode: {mode}")
        package = self.package_service.load(tracker_id)
        record = self.history.get_record_by_id(tracker_id)
        result = ApplicationExecutionResult(tracker_id=tracker_id, package_id=package.package_id if package else "", mode=mode)
        self._audit(result, "PACKAGE_LOADED")
        if not package or not record:
            result.status = "PACKAGE_REFRESH_REQUIRED"; return self._save(result)
        rejection = self._production_rejection(record)
        if rejection:
            result.status = rejection; return self._save(result)
        if package.vacancy_identity != self.package_service._identity(record):
            result.status = "PACKAGE_REFRESH_REQUIRED"; return self._save(result)
        if not package.resume_path or not Path(package.resume_path).is_file() or package.resume_vacancy_identity != package.vacancy_identity:
            result.status = "PACKAGE_REFRESH_REQUIRED"; return self._save(result)
        if package.cover_letter_status == "READY" and (not package.cover_letter_path or not Path(package.cover_letter_path).is_file()):
            result.status = "PACKAGE_REFRESH_REQUIRED"; return self._save(result)
        if package.readiness == "MANUAL_WEB_REQUIRED":
            self._try_safe_route_upgrade(package, record, result, headed)
            if package.readiness == "MANUAL_WEB_REQUIRED":
                result.status = "DIRECT_ROUTE_REQUIRED"; return self._save(result)
        if package.readiness not in {"READY_FOR_BROWSER_PREPARATION", "READY_FOR_APPLICATION"}:
            result.status = "PACKAGE_REFRESH_REQUIRED"; return self._save(result)
        if not package.application_url:
            result.status = "ROUTE_UNRESOLVED"; return self._save(result)
        result.application_url = package.application_url; result.portal = package.application_portal
        vacancy = {"company": package.company, "job_title": package.job_title, "market": package.market,
                   "application_url": package.application_url, "resume_path": package.resume_path,
                   "cover_letter_path": package.cover_letter_path}
        try:
            self._audit(result, "BROWSER_OPENED")
            if mode == "PROGRESS":
                progress=self.browser.progress_url(package.application_url, vacancy, tracker_id, headed, application_date)
                self._from_progress(result, progress)
                return self._save(result)
            plan = self._plan(package.application_url, vacancy, tracker_id, mode, headed, application_date)
        except Exception as exc:
            result.status = "BROWSER_ERROR"; self._audit(result, "PREPARATION_STOPPED", reason=type(exc).__name__); return self._save(result)
        self._from_plan(result, plan, mode)
        return self._save(result)

    def resume(self, execution_id, *, headed=False):
        path=self.execution_dir / f"{execution_id}.json"
        if not path.exists(): raise ValueError("Execution ID was not found.")
        prior=json.loads(path.read_text(encoding="utf-8"))
        result=self.execute(prior["tracker_id"], "PROGRESS", headed=headed)
        self._audit(result, "PROGRESSION_RESUMED", resumed_from=execution_id)
        return self._save(result)

    def ready(self): return self.package_service.ready()

    def _try_safe_route_upgrade(self, package, record, result, headed):
        source = record.get("source_listing_url") or record.get("job_url") or ""
        if not source: return
        try:
            route = self.browser.resolve_route_url(source, record, headed=headed)
        except Exception:
            return
        if route.resolution_status != "RESOLVED" or not route.application_url or route.application_url_type not in {"ATS_URL", "EMPLOYER_CAREER_URL", "DIRECT_APPLICATION_URL"}:
            return
        package.application_url, package.application_portal, package.route_confidence = route.application_url, route.portal, route.route_confidence
        package.portal_capability = "FULL_PREPARATION_SUPPORTED" if route.application_url_type == "ATS_URL" and route.portal in {"GREENHOUSE", "LEVER"} else "PARTIAL_AUTOMATION"
        package.readiness = "READY_FOR_BROWSER_PREPARATION" if package.portal_capability == "FULL_PREPARATION_SUPPORTED" else "READY_FOR_APPLICATION"
        self.package_service._save(package); self._audit(result, "ROUTE_RESOLVED")

    def _plan(self, url, vacancy, tracker_id, mode, headed, application_date):
        # ApplicationBrowserService owns field classification, Answer Vault mapping,
        # and safe document mapping. PREPARE is its only page-write path and it
        # never clicks controls; final submit remains unreachable here.
        if mode == "PREPARE":
            return self.browser.fill_preview_url(url, vacancy, tracker_id, headed, application_date)
        return self.browser.preview_url(url, vacancy, tracker_id, headed, application_date)

    def _from_plan(self, result, plan, mode):
        result.portal, result.application_url = plan.portal, plan.url
        result.pages_processed = plan.pages_navigated; result.fields_detected = len(plan.fields)
        result.fields_resolved = sum(field.action == "FILL" for field in plan.fields)
        result.fields_filled = plan.fields_filled if mode == "PREPARE" else 0
        result.manual_review_fields = sum(field.action == "REVIEW" for field in plan.fields)
        result.unknown_required_fields = sum(field.required and field.field_type != "FILE" and field.concept == "UNKNOWN" for field in plan.fields)
        result.resume_uploaded = any(item["kind"] == "RESUME" and item["action"] == "UPLOADED_IN_FILL_PREVIEW" for item in plan.document_requirements)
        result.cover_letter_uploaded = any(item["kind"] == "COVER_LETTER" and item["action"] == "UPLOADED_IN_FILL_PREVIEW" for item in plan.document_requirements)
        result.captcha_detected = plan.captcha == "CAPTCHA_REQUIRED"; result.auth_required = plan.authentication == "AUTH_REQUIRED"; result.mfa_required = plan.mfa == "MFA_REQUIRED"
        result.account_creation_required = plan.page_purpose == "ACCOUNT_CREATION"; result.final_submit_detected = plan.final_submit_detected
        self._audit(result, "FIELDS_DETECTED")
        for field in plan.fields:
            self._audit(result, "FIELD_RESOLVED" if field.action == "FILL" else "FIELD_REVIEW_REQUIRED", field=field.label, classification=field.action)
        if result.captcha_detected: result.status = "CAPTCHA_REQUIRED"; self._audit(result, "CAPTCHA_DETECTED"); return
        if result.auth_required: result.status = "AUTH_REQUIRED"; self._audit(result, "AUTH_REQUIRED"); return
        if result.mfa_required: result.status = "MFA_REQUIRED"; self._audit(result, "MFA_REQUIRED"); return
        if result.account_creation_required: result.status = "ACCOUNT_CREATION_REQUIRED"; return
        if plan.portal not in {"GREENHOUSE", "LEVER", "GENERIC"}: result.status = "UNSUPPORTED_PORTAL"; return
        if result.unknown_required_fields or any(field.required and field.action == "REVIEW" for field in plan.fields): result.status = "MANUAL_INPUT_REQUIRED"; return
        if any(item["required"] and item["action"] == "DOCUMENT_NOT_READY" for item in plan.document_requirements): result.status = "MANUAL_INPUT_REQUIRED"; return
        if result.final_submit_detected: self._audit(result, "FINAL_SUBMIT_DETECTED")
        # No click path exists in this orchestrator, irrespective of configuration.
        result.status = "PREPARED_FOR_FINAL_REVIEW" if mode == "PREPARE" else "READY_FOR_PREPARATION"

    def _from_progress(self, result, progress):
        plans=progress.get("plans", []); result.pages_processed=len(plans); result.navigation_actions=progress.get("navigation_actions", 0)
        for plan in plans:
            result.portal, result.application_url = plan.portal, plan.url
            result.fields_detected += len(plan.fields); result.fields_resolved += sum(f.action == "FILL" for f in plan.fields)
            result.fields_filled += plan.fields_filled; result.fields_skipped += sum(f.action == "SKIP" for f in plan.fields)
            result.manual_review_fields += sum(f.action == "REVIEW" for f in plan.fields)
            result.unknown_required_fields += sum(f.required and f.field_type != "FILE" and f.concept == "UNKNOWN" for f in plan.fields)
            result.resume_uploaded |= any(d["kind"] == "RESUME" and d["action"] == "UPLOADED_IN_FILL_PREVIEW" for d in plan.document_requirements)
            result.cover_letter_uploaded |= any(d["kind"] == "COVER_LETTER" and d["action"] == "UPLOADED_IN_FILL_PREVIEW" for d in plan.document_requirements)
            result.final_submit_detected |= plan.final_submit_detected
            self._audit(result, "PAGE_CLASSIFIED", purpose=plan.page_purpose)
        result.status=progress.get("status", "FAILED")
        if result.status == "PREPARED_FOR_FINAL_REVIEW" and result.manual_review_fields:
            result.status="FINAL_REVIEW_MANUAL_CONFIRMATION_REQUIRED"
        if result.final_submit_detected: self._audit(result, "FINAL_SUBMIT_DETECTED")
        self._audit(result, "PROGRESSION_PAUSED", reason=result.status)

    @staticmethod
    def _production_rejection(record):
        if record.get("validation_only") is True: return "VALIDATION_ONLY_REJECTED"
        if record.get("decision") != "AUTO_APPLY": return "NOT_APPLICATION_ELIGIBLE"
        if record.get("remote_eligibility") != "ELIGIBLE": return "NOT_APPLICATION_ELIGIBLE"
        if record.get("application_status") in TERMINAL or record.get("status") in TERMINAL: return "NOT_APPLICATION_ELIGIBLE"
        return ""

    def _save(self, result):
        self.execution_dir.mkdir(parents=True, exist_ok=True)
        (self.execution_dir / f"{result.execution_id}.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        return result
    @staticmethod
    def _audit(result, action, **details): result.audit.append({"at": datetime.now(timezone.utc).isoformat(), "action": action, **details})
