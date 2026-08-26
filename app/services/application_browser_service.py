"""Safe, read-only application-form preview service.

There is intentionally no submit, click, upload, credential, cookie, or account
creation API in this service. Playwright is used only for isolated navigation,
HTML inspection, and optional diagnostics.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlparse

from app.config import APPLICATION_AUTO_SUBMIT, APPLICATION_BROWSER_TIMEOUT_MS, APPLICATION_DRY_RUN, APPLICATION_PREVIEW_FOLDER
from app.models.application_browser import ApplicationField, ApplicationPlan
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_route_resolver import ApplicationRouteResolver
from app.services.portal_evidence import detect_portal_evidence

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page


class ApplicationSurface(Protocol):
    """The common Playwright operations used by an application document.

    Both Page and Frame implement this small locator/content contract.  Keeping
    it structural lets the submission pipeline remain one implementation when
    a trusted application surface is eventually supplied as an iframe.
    """
    @property
    def url(self) -> str: ...
    async def content(self) -> str: ...
    def locator(self, selector: str): ...
    def get_by_role(self, role: str, *, name: str | None = None, exact: bool = False): ...
    def is_detached(self) -> bool: ...


FINAL_BUTTON = re.compile(r"\b(submit( application)?|send application|finish( application)?|complete application|apply now|apply)\b", re.I)
SAFE_BUTTON = re.compile(r"^(next|continue|save and continue|next step|continue application|review)$", re.I)


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.fields=[]; self.labels={}; self._label_for=None; self._label=[]; self._current=None; self._option=[]; self.buttons=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag == "label": self._label_for=a.get("for"); self._label=[]
        elif tag in {"input", "textarea", "select"}:
            if tag == "input" and a.get("type", "").lower() == "hidden": return
            field={"id": a.get("id") or a.get("name") or f"field_{len(self.fields)+1}", "type": ("TEXTAREA" if tag == "textarea" else "SELECT" if tag == "select" else a.get("type", "TEXT").upper()), "attrs": a, "options": []}
            self.fields.append(field); self._current=field if tag in {"textarea", "select"} else None
        elif tag == "option" and self._current: self._option=[]
        elif tag in {"button"}: self._current={"button": True, "attrs": a}; self._option=[]
    def handle_data(self, data):
        if self._label_for is not None: self._label.append(data)
        elif self._current is not None: self._option.append(data)
    def handle_endtag(self, tag):
        if tag == "label": self.labels[self._label_for]=" ".join(self._label).strip(); self._label_for=None
        elif tag == "option" and self._current and not self._current.get("button"): self._current["options"].append(" ".join(self._option).strip())
        elif tag == "button" and self._current and self._current.get("button"):
            self.buttons.append((" ".join(self._option).strip(), self._current["attrs"])); self._current=None
        elif tag in {"textarea", "select"}: self._current=None


class ApplicationPortalAdapter:
    portal = "GENERIC"
    @classmethod
    def detect(cls, url: str, html: str) -> bool: return False


class GreenhouseAdapter(ApplicationPortalAdapter):
    portal = "GREENHOUSE"
    @classmethod
    def detect(cls, url, html): return "greenhouse.io" in url.lower() or "application_form" in html.lower() and "greenhouse" in html.lower()


class LeverAdapter(ApplicationPortalAdapter):
    portal = "LEVER"
    @classmethod
    def detect(cls, url, html): return "lever.co" in url.lower() or "lever-application" in html.lower()


class WorkdayAdapter(ApplicationPortalAdapter):
    portal = "WORKDAY"
    @classmethod
    def detect(cls, url, html): return "workday" in url.lower() or "workday" in html.lower()

class SmartRecruitersAdapter(ApplicationPortalAdapter):
    portal = "SMARTRECRUITERS"
    @classmethod
    def detect(cls, url, html): return "smartrecruiters" in url.lower() or "smartrecruiters" in html.lower()

class SuccessFactorsAdapter(ApplicationPortalAdapter):
    portal = "SUCCESSFACTORS"
    @classmethod
    def detect(cls, url, html): return "successfactors" in url.lower() or "successfactors" in html.lower()

class OracleAdapter(ApplicationPortalAdapter):
    portal = "ORACLE"
    @classmethod
    def detect(cls, url, html): return "oraclecloud" in url.lower() or "oracle recruiting" in html.lower()

class AshbyAdapter(ApplicationPortalAdapter):
    portal = "ASHBY"
    @classmethod
    def detect(cls, url, html): return "ashbyhq.com" in url.lower() or "ashby" in html.lower()


class GenericApplicationAdapter(ApplicationPortalAdapter):
    portal = "GENERIC"
    @classmethod
    def detect(cls, url, html): return "<form" in html.lower()


ADAPTERS = (GreenhouseAdapter, LeverAdapter, WorkdayAdapter, SmartRecruitersAdapter, SuccessFactorsAdapter, OracleAdapter, AshbyAdapter, GenericApplicationAdapter)


class ApplicationBrowserService:
    def __init__(self, answer_engine: ApplicationAnswerEngine | None = None, preview_folder: str | Path = APPLICATION_PREVIEW_FOLDER, package_service=None, review_service=None, execution_lookup=None, allowed_hosts=None, network_events=None):
        self.answer_engine = answer_engine or ApplicationAnswerEngine()
        self.preview_folder = Path(preview_folder)
        self.route_resolver = ApplicationRouteResolver()
        self.package_service = package_service
        self.review_service = review_service
        # Tests may supply an isolated execution reader.  Production continues
        # to use the review service's authoritative execution store.
        self.execution_lookup = execution_lookup
        self.allowed_hosts = set(allowed_hosts) if allowed_hosts else None
        self.network_events = network_events

    @staticmethod
    def validate_url(url: str) -> str:
        if urlparse(url).scheme not in {"http", "https"}: raise ValueError("Only http(s) application URLs are allowed.")
        return url

    @staticmethod
    def detect_portal(url: str, html: str) -> str:
        evidence=detect_portal_evidence(url, html)
        if evidence.portal != "UNKNOWN": return evidence.portal
        return next((adapter.portal for adapter in ADAPTERS if adapter.detect(url, html)), "UNKNOWN")

    def page_purpose(self, url: str, html: str) -> str:
        body = re.sub(r"\s+", " ", html).lower(); host = urlparse(url).netloc.lower()
        if re.search(r"captcha|recaptcha|hcaptcha", body): return "CAPTCHA"
        if re.search(r"one.time password|verification code|multi.factor|\bmfa\b", body): return "MFA"
        if re.search(r"create (an )?account|sign up", body): return "ACCOUNT_CREATION"
        if re.search(r"sign in|log in|login", body): return "LOGIN"
        if any(board in host for board in ("linkedin.com", "indeed.com")) and "/jobs/" in urlparse(url).path.lower(): return "JOB_LISTING"
        if re.search(r"application (submitted|complete|success|received)|thank you for applying", body): return "APPLICATION_SUCCESS"
        if re.search(r"review (your )?application", body): return "APPLICATION_REVIEW"
        if "<form" in body: return "APPLICATION_FORM"
        return "NON_APPLICATION"

    def preview_html(self, html: str, url: str = "https://example.test/application", vacancy: Any | None = None, tracker_id: int | None = None, application_date: str | None = None, route=None, persist: bool = True) -> ApplicationPlan:
        if not APPLICATION_DRY_RUN or APPLICATION_AUTO_SUBMIT:
            raise RuntimeError("Application preview safety configuration is not enabled.")
        self.validate_url(url)
        portal = self.detect_portal(url, html)
        vacancy = vacancy or {}; market = self._value(vacancy, "market") or ""
        route = route or self.route_resolver.resolve(vacancy)
        plan = ApplicationPlan(portal, url, tracker_id, self._value(vacancy, "company") or "", self._value(vacancy, "job_title") or self._value(vacancy, "title") or "", market, route=route.to_dict())
        body = re.sub(r"\s+", " ", html).lower()
        plan.page_purpose = self.page_purpose(url, html)
        # Preserve the final-submit boundary even on a review page that is not
        # otherwise parsed as an application form. Strip tags first so a
        # routine type="submit" attribute on an intermediate Continue/Next
        # control can never masquerade as a final submit; only genuine
        # visible text can.
        visible_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).lower()
        plan.final_submit_detected = bool(FINAL_BUTTON.search(visible_text))
        plan.authentication = "AUTH_REQUIRED" if re.search(r"sign in|log in|login", body) else "NO"
        plan.mfa = "MFA_REQUIRED" if re.search(r"one.time password|verification code|multi.factor|\bmfa\b", body) else "NO"
        plan.captcha = "CAPTCHA_REQUIRED" if re.search(r"captcha|recaptcha|hcaptcha", body) else "NO"
        if plan.page_purpose != "APPLICATION_FORM":
            if plan.page_purpose == "LOGIN": plan.readiness = "AUTH_REQUIRED"
            elif plan.page_purpose == "MFA": plan.readiness = "MFA_REQUIRED"
            elif plan.page_purpose == "CAPTCHA": plan.readiness = "CAPTCHA_REQUIRED"
            elif plan.page_purpose == "APPLICATION_REVIEW": plan.readiness = "READY_FOR_FINAL_REVIEW"
            elif route.resolution_status != "RESOLVED": plan.readiness = route.resolution_status
            else: plan.readiness = "FORM_NOT_FOUND"
            if persist: self._persist(plan)
            return plan
        parser = _FormParser(); parser.feed(html)
        plan.final_submit_detected = plan.final_submit_detected or any(FINAL_BUTTON.search(text) for text, _ in parser.buttons)
        plan.safe_navigation_detected = any(SAFE_BUTTON.match(text.strip()) for text, _ in parser.buttons)
        for raw in parser.fields:
            attrs, raw_type = raw["attrs"], raw["type"]
            field_type = self._field_type(raw_type)
            label = parser.labels.get(attrs.get("id"), "") or attrs.get("aria-label") or attrs.get("placeholder") or attrs.get("name") or ""
            required = "required" in attrs or attrs.get("aria-required") == "true"
            if field_type == "FILE":
                kind = "COVER_LETTER" if re.search(r"cover", label, re.I) else "RESUME" if re.search(r"resume|cv", label, re.I) else "SUPPORTING_DOCUMENT"
                document = self._document_path(vacancy, kind)
                action = "READY_FOR_UPLOAD" if document else "DOCUMENT_NOT_READY" if required else "SKIP"
                plan.document_requirements.append({"field_id": raw["id"], "label": label, "kind": kind, "required": required, "path": str(document) if document else "", "action": action})
                plan.fields.append(ApplicationField(raw["id"], portal, label, label, field_type, required, action=action, reason="Upload is limited to an exact existing vacancy document.")); continue
            decision = self.answer_engine.resolve(label, field_type=field_type, choices=raw["options"], market=market, vacancy=vacancy, application_date=application_date)
            decision = self.answer_engine.fit_character_limit(decision, self._int(attrs.get("maxlength")))
            action = "FILL" if not decision.manual_review and decision.confidence == "HIGH" else ("SKIP" if not required and decision.concept == "UNKNOWN" else "REVIEW")
            plan.fields.append(ApplicationField(raw["id"], portal, label, label, field_type, required, raw["options"], self._int(attrs.get("maxlength")), action=action, concept=decision.concept, answer=decision.answer, confidence=decision.confidence, answer_source=decision.answer_source, reason=decision.reason))
        if plan.authentication != "NO": plan.readiness = "AUTH_REQUIRED"
        elif plan.mfa != "NO": plan.readiness = "MFA_REQUIRED"
        elif plan.captcha != "NO": plan.readiness = "CAPTCHA_REQUIRED"
        elif any(f.action == "REVIEW" and f.required for f in plan.fields) or any(d["required"] and d["action"] == "DOCUMENT_NOT_READY" for d in plan.document_requirements): plan.readiness = "MANUAL_INPUT_REQUIRED"
        elif portal in {"WORKDAY", "SMARTRECRUITERS", "SUCCESSFACTORS", "ORACLE", "ASHBY"}: plan.readiness = "UNSUPPORTED_PORTAL"
        if persist: self._persist(plan)
        return plan

    def preview_url(self, url: str, vacancy: Any | None = None, tracker_id: int | None = None, headed: bool = False, application_date: str | None = None) -> ApplicationPlan:
        """Use a new isolated Playwright context for navigation and inspection only."""
        return asyncio.run(self._preview_url(url, vacancy, tracker_id, headed, application_date, False, 0))

    def resolve_route_url(self, url: str, vacancy: Any | None = None, headed: bool = False):
        return asyncio.run(self._resolve_route_url(url, vacancy, headed))

    async def _resolve_route_url(self, url, vacancy, headed):
        self.validate_url(url)
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc: raise RuntimeError("Playwright is required. Install project dependencies and Chromium before live route resolution.") from exc
        async with async_playwright() as api:
            browser = await api.chromium.launch(headless=not headed); context = await browser.new_context(); page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=APPLICATION_BROWSER_TIMEOUT_MS)
            route = self.route_resolver.resolve(vacancy or {"job_url": url}, await page.content(), page.url)
            if route.resolution_status == "RESOLVED" and route.application_url:
                await page.goto(route.application_url, wait_until="domcontentloaded", timeout=APPLICATION_BROWSER_TIMEOUT_MS)
                route.application_url = page.url; route.portal = self.detect_portal(page.url, await page.content())
                route.redirect_chain = [url, page.url]
            await context.close(); await browser.close(); return route

    def fill_preview_url(self, url: str, vacancy: Any | None = None, tracker_id: int | None = None, headed: bool = False, application_date: str | None = None, pause_seconds: int = 0) -> ApplicationPlan:
        return asyncio.run(self._preview_url(url, vacancy, tracker_id, headed, application_date, True, pause_seconds))

    def progress_url(self, url: str, vacancy: Any | None = None, tracker_id: int | None = None, headed: bool = False, application_date: str | None = None, max_pages: int = 6, max_navigation_actions: int = 5):
        """Fill/upload and click only one unambiguous non-final intermediate control."""
        return asyncio.run(self._progress_url(url, vacancy, tracker_id, headed, application_date, max_pages, max_navigation_actions))

    def submit_final_url(self, context):
        """The sole final-click API; requires a verified SubmissionContext."""
        from app.models.submission import SubmissionContext
        if not isinstance(context, SubmissionContext) or context.portal not in {"GREENHOUSE", "LEVER"}:
            return {"outcome":"SUBMISSION_FAILED","signals":["SUBMISSION_PORTAL_UNSUPPORTED"]}
        return asyncio.run(self._submit_final_url(context, allow_final_click=True))

    def reconcile_final_url(self, context):
        """Reconstruct and verify a final review without any final click."""
        from app.models.submission import SubmissionContext
        if not isinstance(context, SubmissionContext) or context.portal not in {"GREENHOUSE", "LEVER"}:
            return {"outcome":"SUBMISSION_FAILED","signals":["SUBMISSION_PORTAL_UNSUPPORTED"]}
        return asyncio.run(self._submit_final_url(context, allow_final_click=False))

    @staticmethod
    def _compare_submission_identity(context, package, review, execution):
        """Return non-sensitive identity mismatch names for fail-closed transport."""
        mismatches=[]
        checks=(
            ("TRACKER_ID", getattr(package, "tracker_id", None), context.tracker_id),
            ("PACKAGE_ID", getattr(package, "package_id", None), context.package_id),
            ("APPLICATION_URL", getattr(package, "application_url", None), context.application_url),
            ("PORTAL", getattr(package, "application_portal", None), context.portal),
            ("REVIEW_TRACKER_ID", getattr(review, "tracker_id", None), context.tracker_id),
            ("REVIEW_PACKAGE_ID", getattr(review, "package_id", None), context.package_id),
            ("EXECUTION_ID", getattr(review, "execution_id", None), context.execution_id),
            ("FINGERPRINT", getattr(review, "fingerprint", None), context.authorized_fingerprint),
            ("REVIEW_APPLICATION_URL", getattr(review, "application_url", None), context.application_url),
            ("REVIEW_PORTAL", getattr(review, "application_portal", None), context.portal),
            ("EXECUTION_TRACKER_ID", (execution or {}).get("tracker_id"), context.tracker_id),
            ("EXECUTION_PACKAGE_ID", (execution or {}).get("package_id"), context.package_id),
            ("EXECUTION_RECORD_ID", (execution or {}).get("execution_id"), context.execution_id),
            ("EXECUTION_APPLICATION_URL", (execution or {}).get("application_url"), context.application_url),
            ("EXECUTION_PORTAL", (execution or {}).get("portal"), context.portal),
        )
        for label,actual,expected in checks:
            if actual != expected:
                # Keep public diagnostics compact and never include values.
                normalized = "MISMATCH_EXECUTION_ID" if label == "EXECUTION_RECORD_ID" else "MISMATCH_"+label
                if normalized not in mismatches: mismatches.append(normalized)
        return {"matches":not mismatches,"mismatches":mismatches}

    async def _submit_final_url(self, context, allow_final_click=True):
        from playwright.async_api import async_playwright
        from app.services.application_package_orchestrator import ApplicationPackageOrchestrator
        from app.services.final_review_service import FinalReviewService
        clicked=False; network_event_count=len(self.network_events) if self.network_events is not None else 0
        try:
            packages=self.package_service or ApplicationPackageOrchestrator(); package=packages.load(context.tracker_id)
            reviews=self.review_service or FinalReviewService(package_service=packages); review=reviews.show(context.review_id)
            execution=(self.execution_lookup(context.execution_id) if self.execution_lookup else reviews._execution(context.execution_id))
            comparison=self._compare_submission_identity(context,package,review,execution) if package and review and execution else {"matches":False,"mismatches":["MISMATCH_REHYDRATION"]}
            if not comparison["matches"]:
                if self.package_service is None: packages.history.close()
                return {"outcome":"SUBMISSION_FAILED","signals":["APPLICATION_CHANGED_AFTER_REVIEW",*comparison["mismatches"]]}
            if package.resume_vacancy_identity != package.vacancy_identity:
                return {"outcome":"SUBMISSION_FAILED","signals":["DOCUMENT_NOT_READY"]}
            vacancy={"company":package.company,"job_title":package.job_title,"market":package.market,"application_url":package.application_url,"resume_path":package.resume_path,"cover_letter_path":package.cover_letter_path}
            async with async_playwright() as api:
                browser=await api.chromium.launch(headless=False); ctx=await browser.new_context()
                if self.allowed_hosts:
                    async def guard(route):
                        target=route.request.url; parsed=urlparse(target)
                        if parsed.scheme in {"http","https"} and parsed.hostname not in self.allowed_hosts:
                            if self.network_events is not None: self.network_events.append({"host":parsed.hostname or "","path":parsed.path,"aborted":True})
                            await route.abort(); return
                        await route.continue_()
                    await ctx.route("**/*", guard)
                page=await ctx.new_page()
                if self.allowed_hosts:
                    # Page routing covers document-redirect navigation in
                    # Chromium builds where context routing is not surfaced.
                    await page.route("**/*", guard)
                    def observe_request(request):
                        parsed=urlparse(request.url)
                        if parsed.scheme in {"http","https"} and parsed.hostname not in self.allowed_hosts:
                            if self.network_events is not None: self.network_events.append({"host":parsed.hostname or "","path":parsed.path,"observed":True})
                    page.on("request", observe_request)
                try:
                    await page.goto(context.application_url,wait_until="domcontentloaded",timeout=APPLICATION_BROWSER_TIMEOUT_MS)
                    selection=await self.select_application_surface(page, context.portal)
                    if selection["status"] == "APPLICATION_SURFACE_AMBIGUOUS":
                        return {"outcome":"SUBMISSION_FAILED","signals":["APPLICATION_SURFACE_AMBIGUOUS"]}
                    surface=selection["surface"] or page
                    seen=set(); actions=0; plan=None; html=""; entered_answers={}; wrapper_transitioned=False
                    for _ in range(6):
                        html=await self._surface_content(surface); surface_url=self._surface_url(surface); purpose=self.page_purpose(surface_url,html); portal=self.detect_portal(surface_url,html)
                        if self.network_events is not None and len(self.network_events) > network_event_count:
                            return {"outcome":"SUBMISSION_FAILED","signals":["EXTERNAL_REDIRECT_BLOCKED"]}
                        if portal!=context.portal:
                            return {"outcome":"SUBMISSION_FAILED","signals":["APPLICATION_CHANGED_AFTER_REVIEW","MISMATCH_PORTAL"]}
                        if purpose in {"CAPTCHA","LOGIN","MFA","ACCOUNT_CREATION"}: return {"outcome":"SUBMISSION_FAILED","signals":[purpose]}
                        if purpose == "APPLICATION_SUCCESS": return {"outcome":"SUBMISSION_FAILED","signals":["UNEXPECTED_APPLICATION_SUCCESS"]}
                        if wrapper_transitioned and purpose == "NON_APPLICATION" and detect_portal_evidence(self._surface_url(surface), html).wrapper_detected:
                            return {"outcome":"SUBMISSION_FAILED","signals":["LOOP_DETECTED"]}
                        if not wrapper_transitioned and purpose == "NON_APPLICATION":
                            target=await self._trusted_wrapper_application_target(page, context.portal, html)
                            if target == "CONFLICT": return {"outcome":"SUBMISSION_FAILED","signals":["WRAPPER_PORTAL_CONFLICT"]}
                            if target == "EXTERNAL": return {"outcome":"SUBMISSION_FAILED","signals":["EXTERNAL_REDIRECT_BLOCKED"]}
                            if target == "AMBIGUOUS": return {"outcome":"SUBMISSION_FAILED","signals":["WRAPPER_TARGET_AMBIGUOUS"]}
                            if not target: return {"outcome":"SUBMISSION_FAILED","signals":["ROUTE_UNRESOLVED"]}
                            before_url=page.url; before_fingerprint=self._page_fingerprint(html)
                            await target.click(no_wait_after=True)
                            await self._wait_for_meaningful_transition(page,before_url,before_fingerprint)
                            wrapper_transitioned=True
                            continue
                        plan=self.preview_html(html,surface_url,vacancy,context.tracker_id,persist=False)
                        parser=_FormParser(); parser.feed(html); fp=hashlib.sha256((surface_url+"|"+purpose+"|"+"|".join(f"{f.concept}:{f.field_type}" for f in plan.fields)).encode()).hexdigest()
                        if fp in seen: return {"outcome":"SUBMISSION_FAILED","signals":["LOOP_DETECTED"]}
                        seen.add(fp)
                        if purpose=="APPLICATION_REVIEW": break
                        if purpose!="APPLICATION_FORM": return {"outcome":"SUBMISSION_FAILED","signals":["FINAL_REVIEW_NOT_REACHED"]}
                        if any(f.required and f.action=="REVIEW" for f in plan.fields) or any(d["required"] and d["action"]=="DOCUMENT_NOT_READY" for d in plan.document_requirements): return {"outcome":"SUBMISSION_FAILED","signals":["MANUAL_INPUT_REQUIRED"]}
                        for field in plan.fields:
                            if field.action == "FILL" and field.answer is not None:
                                entered_answers[field.label]=str(field.answer)
                        await self._fill_supported(surface,plan)
                        safe=[t for t,_ in parser.buttons if SAFE_BUTTON.match(t.strip())]
                        if len(safe)!=1 or actions>=5: return {"outcome":"SUBMISSION_FAILED","signals":["NAVIGATION_UNCERTAIN"]}
                        before_url=self._surface_url(surface); before_fingerprint=self._page_fingerprint(html)
                        await self._click_safe_navigation(surface, html); actions+=1
                        await self._wait_for_meaningful_transition(surface,before_url,before_fingerprint)
                    else: return {"outcome":"SUBMISSION_FAILED","signals":["MAX_PAGES_EXCEEDED"]}
                    if review.unknown_required_fields or not review.final_submit_detected: return {"outcome":"SUBMISSION_FAILED","signals":["APPLICATION_CHANGED_AFTER_REVIEW"]}
                    reconciliation=self._reconcile_final_review(html, entered_answers, package, review)
                    if not reconciliation["matches"]:
                        return {"outcome":"SUBMISSION_FAILED","signals":["APPLICATION_CHANGED_AFTER_REVIEW",*reconciliation["mismatches"]]}
                    parser=_FormParser(); parser.feed(html); candidates=[text for text,_ in parser.buttons if FINAL_BUTTON.search(text)]
                    if len(candidates)!=1: return {"outcome":"SUBMISSION_FAILED","signals":["FINAL_SUBMIT_AMBIGUOUS" if candidates else "FINAL_SUBMIT_NOT_FOUND"]}
                    button=surface.get_by_role("button",name=candidates[0],exact=True)
                    if await button.count()!=1 or not await button.is_visible() or not await button.is_enabled(): return {"outcome":"SUBMISSION_FAILED","signals":["FINAL_CONTROL_NOT_VERIFIED"]}
                    if not allow_final_click:
                        return {"outcome":"FINAL_REVIEW_READY","final_submit_detected":True,"final_submit_clicked":False,"signals":["FINAL_REVIEW_RECONCILED","FINAL_CONTROL_VERIFIED"]}
                    before_url=surface_url; before_fingerprint=self._page_fingerprint(html)
                    await button.click(); clicked=True; at=datetime.now(timezone.utc).isoformat()
                    try: await self._wait_for_meaningful_transition(surface,before_url,before_fingerprint)
                    except Exception: return {"outcome":"SUBMISSION_OUTCOME_UNCERTAIN","submit_clicked_at":at,"signals":["POST_CLICK_TIMEOUT"]}
                    body=(await self._surface_content(surface)).lower(); signals=[]
                    if self.page_purpose(self._surface_url(surface),body)=="APPLICATION_SUCCESS": signals.append("SUCCESS_PAGE_CLASSIFIED")
                    if re.search(r"thank you for applying|application (has been )?(submitted|received|complete)",body): signals.append("SUCCESS_MESSAGE")
                    if len(signals)>=1: return {"outcome":"SUBMISSION_CONFIRMED","submit_clicked_at":at,"confirmed_at":datetime.now(timezone.utc).isoformat(),"signals":signals}
                    if re.search(r"error|required field|unable to submit|validation",body): return {"outcome":"SUBMISSION_FAILED","submit_clicked_at":at,"signals":["FAILURE_SIGNAL"]}
                    return {"outcome":"SUBMISSION_OUTCOME_UNCERTAIN","submit_clicked_at":at,"signals":["NO_CONFIRMATION"]}
                finally:
                    await ctx.close(); await browser.close()
                    if self.package_service is None: packages.history.close()
        except Exception as exc:
            if self.network_events is not None and len(self.network_events) > network_event_count:
                return {"outcome":"SUBMISSION_FAILED","signals":["EXTERNAL_REDIRECT_BLOCKED"]}
            return {"outcome":"SUBMISSION_OUTCOME_UNCERTAIN" if clicked else "SUBMISSION_FAILED","signals":[type(exc).__name__]}

    @staticmethod
    def _reconcile_final_review(html, entered_answers, package, review):
        """Compare only safe, already-entered evidence shown on a review page."""
        text=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip().lower()
        normalized_text=re.sub(r"[^a-z0-9]+", " ", text).strip()
        mismatches=[]
        for label,value in entered_answers.items():
            normalized_label=re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
            normalized_value=re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
            if normalized_label in normalized_text and f"{normalized_label} {normalized_value}" not in normalized_text:
                mismatches.append("MISMATCH_REVIEW_ANSWER")
        for path in (getattr(package,"resume_path",""), getattr(package,"cover_letter_path","")):
            if path and Path(path).is_file() and Path(path).name.lower() not in text:
                mismatches.append("MISMATCH_REVIEW_DOCUMENT")
        if getattr(review,"pending_manual_actions",None): mismatches.append("MANUAL_REVIEW_REQUIRED")
        return {"matches":not mismatches,"mismatches":list(dict.fromkeys(mismatches))}

    async def _trusted_wrapper_application_target(self, page, portal, html):
        """Return one evidence-marked wrapper Apply link, never an arbitrary link."""
        evidence=detect_portal_evidence(page.url, html)
        if portal != "GREENHOUSE" or evidence.confidence != "HIGH" or not evidence.wrapper_detected:
            return None
        if re.search(r"data-(?:ats|portal|provider)=[\"'](?:workday|lever|smartrecruiters|oracle|ashby)", html, re.I):
            return "CONFLICT"
        links=page.locator("a[data-portal='greenhouse'], a[data-ats='greenhouse'], a[href*='greenhouse.io']")
        candidates=[]
        for index in range(await links.count()):
            link=links.nth(index)
            href=await link.get_attribute("href")
            if href and self.allowed_hosts and urlparse(href).hostname not in self.allowed_hosts:
                return "EXTERNAL"
            if await link.is_visible() and href:
                candidates.append(link)
        return candidates[0] if len(candidates) == 1 else ("AMBIGUOUS" if candidates else None)

    async def select_application_surface(self, page: "Page", expected_portal: str) -> dict[str, Any]:
        """Select the one trusted Page/Frame that hosts the application form.

        The top-level page is always preferred and returned as-is whenever it
        is already a valid surface for ``expected_portal``; no iframe search
        is attempted in that case. Embedded-iframe discovery is otherwise
        only attempted for Greenhouse, and a frame is only ever trusted when
        it carries HIGH-confidence portal evidence for the expected portal
        *and* is structurally an application form -- a domain/marker match
        alone never authorizes a structurally inconsistent frame (e.g. a
        CAPTCHA or auth iframe). Zero or multiple such frames fail closed
        instead of guessing, and an uninspectable or detached candidate is
        simply excluded rather than selected.
        """
        html = await self._surface_content(page)
        url = self._surface_url(page)
        if self.page_purpose(url, html) == "APPLICATION_FORM" and self.detect_portal(url, html) == expected_portal:
            return {"surface": page, "status": "DIRECT_PAGE"}
        if expected_portal != "GREENHOUSE":
            return {"surface": None, "status": "APPLICATION_SURFACE_NOT_FOUND"}
        candidates = []
        for frame in page.frames:
            if frame is page.main_frame or frame.is_detached():
                continue
            try:
                frame_url = frame.url
                frame_html = await frame.content()
            except Exception:
                continue
            evidence = detect_portal_evidence(frame_url, frame_html)
            if evidence.portal != "GREENHOUSE" or evidence.confidence != "HIGH":
                continue
            if self.page_purpose(frame_url, frame_html) != "APPLICATION_FORM":
                continue
            candidates.append(frame)
        if len(candidates) == 1:
            return {"surface": candidates[0], "status": "TRUSTED_IFRAME"}
        if not candidates:
            return {"surface": None, "status": "APPLICATION_SURFACE_NOT_FOUND"}
        return {"surface": None, "status": "APPLICATION_SURFACE_AMBIGUOUS"}

    async def _progress_url(self, url, vacancy, tracker_id, headed, application_date, max_pages, max_navigation_actions):
        self.validate_url(url)
        from playwright.async_api import async_playwright
        plans=[]; fingerprints=set(); actions=0; status="FAILED"
        async with async_playwright() as api:
            browser=await api.chromium.launch(headless=not headed); context=await browser.new_context(); page=await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=APPLICATION_BROWSER_TIMEOUT_MS)
                selection=await self.select_application_surface(page, "GREENHOUSE")
                if selection["status"] == "APPLICATION_SURFACE_AMBIGUOUS":
                    status="APPLICATION_SURFACE_AMBIGUOUS"
                else:
                    surface=selection["surface"] or page
                    for _ in range(max_pages):
                        html=await self._surface_content(surface); surface_url=self._surface_url(surface); plan=self.preview_html(html, surface_url, vacancy, tracker_id, application_date, persist=False); plans.append(plan)
                        parser=_FormParser(); parser.feed(html)
                        fingerprint=hashlib.sha256((surface_url+"|"+plan.portal+"|"+plan.page_purpose+"|"+"|".join(f"{f.concept}:{f.field_type}:{f.required}" for f in plan.fields)+"|"+"|".join(text for text,_ in parser.buttons)).encode()).hexdigest()
                        if fingerprint in fingerprints: status="LOOP_DETECTED"; break
                        fingerprints.add(fingerprint)
                        if plan.page_purpose == "APPLICATION_SUCCESS": status="UNEXPECTED_APPLICATION_SUCCESS"; break
                        if plan.readiness in {"CAPTCHA_REQUIRED","AUTH_REQUIRED","MFA_REQUIRED"}: status=plan.readiness; break
                        if plan.page_purpose == "ACCOUNT_CREATION": status="ACCOUNT_CREATION_REQUIRED"; break
                        if plan.page_purpose == "APPLICATION_REVIEW" or plan.final_submit_detected:
                            status="PREPARED_FOR_FINAL_REVIEW"; break
                        if plan.portal not in {"GREENHOUSE","LEVER"}: status="UNSUPPORTED_PORTAL"; break
                        if any(f.required and f.action == "REVIEW" for f in plan.fields) or any(d["required"] and d["action"] == "DOCUMENT_NOT_READY" for d in plan.document_requirements): status="MANUAL_INPUT_REQUIRED"; break
                        await self._fill_supported(surface, plan)
                        safe=[text for text,_ in parser.buttons if SAFE_BUTTON.match(text.strip())]
                        if len(safe) != 1: status="NAVIGATION_UNCERTAIN"; break
                        if actions >= max_navigation_actions: status="MAX_NAVIGATION_ACTIONS_EXCEEDED"; break
                        before_url=surface_url; before_fingerprint=self._page_fingerprint(html)
                        await self._click_safe_navigation(surface, html); actions += 1
                        try: await self._wait_for_meaningful_transition(surface, before_url, before_fingerprint)
                        except Exception: status="TIMEOUT"; break
                    else: status="MAX_PAGES_EXCEEDED"
            finally:
                await context.close(); await browser.close()
        return {"plans":plans,"status":status,"navigation_actions":actions}

    async def _preview_url(self, url, vacancy, tracker_id, headed, application_date, fill_preview, pause_seconds):
        self.validate_url(url)
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc: raise RuntimeError("Playwright is required. Install project dependencies and Chromium before live preview.") from exc
        async with async_playwright() as api:
            browser = await api.chromium.launch(headless=not headed)
            context = await browser.new_context()  # isolated: no imported browser profile/cookies
            page = await context.new_page(); await page.goto(url, wait_until="domcontentloaded", timeout=APPLICATION_BROWSER_TIMEOUT_MS)
            html = await page.content(); route=self.route_resolver.resolve(vacancy or {"job_url": url}, html, page.url)
            if route.resolution_status == "RESOLVED" and route.application_url and route.application_url != page.url:
                await page.goto(route.application_url, wait_until="domcontentloaded", timeout=APPLICATION_BROWSER_TIMEOUT_MS); html=await page.content()
            plan = self.preview_html(html, page.url, vacancy, tracker_id, application_date, route)
            if fill_preview and plan.page_purpose == "APPLICATION_FORM" and plan.portal in {"GREENHOUSE", "LEVER", "GENERIC"}:
                await self._fill_supported(page, plan)
                if pause_seconds and headed: await page.wait_for_timeout(min(pause_seconds, 300) * 1000)
            folder = self.preview_folder / str(tracker_id or "url") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            folder.mkdir(parents=True, exist_ok=True); await page.screenshot(path=str(folder / "landing.png"), full_page=True)
            await context.close(); await browser.close()
            return plan

    @staticmethod
    def _surface_url(surface: "Page | Frame | ApplicationSurface") -> str:
        """Return the current URL without persisting it.

        Playwright exposes ``url`` on both Page and Frame.  This tiny helper is
        deliberately the sole URL access point for shared surface primitives.
        """
        return str(surface.url)

    @staticmethod
    async def _surface_content(surface: "Page | Frame | ApplicationSurface") -> str:
        """Read the active Page or Frame DOM through the shared API."""
        if getattr(surface, "is_detached", lambda: False)():
            raise RuntimeError("APPLICATION_SURFACE_DETACHED")
        return await surface.content()

    async def _fill_supported(self, surface: "Page | Frame | ApplicationSurface", plan: ApplicationPlan) -> None:
        """Fill only pre-approved high-confidence fields. Never clicks a button."""
        for field in plan.fields:
            if field.action != "FILL" or field.answer is None: continue
            selector = f"#{field.field_id}"
            try:
                locator = surface.locator(selector)
                if field.field_type in {"SELECT", "MULTISELECT"}: await locator.select_option(label=str(field.answer))
                elif field.field_type in {"RADIO", "BOOLEAN", "CHECKBOX"}:
                    # Legal/consent fields never reach FILL. Only explicit safe boolean controls may.
                    if str(field.answer).upper() in {"YES", "TRUE"}: await locator.check()
                else: await locator.fill(str(field.answer))
                plan.fields_filled += 1
            except Exception:
                field.action = "REVIEW"; field.reason = "Supported fill selector/value was not unambiguous."
        for document in plan.document_requirements:
            if document["action"] != "READY_FOR_UPLOAD" or document["kind"] not in {"RESUME", "COVER_LETTER"}: continue
            try:
                await surface.locator(f"#{document['field_id']}").set_input_files(document["path"])
                document["action"] = "UPLOADED_IN_FILL_PREVIEW"; plan.documents_uploaded += 1
            except Exception:
                document["action"] = "DOCUMENT_NOT_READY"

    @staticmethod
    def _page_fingerprint(html: str) -> str:
        return hashlib.sha256(re.sub(r"\s+", " ", html).encode()).hexdigest()

    async def _click_safe_navigation(self, surface: "Page | Frame | ApplicationSurface", html: str) -> str:
        """Click exactly one existing non-final navigation control on a surface."""
        parser=_FormParser(); parser.feed(html)
        safe=[text for text,_ in parser.buttons if SAFE_BUTTON.match(text.strip())]
        if len(safe) != 1:
            raise RuntimeError("NAVIGATION_UNCERTAIN")
        await surface.get_by_role("button", name=safe[0], exact=True).click(no_wait_after=True)
        return safe[0]

    async def _wait_for_meaningful_transition(self, surface: "Page | Frame | ApplicationSurface", previous_url: str, previous_fingerprint: str) -> None:
        """Wait for a URL or structural DOM change on the selected surface.

        Frame transitions do not necessarily alter the top-level Page URL, so
        all observations are intentionally scoped to ``surface``.  A detached
        frame fails closed instead of being silently replaced.
        """
        deadline=asyncio.get_running_loop().time() + APPLICATION_BROWSER_TIMEOUT_MS / 1000
        while asyncio.get_running_loop().time() < deadline:
            if getattr(surface, "is_detached", lambda: False)():
                raise RuntimeError("APPLICATION_SURFACE_DETACHED")
            if self._surface_url(surface) != previous_url:
                return
            try:
                if self._page_fingerprint(await self._surface_content(surface)) != previous_fingerprint:
                    return
            except RuntimeError:
                raise
            except Exception:
                # A native form navigation can briefly make the DOM unavailable.
                # It is not a submission or form-classification failure.
                pass
            await asyncio.sleep(0.05)
        raise TimeoutError("Safe navigation did not produce a meaningful page transition.")

    @staticmethod
    def _document_path(vacancy: Any, kind: str) -> Path | None:
        key = "resume_path" if kind == "RESUME" else "cover_letter_path" if kind == "COVER_LETTER" else ""
        value = vacancy.get(key) if isinstance(vacancy, dict) else getattr(vacancy, key, None)
        path = Path(value) if value else None
        return path if path and path.is_file() and path.suffix.lower() in {".pdf", ".docx"} else None

    def _persist(self, plan: ApplicationPlan) -> None:
        self.preview_folder.mkdir(parents=True, exist_ok=True)
        path = self.preview_folder / f"preview_{plan.tracker_id or 'url'}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ%f')}.json"
        path.write_text(json.dumps(plan.to_dict(), indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _field_type(value: str) -> str:
        value = value.upper()
        return {"TEXT": "TEXT", "EMAIL": "EMAIL", "TEL": "PHONE", "NUMBER": "NUMBER", "DATE": "DATE", "RADIO": "RADIO", "CHECKBOX": "CHECKBOX", "FILE": "FILE", "TEXTAREA": "TEXTAREA", "SELECT": "SELECT"}.get(value, "UNKNOWN")
    @staticmethod
    def _value(value, key): return value.get(key) if isinstance(value, dict) else getattr(value, key, None)
    @staticmethod
    def _int(value):
        try: return int(value) if value else None
        except ValueError: return None
