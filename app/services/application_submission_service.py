"""Explicit, one-review submission boundary. No bulk or automatic submission exists."""
from __future__ import annotations
import json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from app.models.submission import SubmissionAuthorization, SubmissionReceipt
from app.services.final_review_service import FinalReviewService
from app.services.application_browser_service import ApplicationBrowserService
from app.models.submission import SubmissionContext

RECEIPT_DIR=Path("app/data/application_submissions"); LOCK_DIR=Path("app/data/application_submission_locks"); TTL=timedelta(minutes=15)
class ApplicationSubmissionService:
 def __init__(self, review_service=None,browser=None,receipt_dir=RECEIPT_DIR,lock_dir=LOCK_DIR):
  self.reviews=review_service or FinalReviewService(); self.history=self.reviews.history; self.browser=browser or ApplicationBrowserService(); self.receipt_dir=Path(receipt_dir); self.lock_dir=Path(lock_dir)
 def inspect(self,review_id):
  review=self.reviews.show(review_id); reason=self._gate(review)
  return {"status":"READY_FOR_EXPLICIT_AUTHORIZATION" if not reason else "SUBMISSION_BLOCKED","reason":reason or "","review":review}
 def simulate(self,review_id): return self.inspect(review_id)
 def submit(self,review_id,confirmation):
  review=self.reviews.show(review_id); expected=f"SUBMIT {review_id}"
  if confirmation != expected: return self._receipt(review,"SUBMISSION_CANCELLED")
  if self._confirmed(review): return self._receipt(review,"ALREADY_SUBMITTED")
  if self._uncertain(review): return self._receipt(review,"SUBMISSION_OUTCOME_UNCERTAIN",["PREVIOUS_OUTCOME_UNCERTAIN"])
  reason=self._gate(review)
  if reason: return self._receipt(review,"SUBMISSION_BLOCKED",[reason])
  lock=self._lock(review_id)
  if not lock: return self._receipt(review,"SUBMISSION_IN_PROGRESS")
  try:
   auth=SubmissionAuthorization(review_id,review.tracker_id,review.package_id,review.execution_id,review.fingerprint)
   receipt=self._receipt(review,"AUTHORIZED"); self._event(receipt,"SUBMISSION_AUTHORIZED")
   # Re-check after exclusive lock and before browser creation.
   if self._gate(review): receipt.outcome="SUBMISSION_BLOCKED"; return self._save(receipt)
   context=SubmissionContext(review.review_id,review.tracker_id,review.package_id,review.execution_id,review.application_portal,review.application_url,review.fingerprint)
   self._event(receipt,"SUBMISSION_PRECHECK_PASSED"); outcome=self.browser.submit_final_url(context)
   receipt.submit_clicked_at=outcome.get("submit_clicked_at",""); receipt.confirmation_signals=outcome.get("signals",[])
   receipt.outcome=outcome.get("outcome","SUBMISSION_OUTCOME_UNCERTAIN")
   if receipt.outcome=="SUBMISSION_CONFIRMED":
    receipt.confirmed_at=outcome.get("confirmed_at") or self._now(); self._event(receipt,"SUBMISSION_CONFIRMED")
    self.history.update_record(self.history.get_record_by_id(review.tracker_id)["job_fingerprint"],status="APPLIED",application_status="APPLIED",applied_at=receipt.confirmed_at)
    receipt.tracker_updated=True; self._event(receipt,"TRACKER_MARKED_APPLIED")
   elif receipt.outcome=="SUBMISSION_OUTCOME_UNCERTAIN": self._event(receipt,"SUBMISSION_OUTCOME_UNCERTAIN")
   else: self._event(receipt,"SUBMISSION_FAILED")
   return self._save(receipt)
  finally:
   try: lock.unlink()
   except Exception: pass
 def _gate(self,review):
  if not review or review.review_status!="APPROVED_FOR_SUBMISSION": return "REVIEW_NOT_APPROVED"
  current=self.reviews._build(self.history.get_record_by_id(review.tracker_id),self.reviews.package_service.load(review.tracker_id),self.reviews._execution(review.execution_id))
  if current.fingerprint!=review.fingerprint: return "REVIEW_EXPIRED"
  if current.blocking_reasons: return current.blocking_reasons[0]
  if current.application_portal not in {"GREENHOUSE","LEVER"}: return "SUBMISSION_PORTAL_UNSUPPORTED"
  return ""
 def _confirmed(self,review): return any(json.loads(p.read_text()).get("outcome")=="SUBMISSION_CONFIRMED" and json.loads(p.read_text()).get("review_id")==review.review_id for p in self.receipt_dir.glob("*.json")) if self.receipt_dir.exists() else False
 def _uncertain(self,review): return any(json.loads(p.read_text()).get("outcome")=="SUBMISSION_OUTCOME_UNCERTAIN" and json.loads(p.read_text()).get("review_id")==review.review_id for p in self.receipt_dir.glob("*.json")) if self.receipt_dir.exists() else False
 def _lock(self,id):
  self.lock_dir.mkdir(parents=True,exist_ok=True); path=self.lock_dir/f"{id}.lock"
  try: fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY); os.close(fd); return path
  except FileExistsError: return None
 def _receipt(self,r,outcome,signals=None): return SubmissionReceipt(review_id=getattr(r,"review_id",""),tracker_id=getattr(r,"tracker_id",0),package_id=getattr(r,"package_id","") or "",execution_id=getattr(r,"execution_id","") or "",company=getattr(r,"company","") or "",job_title=getattr(r,"job_title","") or "",portal=getattr(r,"application_portal","") or "",application_url=getattr(r,"application_url","") or "",outcome=outcome,confirmation_signals=signals or [])
 def _save(self,x): self.receipt_dir.mkdir(parents=True,exist_ok=True); (self.receipt_dir/f"{x.submission_id}.json").write_text(json.dumps(x.to_dict(),indent=2)); return x
 def _event(self,x,a): x.audit.append({"at":self._now(),"action":a})
 @staticmethod
 def _now(): return datetime.now(timezone.utc).isoformat()
