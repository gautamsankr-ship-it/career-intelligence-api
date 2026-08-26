"""Metadata-only human final review; deliberately contains no browser/submission API."""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from app.models.final_review import FinalReviewArtifact
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_package_orchestrator import ApplicationPackageOrchestrator, TERMINAL

REVIEW_DIR=Path("app/data/application_reviews")
COMPATIBLE={"PREPARED_FOR_FINAL_REVIEW","FINAL_REVIEW_MANUAL_CONFIRMATION_REQUIRED"}
BLOCKERS={"DIRECT_ROUTE_REQUIRED","CAPTCHA_REQUIRED","AUTH_REQUIRED","MFA_REQUIRED","ACCOUNT_CREATION_REQUIRED","MANUAL_INPUT_REQUIRED","PACKAGE_REFRESH_REQUIRED","ROUTE_UNRESOLVED","UNSUPPORTED_PORTAL","LOOP_DETECTED","FAILED"}

class FinalReviewService:
    def __init__(self, package_service=None, review_dir=REVIEW_DIR, answer_engine=None, execution_dir="app/data/application_executions"):
        self.package_service=package_service or ApplicationPackageOrchestrator(); self.history=self.package_service.history
        self.review_dir=Path(review_dir); self.execution_dir=Path(execution_dir); self.answers=answer_engine or ApplicationAnswerEngine()

    def create(self, tracker_id):
        record=self.history.get_record_by_id(tracker_id); package=self.package_service.load(tracker_id); execution=self._latest_execution(tracker_id)
        review=self._build(record, package, execution)
        existing=self._active(tracker_id)
        if existing and existing.fingerprint == review.fingerprint: return existing
        if existing and existing.review_status == "APPROVED_FOR_SUBMISSION":
            existing.review_status="REVIEW_EXPIRED"; self._event(existing,"FINAL_REVIEW_EXPIRED"); self._save(existing)
        self._event(review,"FINAL_REVIEW_CREATED"); return self._save(review)

    def show(self, review_id):
        path=self._path(review_id)
        return FinalReviewArtifact.from_dict(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None
    def ready(self): return [r for r in self.list() if r.review_status == "READY_FOR_HUMAN_REVIEW"]
    def list(self): return [FinalReviewArtifact.from_dict(json.loads(p.read_text(encoding="utf-8"))) for p in self.review_dir.glob("*.json")] if self.review_dir.exists() else []

    def approve(self, review_id, notes="", manual_confirmations=None):
        review=self._required(review_id); current=self._build(self.history.get_record_by_id(review.tracker_id), self.package_service.load(review.tracker_id), self._execution(review.execution_id))
        if review.fingerprint != current.fingerprint:
            review.review_status="REVIEW_EXPIRED"; self._event(review,"FINAL_REVIEW_EXPIRED"); self._save(review); raise ValueError("Review expired; create a new review.")
        if review.review_status != "READY_FOR_HUMAN_REVIEW": raise ValueError("Review is not eligible for approval.")
        review.review_status="APPROVED_FOR_SUBMISSION"; review.reviewed_at=self._now(); review.pending_manual_actions=list(manual_confirmations or review.pending_manual_actions)
        self._event(review,"FINAL_REVIEW_APPROVED", notes=notes); return self._save(review)
    def changes(self, review_id, note):
        review=self._required(review_id); review.review_status="CHANGES_REQUIRED"; review.reviewed_at=self._now(); self._event(review,"FINAL_REVIEW_CHANGES_REQUESTED", notes=note); return self._save(review)
    def cancel(self, review_id):
        review=self._required(review_id); review.review_status="CHANGES_REQUIRED"; self._event(review,"FINAL_REVIEW_CANCELLED"); return self._save(review)

    def _build(self, record, package, execution):
        review=FinalReviewArtifact(tracker_id=(record or {}).get("id",0), package_id=getattr(package,"package_id","") or "", execution_id=(execution or {}).get("execution_id", ""))
        if not record or not package or not execution: review.blocking_reasons=["MISSING_TRACKER_PACKAGE_OR_EXECUTION"]; return review
        review.company=package.company; review.job_title=package.job_title; review.market=package.market; review.career_track=package.career_track; review.application_url=self._safe_url(package.application_url); review.application_portal=package.application_portal
        for key in ("fields_detected","fields_filled","fields_skipped","manual_review_fields","unknown_required_fields","resume_uploaded","cover_letter_uploaded","final_submit_detected"):
            setattr(review,key,execution.get(key, getattr(review,key)))
        review.execution_status=execution.get("status",""); review.resume_path=package.resume_path; review.cover_letter_path=package.cover_letter_path
        labels=[str(item.get("field", "")) for item in execution.get("audit", [])]
        review.legal_confirmations=[x for x in labels if re.search(r"certif|agree|signature|privacy|consent|legal",x,re.I)]
        review.pending_manual_actions=list(review.legal_confirmations) if review.execution_status == "FINAL_REVIEW_MANUAL_CONFIRMATION_REQUIRED" else []
        review.answer_summary=self._answers(labels, package.market)
        reasons=[]
        if record.get("validation_only") is True: reasons.append("VALIDATION_ONLY_REJECTED")
        if record.get("decision") != "AUTO_APPLY" or record.get("remote_eligibility") != "ELIGIBLE": reasons.append("NOT_APPLICATION_ELIGIBLE")
        if record.get("status") in TERMINAL or record.get("application_status") in TERMINAL: reasons.append("TERMINAL_APPLICATION_STATUS")
        if review.execution_status not in COMPATIBLE: reasons.append(review.execution_status or "EXECUTION_NOT_READY")
        if review.unknown_required_fields: reasons.append("UNKNOWN_REQUIRED_FIELDS")
        if not review.final_submit_detected: reasons.append("FINAL_SUBMIT_NOT_DETECTED")
        if not Path(package.resume_path).is_file() or (package.cover_letter_status == "READY" and not Path(package.cover_letter_path).is_file()): reasons.append("DOCUMENT_NOT_READY")
        if package.resume_vacancy_identity != package.vacancy_identity: reasons.append("PACKAGE_REFRESH_REQUIRED")
        review.blocking_reasons=reasons
        review.review_status="READY_FOR_HUMAN_REVIEW" if not reasons else ("CHANGES_REQUIRED" if any(x in reasons for x in {"UNKNOWN_REQUIRED_FIELDS","DOCUMENT_NOT_READY","PACKAGE_REFRESH_REQUIRED"}) else "NOT_READY")
        review.fingerprint=self._fingerprint(record,package,execution); return review

    def _answers(self, labels, market):
        queries={"work_authorization":"authorized to work","sponsorship":"visa sponsorship","notice_period":"notice period","earliest_start_date":"earliest start date","salary":"salary"}; result={}
        for key, query in queries.items():
            if not any(query.split()[0] in label.lower() for label in labels): result[key]="NOT_PRESENT"; continue
            value=self.answers.resolve(query,market=market).answer; result[key]=str(value) if value is not None else "MANUAL_REVIEW"
        return result
    def _latest_execution(self, tracker_id):
        if not self.execution_dir.exists(): return None
        values=[json.loads(p.read_text(encoding="utf-8")) for p in self.execution_dir.glob("*.json")]
        values=[x for x in values if x.get("tracker_id")==tracker_id]; return max(values,key=lambda x:x.get("created_at", "")) if values else None
    def _execution(self, execution_id):
        path=self.execution_dir/f"{execution_id}.json"; return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    def _active(self,tracker_id):
        items=[x for x in self.list() if x.tracker_id==tracker_id and x.review_status not in {"REVIEW_EXPIRED","CHANGES_REQUIRED"}]; return max(items,key=lambda x:x.updated_at) if items else None
    def _path(self,id): return self.review_dir/f"{id}.json"
    def _save(self,r): self.review_dir.mkdir(parents=True,exist_ok=True); r.updated_at=self._now(); self._path(r.review_id).write_text(json.dumps(r.to_dict(),indent=2),encoding="utf-8"); return r
    def _required(self,id):
        value=self.show(id)
        if not value: raise ValueError("Review ID was not found.")
        return value
    @staticmethod
    def _fingerprint(record,pkg,exe): return hashlib.sha256("|".join(map(str,[(record or {}).get("job_fingerprint"),getattr(pkg,"package_id","") or "",(exe or {}).get("execution_id",""),(exe or {}).get("fields_filled",0),(exe or {}).get("unknown_required_fields",0),getattr(pkg,"application_url","") or "",getattr(pkg,"resume_path","") or "",getattr(pkg,"cover_letter_path","") or ""])).encode()).hexdigest()
    @staticmethod
    def _safe_url(url): return re.sub(r"([?&](?:token|validitytoken)=[^&]+)","",url,flags=re.I)
    def _event(self,r,action,**details): r.audit.append({"at":self._now(),"action":action,**details})
    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat()
