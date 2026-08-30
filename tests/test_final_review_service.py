import json
from pathlib import Path
import pytest
from app.models.application_package import ApplicationPackage
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.final_review_service import FinalReviewService

class History:
 def __init__(self,r): self.r=r
 def get_record_by_id(self,i): return self.r if i==self.r["id"] else None
 def update_record(self,fingerprint,**values): self.r.update(values); return self.r
 def close(self): pass
class Packages:
 def __init__(self,r,p): self.history=History(r); self.p=p
 def load(self,i): return self.p

def setup(tmp_path, status="PREPARED_FOR_FINAL_REVIEW", **changes):
 tmp_path.mkdir(parents=True,exist_ok=True); resume=tmp_path/'r.docx'; cover=tmp_path/'c.docx'; resume.write_text('r'); cover.write_text('c')
 record={"id":42,"job_fingerprint":"f","decision":"AUTO_APPLY","remote_eligibility":"ELIGIBLE","intelligence_priority":"B","status":"MANUAL_WEB_REQUIRED","application_status":"MANUAL_WEB_REQUIRED"}
 pkg=ApplicationPackage('pkg',42,company='Example',job_title='Finance',market='united_kingdom',application_url='https://boards.greenhouse.io/x',application_portal='GREENHOUSE',resume_path=str(resume),resume_status='READY',resume_vacancy_identity='identity',cover_letter_path=str(cover),cover_letter_status='READY',vacancy_identity='identity')
 execution={"execution_id":"exec","tracker_id":42,"status":status,"fields_detected":10,"fields_filled":8,"fields_skipped":1,"manual_review_fields":0,"unknown_required_fields":0,"resume_uploaded":True,"cover_letter_uploaded":True,"final_submit_detected":True,"audit":[{"field":"What is your notice period?"},{"field":"I certify this is accurate"}]}
 record.update(changes.pop('record',{}));
 for k,v in changes.pop('package',{}).items(): setattr(pkg,k,v)
 execution.update(changes.pop('execution',{})); exe=tmp_path/'executions'; exe.mkdir(); (exe/'exec.json').write_text(json.dumps(execution))
 # Real (non-synthetic) answer engine backed by a fresh, isolated vault --
 # a brand-new tmp_path vault seeds identically to production's original
 # seed data, so behavior is unchanged while never touching the real
 # app/data/application_answer_vault.json.
 answers=ApplicationAnswerEngine(ApplicationAnswerVault(tmp_path/'vault.json'))
 return FinalReviewService(Packages(record,pkg),tmp_path/'reviews',answer_engine=answers,execution_dir=exe),record,pkg,execution

def test_happy_path_review_is_idempotent_and_approval_is_metadata_only(tmp_path):
 service,record,pkg,execution=setup(tmp_path)
 review=service.create(42); assert review.review_status=='READY_FOR_HUMAN_REVIEW' and review.legal_confirmations
 assert service.create(42).review_id==review.review_id
 approved=service.approve(review.review_id); assert approved.review_status=='APPROVED_FOR_SUBMISSION'
 assert record['status']=='MANUAL_WEB_REQUIRED' and 'APPLICATION_SUBMITTED' not in str(approved.audit)

@pytest.mark.parametrize(('status','expected'), [('DIRECT_ROUTE_REQUIRED','NOT_READY'),('CAPTCHA_REQUIRED','NOT_READY'),('AUTH_REQUIRED','NOT_READY'),('MFA_REQUIRED','NOT_READY'),('ACCOUNT_CREATION_REQUIRED','NOT_READY')])
def test_blocked_execution_is_not_ready(tmp_path,status,expected): assert setup(tmp_path/status,status)[0].create(42).review_status==expected

def test_validation_unknown_missing_and_mismatch_prevent_approval(tmp_path):
 service,record,pkg,execution=setup(tmp_path/'v',record={'validation_only':True}); assert service.create(42).review_status=='NOT_READY'
 service,record,pkg,execution=setup(tmp_path/'u',execution={'unknown_required_fields':1}); assert service.create(42).review_status=='CHANGES_REQUIRED'
 service,record,pkg,execution=setup(tmp_path/'m',package={'resume_vacancy_identity':'wrong'}); assert service.create(42).review_status=='CHANGES_REQUIRED'

def test_expiration_terminal_and_final_submit_absent(tmp_path):
 service,record,pkg,execution=setup(tmp_path/'e'); review=service.create(42); service.approve(review.review_id); pkg.application_url='https://boards.greenhouse.io/changed'
 with pytest.raises(ValueError,match='expired'): service.approve(review.review_id)
 service,record,pkg,execution=setup(tmp_path/'t',record={'status':'APPLIED'}); assert service.create(42).review_status=='NOT_READY'
 service,record,pkg,execution=setup(tmp_path/'f',execution={'final_submit_detected':False}); assert service.create(42).review_status=='NOT_READY'

@pytest.mark.parametrize("priority", ["C", "D", "E"])
def test_non_ab_intelligence_priority_blocks_final_review(tmp_path, priority):
    """Task 21.17D: intelligence_priority is authoritative here too -- C/D/E
    must never reach READY_FOR_HUMAN_REVIEW regardless of legacy
    decision/remote_eligibility fields (both still say AUTO_APPLY/ELIGIBLE)."""
    service, record, pkg, execution = setup(tmp_path / priority, record={"intelligence_priority": priority})
    assert service.create(42).review_status == "NOT_READY"


def test_missing_intelligence_priority_fails_closed_at_final_review(tmp_path):
    service, record, pkg, execution = setup(tmp_path)
    del record["intelligence_priority"]
    assert service.create(42).review_status == "NOT_READY"


def test_unrecognized_intelligence_priority_fails_closed_at_final_review(tmp_path):
    service, record, pkg, execution = setup(tmp_path, record={"intelligence_priority": "NOT_A_REAL_PRIORITY"})
    assert service.create(42).review_status == "NOT_READY"


def test_priority_a_is_authorized_same_as_b_at_final_review(tmp_path):
    service, record, pkg, execution = setup(tmp_path, record={"intelligence_priority": "A"})
    assert service.create(42).review_status == "READY_FOR_HUMAN_REVIEW"


def test_review_storage_has_no_browser_secrets(tmp_path):
 service,_,_,_=setup(tmp_path); review=service.create(42); text=(tmp_path/'reviews'/f'{review.review_id}.json').read_text().lower()
 assert all(word not in text for word in ('cookie','password','csrf','otp','validitytoken'))
