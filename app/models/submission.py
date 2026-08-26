from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
@dataclass
class SubmissionAuthorization:
 review_id:str; tracker_id:int; package_id:str; execution_id:str; fingerprint:str; authorized_at:str=field(default_factory=lambda:datetime.now(timezone.utc).isoformat())
@dataclass(frozen=True)
class SubmissionContext:
 review_id:str; tracker_id:int; package_id:str; execution_id:str; portal:str; application_url:str; authorized_fingerprint:str
@dataclass
class SubmissionReceipt:
 submission_id:str=field(default_factory=lambda:uuid4().hex); review_id:str=""; tracker_id:int=0; package_id:str=""; execution_id:str=""; company:str=""; job_title:str=""; portal:str=""; application_url:str=""; authorized_at:str=""; submit_clicked_at:str=""; confirmed_at:str=""; outcome:str="SUBMISSION_BLOCKED"; confirmation_signals:list[str]=field(default_factory=list); tracker_updated:bool=False; gmail_sent:bool=False; audit:list[dict]=field(default_factory=list); created_at:str=field(default_factory=lambda:datetime.now(timezone.utc).isoformat())
 def to_dict(self): return asdict(self)
