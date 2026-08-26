"""Per-review explicit submission command; no bulk submission flags exist."""
from __future__ import annotations
import argparse
from app.services.application_submission_service import ApplicationSubmissionService
def main():
 p=argparse.ArgumentParser(); s=p.add_subparsers(dest='cmd',required=True)
 for x in ('inspect','simulate','submit'):
  q=s.add_parser(x); q.add_argument('--review-id',required=True)
  if x=='submit': q.add_argument('--confirm')
 a=p.parse_args(); service=ApplicationSubmissionService()
 try:
  if a.cmd=='inspect': result=service.inspect(a.review_id); print(result['status'],result['reason'])
  elif a.cmd=='simulate': result=service.simulate(a.review_id); print(result['status'],result['reason'])
  else:
   confirm=a.confirm or input(f'Type exactly SUBMIT {a.review_id} to continue: ')
   r=service.submit(a.review_id,confirm); print(r.outcome); print('Tracker applied:', 'YES' if r.tracker_updated else 'NO'); print('Gmail sent: NO')
 finally: service.history.close()
if __name__=='__main__': main()
