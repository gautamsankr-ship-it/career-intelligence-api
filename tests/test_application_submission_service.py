from app.services.application_submission_service import ApplicationSubmissionService
from test_final_review_service import setup

class Browser:
 def __init__(self,outcome): self.outcome=outcome; self.calls=0
 def submit_final_url(self,*args): self.calls+=1; return {"outcome":self.outcome,"signals":["success"],"submit_clicked_at":"now","confirmed_at":"now"}

def test_explicit_confirmation_and_confirmed_receipt_updates_tracker(tmp_path):
 reviews,record,pkg,exe=setup(tmp_path); record['job_fingerprint']='f'; reviews.history.update_record=lambda fp,**fields: record.update(fields)
 review=reviews.create(42); reviews.approve(review.review_id)
 browser=Browser('SUBMISSION_CONFIRMED'); service=ApplicationSubmissionService(reviews,browser,tmp_path/'receipts',tmp_path/'locks')
 assert service.submit(review.review_id,'wrong').outcome=='SUBMISSION_CANCELLED' and browser.calls==0
 receipt=service.submit(review.review_id,f'SUBMIT {review.review_id}')
 assert receipt.outcome=='SUBMISSION_CONFIRMED' and record['status']=='APPLIED' and receipt.tracker_updated
 assert service.submit(review.review_id,f'SUBMIT {review.review_id}').outcome=='ALREADY_SUBMITTED'

def test_unapproved_and_job33_style_are_blocked_without_browser(tmp_path):
 reviews,record,pkg,exe=setup(tmp_path); browser=Browser('SUBMISSION_CONFIRMED'); service=ApplicationSubmissionService(reviews,browser,tmp_path/'r',tmp_path/'l')
 review=reviews.create(42); assert service.submit(review.review_id,f'SUBMIT {review.review_id}').outcome=='SUBMISSION_BLOCKED'
 assert browser.calls==0

def test_failed_and_uncertain_do_not_apply(tmp_path):
 for outcome in ('SUBMISSION_FAILED','SUBMISSION_OUTCOME_UNCERTAIN'):
  reviews,record,pkg,exe=setup(tmp_path/outcome); record['job_fingerprint']='f'; reviews.history.update_record=lambda *a,**k: record.update(k)
  review=reviews.create(42); reviews.approve(review.review_id); r=ApplicationSubmissionService(reviews,Browser(outcome),tmp_path/outcome/'r',tmp_path/outcome/'l').submit(review.review_id,f'SUBMIT {review.review_id}')
  assert r.outcome==outcome and record['status']!='APPLIED'
