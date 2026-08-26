"""Isolated, non-submitting public Greenhouse/Lever validation support."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from uuid import uuid4

from app.config import APPLICATION_AUTO_SUBMIT, APPLICATION_BROWSER_TIMEOUT_MS, APPLICATION_DRY_RUN
from app.services.application_browser_service import ApplicationBrowserService, FINAL_BUTTON, SAFE_BUTTON
from app.services.application_route_resolver import ApplicationRouteResolver
from app.services.portal_evidence import detect_portal_evidence

VALIDATION_DIR = Path("app/data/application_validation_sessions")
SYNTHETIC = {
    "FIRST_NAME": "Validation", "LAST_NAME": "Candidate", "EMAIL_ADDRESS": "validation@example.test",
    "PHONE_NUMBER": "+10000000000", "CURRENT_LOCATION_COUNTRY": "Nepal", "NOTICE_PERIOD": "7 calendar days",
}
SUPPORTED = {"GREENHOUSE", "LEVER"}
ALLOWED_DOCUMENTS = {".pdf", ".docx", ".txt"}


class LiveValidationService:
    """A separate mode: no tracker ID, history access, or submit operation exists."""
    def __init__(self, browser=None, session_dir=VALIDATION_DIR):
        self.browser = browser or ApplicationBrowserService()
        self.routes = ApplicationRouteResolver()
        self.session_dir = Path(session_dir); self.session_dir.mkdir(parents=True, exist_ok=True)

    def validate_html(self, html, url, market="united_kingdom", *, use_real_profile=False, fill=False,
                      allow_safe_navigation=False, test_resume=None, test_cover_letter=None, application_date=None,
                      frame_html=None, frame_url=None):
        self._validate_document(test_resume); self._validate_document(test_cover_letter)
        route = self.routes.resolve({"application_url": url, "job_url": url})
        if route.application_url_type not in {"ATS_URL", "EMPLOYER_CAREER_URL", "DIRECT_APPLICATION_URL"}:
            raise ValueError("Live validation requires a direct ATS or employer application URL.")
        evidence=detect_portal_evidence(url, html)
        surface_html=frame_html or html; surface_url=frame_url or url
        surface="IFRAME" if frame_html else "MAIN_DOCUMENT"
        plan = self.browser.preview_html(surface_html, surface_url, {"market": market, "application_url": surface_url}, application_date=application_date, route=route, persist=False)
        if evidence.portal == "GREENHOUSE": plan.portal="GREENHOUSE"
        session = self._new(url, market, plan.portal, use_real_profile)
        session.update({"final_url": self._safe_persisted_url(surface_url, url), "wrapper_url": url if evidence.wrapper_detected else "", "page_purpose": plan.page_purpose, "fields_detected": len(plan.fields),
                        "final_submit_detected": plan.final_submit_detected, "safe_navigation_detected": plan.safe_navigation_detected})
        session.update({"portal_evidence":evidence.to_dict(), "wrapper_detected":evidence.wrapper_detected, "application_surface":surface})
        if plan.portal not in SUPPORTED:
            session["state"] = "PORTAL_LIMITED"
        elif plan.page_purpose != "APPLICATION_FORM":
            session["state"] = {"LOGIN":"AUTH_REQUIRED", "MFA":"MFA_REQUIRED", "CAPTCHA":"CAPTCHA_REQUIRED", "ACCOUNT_CREATION":"ACCOUNT_CREATION_REQUIRED", "APPLICATION_REVIEW":"READY_FOR_FINAL_REVIEW", "APPLICATION_SUCCESS":"UNEXPECTED_APPLICATION_SUCCESS"}.get(plan.page_purpose, "FORM_NOT_FOUND")
            if evidence.portal == "GREENHOUSE" and evidence.wrapper_detected and plan.page_purpose == "NON_APPLICATION":
                session["state"]="GREENHOUSE_WRAPPER_FORM_NOT_FOUND"
        else:
            self._apply_validation_policy(plan, use_real_profile, fill)
            session["fields_filled"] = sum(f.action == "FILL" for f in plan.fields) if fill else 0
            session["exceptions"] = [self._exception(f) for f in plan.fields if f.action == "REVIEW"]
            session["state"] = "READY_FOR_FINAL_REVIEW" if plan.page_purpose == "APPLICATION_REVIEW" else ("MANUAL_INPUT_REQUIRED" if any(f.required and f.action == "REVIEW" for f in plan.fields) else "INSPECTED")
        session["field_snapshot"] = [{"label":f.label,"type":f.field_type,"required":f.required,"concept":f.concept,"action":f.action,"reason":f.reason} for f in plan.fields]
        session["documents"] = self._documents(test_resume, test_cover_letter, plan, fill)
        session["application_submitted"] = False; session["tracker_updated"] = False; session["gmail_sent"] = False
        self.save(session); return session, plan

    async def validate_url(self, url, market="united_kingdom", *, headed=True, use_real_profile=False, fill=False,
                           allow_safe_navigation=False, test_resume=None, test_cover_letter=None, application_date=None,
                           max_pages=5, pause_seconds=0):
        if not APPLICATION_DRY_RUN or APPLICATION_AUTO_SUBMIT: raise RuntimeError("Application submission safety is not enabled.")
        self._validate_document(test_resume); self._validate_document(test_cover_letter)
        self.browser.validate_url(url)
        initial = self.routes.resolve({"application_url": url, "job_url": url})
        if initial.application_url_type not in {"ATS_URL", "EMPLOYER_CAREER_URL", "DIRECT_APPLICATION_URL"}: raise ValueError("Live validation rejects job-board, tracking, and non-application URLs.")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc: raise RuntimeError("Playwright and Chromium are required for live validation.") from exc
        session = self._new(url, market, initial.portal or "UNKNOWN", use_real_profile)
        session["redirect_chain"] = [url]
        try:
            async with async_playwright() as api:
                browser = await api.chromium.launch(headless=not headed); context = await browser.new_context(); page = await context.new_page()
                try:
                    for page_no in range(1, max_pages + 1):
                        if page_no == 1:
                            await page.goto(url, wait_until="domcontentloaded", timeout=APPLICATION_BROWSER_TIMEOUT_MS)
                            try: await page.wait_for_selector("iframe, form, input, textarea, select", timeout=5000)
                            except Exception: pass
                        html = await page.content(); current = page.url; session["redirect_chain"].append(current) if current != session["redirect_chain"][-1] else None
                        surface_html, surface_url, surface = await self._locate_surface(page, current, html)
                        current_session, plan = self.validate_html(html, current, market, use_real_profile=use_real_profile, fill=fill, allow_safe_navigation=allow_safe_navigation, test_resume=test_resume, test_cover_letter=test_cover_letter, application_date=application_date, frame_html=surface_html if surface == "IFRAME" else None, frame_url=surface_url if surface == "IFRAME" else None)
                        if surface == "RESOLVED_ATS_URL":
                            plan=self.browser.preview_html(surface_html, surface_url, {"market":market,"application_url":surface_url}, application_date=application_date, persist=False)
                            current_session["portal"]="GREENHOUSE"; current_session["final_url"]=surface_url; current_session["application_surface"]=surface
                            current_session["fields_detected"]=len(plan.fields); current_session["final_submit_detected"]=plan.final_submit_detected
                            if plan.page_purpose == "APPLICATION_FORM": current_session["state"]="INSPECTED"
                        # validate_html is also a useful deterministic API.  In
                        # browser mode merge its diagnostics into this one root
                        # session, rather than creating a lifecycle record per page.
                        transient_id=current_session["session_id"]
                        try: (self.session_dir / f"{transient_id}.json").unlink()
                        except FileNotFoundError: pass
                        root_id=session["session_id"]
                        current_session["fields_filled"] = 0
                        session.update(current_session); session["session_id"] = root_id; session["pages_processed"] = page_no
                        fingerprint=hashlib.sha256((current + "|" + plan.portal + "|" + plan.page_purpose + "|" + "|".join(f"{f.concept}:{f.field_type}" for f in plan.fields)).encode()).hexdigest()
                        if fingerprint in session.setdefault("page_fingerprints", []):
                            session["state"]="LOOP_DETECTED"; break
                        session["page_fingerprints"].append(fingerprint)
                        if plan.page_purpose == "APPLICATION_REVIEW": session["final_review_detected"] = True
                        if fill and plan.page_purpose == "APPLICATION_FORM" and plan.portal in SUPPORTED:
                            await self._fill_page(page, plan, use_real_profile, test_resume, test_cover_letter, session)
                        self.save(session)
                        if not allow_safe_navigation or session["state"] != "INSPECTED" or not plan.safe_navigation_detected: break
                        button = page.get_by_role("button", name=SAFE_BUTTON)
                        if await button.count() != 1: session["state"]="NAVIGATION_UNCERTAIN"; break
                        await button.click(); session["navigation_actions"].append("SAFE_NAVIGATION_CLICKED"); await page.wait_for_load_state("domcontentloaded", timeout=APPLICATION_BROWSER_TIMEOUT_MS)
                    if pause_seconds and headed: await page.wait_for_timeout(min(max(pause_seconds, 0), 300) * 1000)
                finally:
                    await context.close(); await browser.close()
        except KeyboardInterrupt: session["state"]="INTERRUPTED"
        except Exception as exc:
            session["state"]="TIMEOUT" if "timeout" in type(exc).__name__.lower() else "BROWSER_ERROR"
            session["failure_reason"]=type(exc).__name__
        session["updated_at"] = self._now(); self.save(session); return session

    async def _locate_surface(self, page, url, html):
        """Find a trusted Greenhouse form surface without application progression."""
        evidence=detect_portal_evidence(url, html)
        if evidence.portal != "GREENHOUSE" or not evidence.wrapper_detected: return html, url, "MAIN_DOCUMENT"
        for frame in page.frames:
            if frame == page.main_frame: continue
            frame_url=frame.url
            if detect_portal_evidence(frame_url).portal == "GREENHOUSE":
                try: return await frame.content(), frame_url, "IFRAME"
                except Exception: return html, url, "NOT_FOUND"
        entry=self._greenhouse_entry_url(url, html)
        if entry:
            await page.goto(entry, wait_until="domcontentloaded", timeout=APPLICATION_BROWSER_TIMEOUT_MS)
            return await page.content(), page.url, "RESOLVED_ATS_URL"
        return html, url, "NOT_FOUND"

    @staticmethod
    def _greenhouse_entry_url(base_url, html):
        for href in re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"']", html, re.I):
            candidate=urljoin(base_url, href)
            if detect_portal_evidence(candidate).portal == "GREENHOUSE": return candidate
        return ""

    @staticmethod
    def _safe_persisted_url(candidate, stable):
        """Never retain temporary Greenhouse validity/token URLs in diagnostics."""
        return stable if re.search(r"(?:validitytoken|[?&]token=)", candidate, re.I) else candidate

    def _apply_validation_policy(self, plan, use_real_profile, fill):
        for field in plan.fields:
            if field.concept in {"LEGAL_DECLARATION","VOLUNTARY_DEMOGRAPHIC","CRIMINAL_HISTORY","SECURITY_CLEARANCE","CONFLICT_OF_INTEREST"}:
                field.action="REVIEW"; field.reason="Legal, sensitive, or voluntary field remains untouched."
            elif not use_real_profile:
                label = field.label.lower()
                if re.search(r"\bfirst\s*name\b", label): field.answer="Validation"; field.action="FILL" if fill else "REVIEW"; field.reason="Synthetic validation value."
                elif re.search(r"\blast\s*name\b", label): field.answer="Candidate"; field.action="FILL" if fill else "REVIEW"; field.reason="Synthetic validation value."
                if field.concept in SYNTHETIC: field.answer=SYNTHETIC[field.concept]; field.action="FILL" if fill else "REVIEW"; field.reason="Synthetic validation value."
                elif field.concept in {"SPONSORSHIP_UK","SPONSORSHIP_US","SPONSORSHIP_AUSTRALIA","WORK_AUTHORIZATION_UK","WORK_AUTHORIZATION_US","WORK_AUTHORIZATION_AUSTRALIA","EXPECTED_SALARY"}: field.action="REVIEW"; field.reason="Contextual answer requires --use-real-profile."
            elif not fill and field.action == "FILL": field.action="REVIEW"; field.reason="Inspect-only validation does not fill fields."

    async def _fill_page(self, page, plan, real, resume, cover, session):
        for field in plan.fields:
            if field.action != "FILL" or field.answer is None: continue
            try:
                locator=page.locator(f"#{field.field_id}")
                if await locator.count() != 1: field.action="REVIEW"; continue
                value=str(field.answer)
                if field.field_type in {"SELECT","MULTISELECT"}: await locator.select_option(label=value)
                elif field.field_type in {"RADIO","BOOLEAN","CHECKBOX"}:
                    if value.upper() in {"YES","TRUE"}: await locator.check()
                    else: field.action="REVIEW"; continue
                else: await locator.fill(value)
                session["fields_filled"] += 1
            except Exception: field.action="REVIEW"; field.reason="Exact live selector/value mapping was unavailable."
        for doc in session.get("documents", []):
            if doc["action"] != "READY_FOR_UPLOAD": continue
            try:
                await page.locator(f"#{doc['field_id']}").set_input_files(doc["path"]); doc["action"]="UPLOADED"; session["documents_uploaded"] += 1
            except Exception: doc["action"]="DOCUMENT_NOT_READY"

    def _documents(self, resume, cover, plan, fill):
        supplied={"RESUME":resume,"COVER_LETTER":cover}; result=[]
        for requirement in plan.document_requirements:
            path=supplied.get(requirement["kind"])
            action="READY_FOR_UPLOAD" if fill and path else ("DOCUMENT_NOT_READY" if requirement["required"] else "SKIP")
            result.append({"field_id":requirement["field_id"],"kind":requirement["kind"],"required":requirement["required"],"path":str(path or ""),"action":action})
        return result
    @staticmethod
    def _exception(field): return {"field_label":field.label,"concept":field.concept,"required":field.required,"exception_type":"LEGAL_DECLARATION_EXCEPTION" if field.concept=="LEGAL_DECLARATION" else "MANUAL_REQUIRED","reason":field.reason}
    @staticmethod
    def _validate_document(path):
        if path and (not Path(path).is_file() or Path(path).suffix.lower() not in ALLOWED_DOCUMENTS): raise ValueError("Test document must be an existing .pdf, .docx, or .txt file.")
    def _new(self, url, market, portal, real): return {"session_id":uuid4().hex,"mode":"LIVE_VALIDATION","tracker_id":None,"application_lifecycle_enabled":False,"submission_enabled":False,"source_url":url,"final_url":url,"portal":portal,"market":market,"profile":"REAL_PROFILE" if real else "SYNTHETIC","started_at":self._now(),"updated_at":self._now(),"state":"OPENING","pages_processed":0,"fields_detected":0,"fields_filled":0,"documents_uploaded":0,"exceptions":[],"navigation_actions":[],"final_review_detected":False,"final_submit_detected":False}
    def save(self, session): (self.session_dir / f"{session['session_id']}.json").write_text(json.dumps(session, indent=2), encoding="utf-8")
    def load(self, session_id): return json.loads((self.session_dir / f"{session_id}.json").read_text(encoding="utf-8"))
    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat()
