"""Human final-review metadata CLI. Approval never submits an application."""
from __future__ import annotations
import argparse, json
from app.services.final_review_service import FinalReviewService

def show(r, as_json=False):
    if as_json: print(json.dumps(r.to_dict(),indent=2)); return
    print("APPLICATION FINAL REVIEW"); print(f"Review ID: {r.review_id}\nTracker ID: {r.tracker_id}\nCompany: {r.company}\nRole: {r.job_title}\nPortal: {r.application_portal}")
    print(f"Fields: detected={r.fields_detected}, filled={r.fields_filled}, skipped={r.fields_skipped}, manual={r.manual_review_fields}, unknown required={r.unknown_required_fields}")
    print(f"Resume: {r.resume_uploaded} | Cover letter: {r.cover_letter_uploaded}\nFinal submit: {'YES' if r.final_submit_detected else 'NO'} | Clicked: NO")
    print(f"Manual confirmations: {', '.join(r.legal_confirmations) or 'None'}\nReview Status: {r.review_status}\nReasons: {', '.join(r.blocking_reasons) or 'None'}")
    print("APPLICATION SUBMITTED: NO | TRACKER APPLIED: NO")
def main():
    p=argparse.ArgumentParser(description="Metadata-only final review; no submission exists."); s=p.add_subparsers(dest="cmd",required=True)
    c=s.add_parser("create"); c.add_argument("--tracker-id",type=int,required=True); c.add_argument("--json",action="store_true")
    sh=s.add_parser("show"); sh.add_argument("--review-id",required=True); sh.add_argument("--json",action="store_true")
    a=s.add_parser("approve"); a.add_argument("--review-id",required=True); a.add_argument("--note",default="")
    ch=s.add_parser("changes"); ch.add_argument("--review-id",required=True); ch.add_argument("--note",required=True)
    ca=s.add_parser("cancel"); ca.add_argument("--review-id",required=True); s.add_parser("ready"); s.add_parser("list")
    args=p.parse_args(); service=FinalReviewService()
    try:
        if args.cmd=="create": show(service.create(args.tracker_id),args.json)
        elif args.cmd=="show": show(service.show(args.review_id),args.json)
        elif args.cmd=="approve": show(service.approve(args.review_id,args.note))
        elif args.cmd=="changes": show(service.changes(args.review_id,args.note))
        elif args.cmd=="cancel": show(service.cancel(args.review_id))
        else:
            for r in (service.ready() if args.cmd=="ready" else service.list()): print(f"{r.review_id} | {r.company} | {r.job_title} | {r.application_portal} | {r.review_status} | {len(r.legal_confirmations)} | {'YES' if r.final_submit_detected else 'NO'}")
    finally: service.history.close()
if __name__=="__main__": main()
