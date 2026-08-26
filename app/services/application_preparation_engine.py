"""Bounded, non-submitting multi-page application preparation orchestration."""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from app.config import APPLICATION_AUTO_SUBMIT, APPLICATION_DRY_RUN
from app.models.application_preparation import ApplicationException, ApplicationPreparationSession
from app.services.application_browser_service import ApplicationBrowserService, FINAL_BUTTON, SAFE_BUTTON, _FormParser

SESSION_DIR=Path("app/data/application_sessions")
SUPPORTED={"GREENHOUSE","LEVER","GENERIC"}
CAPABILITIES={
 "GREENHOUSE":{"DETECT":"YES","INSPECT":"YES","FILL":"YES","UPLOAD":"YES","SAFE_NAVIGATE":"YES","RESUME":"YES","FINAL_REVIEW_DETECT":"YES"},
 "LEVER":{"DETECT":"YES","INSPECT":"YES","FILL":"YES","UPLOAD":"YES","SAFE_NAVIGATE":"YES","RESUME":"YES","FINAL_REVIEW_DETECT":"YES"},
 "GENERIC":{"DETECT":"YES","INSPECT":"YES","FILL":"YES","UPLOAD":"LIMITED","SAFE_NAVIGATE":"LIMITED","RESUME":"YES","FINAL_REVIEW_DETECT":"YES"},
}

class ApplicationPreparationEngine:
    def __init__(self, browser_service=None, session_dir=SESSION_DIR):
        self.browser=browser_service or ApplicationBrowserService(); self.session_dir=Path(session_dir); self.session_dir.mkdir(parents=True,exist_ok=True)
    def create_session(self, vacancy, tracker_id=None, application_date=""):
        get=lambda k: vacancy.get(k) if isinstance(vacancy,dict) else getattr(vacancy,k,None)
        session=ApplicationPreparationSession(uuid4().hex,tracker_id,get("application_url") or "",get("source_listing_url") or get("job_url") or "",get("application_portal") or "UNKNOWN",application_date=application_date)
        self._audit(session,"SESSION_CREATED"); self.save(session); return session
    def prepare_pages(self, pages, vacancy, tracker_id=None, application_date="", max_pages=5, max_navigation_actions=4, session=None):
        if not APPLICATION_DRY_RUN or APPLICATION_AUTO_SUBMIT: raise RuntimeError("Preparation safety configuration is not enabled.")
        session=session or self.create_session(vacancy,tracker_id,application_date); session.state="OPENING"
        for index,page in enumerate(pages,1):
            if index>max_pages: return self._stop(session,"FAILED","MAX_PAGES_EXCEEDED")
            html=page["html"] if isinstance(page,dict) else page; url=page.get("url",session.application_url) if isinstance(page,dict) else session.application_url
            plan=self.browser.preview_html(html,url,vacancy,tracker_id,application_date)
            fingerprint=self._fingerprint(plan)
            if fingerprint in session.page_fingerprints: return self._stop(session,"FAILED","LOOP_DETECTED")
            session.page_fingerprints.append(fingerprint); session.current_page_number=index; session.current_url=url; session.pages_processed+=1; session.portal=plan.portal; self._audit(session,"PAGE_CLASSIFIED",purpose=plan.page_purpose)
            if plan.page_purpose=="APPLICATION_SUCCESS": return self._stop(session,"FAILED","UNEXPECTED_APPLICATION_SUCCESS")
            purpose_state={"LOGIN":"AUTH_REQUIRED","MFA":"MFA_REQUIRED","CAPTCHA":"CAPTCHA_REQUIRED","ACCOUNT_CREATION":"ACCOUNT_CREATION_REQUIRED","APPLICATION_REVIEW":"READY_FOR_FINAL_REVIEW"}.get(plan.page_purpose)
            if purpose_state:
                session.final_review_detected=purpose_state=="READY_FOR_FINAL_REVIEW"; return self._stop(session,purpose_state,purpose_state)
            if plan.portal not in SUPPORTED: return self._stop(session,"PORTAL_LIMITED","PORTAL_LIMITED")
            self._process_plan(session,plan)
            blocking=[x for x in session.exceptions if x.page_number==index and x.required and x.resolution=="OPEN"]
            if blocking: return self._stop(session,"MANUAL_INPUT_REQUIRED","REQUIRED_EXCEPTION")
            nav=self._navigation(html,plan.page_purpose)
            if nav=="FINAL_SUBMISSION": return self._stop(session,"READY_FOR_FINAL_REVIEW","FINAL_SUBMIT_DETECTED")
            if nav=="SAFE_NAVIGATION":
                session.navigation_actions+=1; self._audit(session,"SAFE_NAVIGATION_CLICKED",control="fixture-only")
                if session.navigation_actions>max_navigation_actions:return self._stop(session,"FAILED","MAX_NAVIGATION_ACTIONS_EXCEEDED")
                continue
            return self._stop(session,"COMPLETED_PREPARATION","NO_SAFE_NAVIGATION")
        return self._stop(session,"COMPLETED_PREPARATION","PAGES_EXHAUSTED")
    def _process_plan(self, session, plan):
        for field in plan.fields:
            session.fields_detected+=1
            if field.action=="FILL": session.fields_filled+=1; self._audit(session,"FIELD_FILLED",concept=field.concept,source=field.answer_source)
            elif field.action=="SKIP": session.fields_skipped+=1; self._audit(session,"FIELD_SKIPPED",concept=field.concept)
            else: self._exception(session,plan,field)
        for doc in plan.document_requirements:
            if doc["action"]=="READY_FOR_UPLOAD": session.documents_uploaded+=1; self._audit(session,"DOCUMENT_UPLOADED",kind=doc["kind"])
            elif doc["required"]: self._exception(session,plan,None,"DOCUMENT_NOT_READY",doc["label"],True,"Exact vacancy-specific document is unavailable.")
    def _exception(self,session,plan,field=None,kind=None,label=None,required=None,reason=None):
        concept=field.concept if field else "DOCUMENT"; label=label or field.label; required=field.required if field else required; reason=reason or field.reason
        if field and concept in {"LEGAL_DECLARATION","VOLUNTARY_DEMOGRAPHIC","CRIMINAL_HISTORY","SECURITY_CLEARANCE","CONFLICT_OF_INTEREST"}: kind="LEGAL_DECLARATION" if concept=="LEGAL_DECLARATION" else "MANUAL_REQUIRED"
        elif field and concept=="EXPECTED_SALARY": kind="SALARY_NUMERIC_REQUIRED"
        elif field and concept=="TRAVEL_PERCENTAGE": kind="TRAVEL_PERCENTAGE_REQUIRED"
        else: kind=kind or "MANUAL_REQUIRED"
        identity=f"{session.session_id}:{session.current_page_number}:{label}:{concept}"
        if any(x.exception_id==identity for x in session.exceptions): return
        session.exceptions.append(ApplicationException(identity,session.session_id,session.current_page_number,session.current_url,plan.portal,label,concept,kind,bool(required),reason,field.choices if field else [])); self._audit(session,"EXCEPTION_CREATED",type=kind,concept=concept)
    @staticmethod
    def _navigation(html,purpose):
        parser=_FormParser(); parser.feed(html)
        texts=[text.strip() for text,_ in parser.buttons]
        if any(FINAL_BUTTON.search(text) for text in texts): return "FINAL_SUBMISSION"
        if purpose=="APPLICATION_FORM" and any(SAFE_BUTTON.match(text) for text in texts): return "SAFE_NAVIGATION"
        return "UNKNOWN"
    @staticmethod
    def _fingerprint(plan):
        structure="|".join([plan.url,plan.portal,plan.page_purpose,*[f"{f.concept}:{f.field_type}" for f in plan.fields]])
        return hashlib.sha256(structure.encode()).hexdigest()
    def _stop(self,session,state,reason): session.state=state; session.failure_reason=reason; session.updated_at=datetime.now(timezone.utc).isoformat(); self._audit(session,"SESSION_PAUSED",reason=reason); self.save(session); return session
    def _audit(self,session,action,**details): session.audit.append({"timestamp":datetime.now(timezone.utc).isoformat(),"action_type":action,**details})
    def save(self,session): self.session_dir.mkdir(parents=True,exist_ok=True); (self.session_dir/f"{session.session_id}.json").write_text(json.dumps(session.to_dict(),indent=2),encoding="utf-8")
    def load(self,session_id): return ApplicationPreparationSession.from_dict(json.loads((self.session_dir/f"{session_id}.json").read_text(encoding="utf-8")))
    def sessions(self): return [self.load(p.stem) for p in sorted(self.session_dir.glob("*.json"),reverse=True)]
