# Career Intelligence Gmail MVP

This is a personal, draft-only job-application workflow. It loads cached jobs,
scores them against the candidate profile, prevents duplicate processing, and
creates a Gmail draft only when a vacancy explicitly authorizes application by
email.

## Daily use

Refresh the local job cache:

```powershell
python refresh_jobs.py
```

Default daily sources:

- LinkedIn — verified
- Indeed — verified

Daily discovery searches Australia, the United Kingdom, and the United States
for confirmed **remote-only** finance roles. Hybrid, on-site, and unknown work
arrangements are excluded from the active processing cache. A city such as
London or Sydney does not exclude a role when the source explicitly marks it
remote. Country, residency, right-to-work, and time-zone wording is retained
as diagnostic source metadata; it does not yet change scoring.

LinkedIn requests vacancies posted in the last seven days and rotates compact
accounting, FP&A, finance-management, controller, commercial-transformation,
and professional-services query groups. It uses listing-level structured or
explicit workplace wording only; the remote search URL is never treated as
proof that a listing is remote. Indeed relies on the same downstream <=7-day
freshness gate because its actor date filter is not currently enabled.

Optional diagnostics only (not run by default):

- SEEK — current actor is unreliable
- Hays — no reliable actor is configured
- Robert Half — current actor is not live-verified: its latest run reported
  success but the actor log showed a failed scrape and returned zero jobs

## Target-employer intelligence

LinkedIn and Indeed remain the only default discovery sources. A separate,
explicit target-employer registry tracks strategically relevant accounting,
advisory, consulting, technology/data, financial-services, and recruiter
employers. It records aliases, priority tier, careers URL, ATS platform, and
whether a public structured job endpoint is currently supported. Tiers affect
discovery priority only; they never change CareerDecision or ATS scoring.

Inspect the registry locally:

```powershell
python target_employers.py summary
python target_employers.py list --tier 1
python target_employers.py ats
```

Employer-portal discovery is opt-in and bounded. `--count` is the maximum
relevant vacancies returned from each selected employer endpoint; `--max-employers` limits
how many endpoints are contacted. `--market` is applied only where the
registry has a verified market-specific ATS tenant; a generic global board is
never falsely labelled as a requested market. It is not a default daily source:

```powershell
python refresh_jobs.py --sources target_employers --employers palantir --market united_kingdom --count 1 --max-employers 1
```

Only public structured Lever and Greenhouse endpoints are currently enabled
for controlled validation. Workday, SuccessFactors, Oracle, SmartRecruiters,
and proprietary sites are catalogued for coverage but remain diagnostic/manual
until a tenant-specific public endpoint is verified. The same relevance,
<=7-day freshness, strict remote-only, duplicate, eligibility, and
application-email safety gates apply to employer-portal listings. An official
employer application URL is retained when present; no portal login or
application submission is automated.

For public Lever and Greenhouse catalogues, `--count` is applied **after** a
bounded local finance/accounting/finance-tech prefilter. Use `--scan-limit`
(default `100`) to cap catalogue entries inspected per employer, so `--count 1`
selects the first relevant candidate rather than the first arbitrary ATS job.

Discovery also labels jobs diagnostically as `CORE_FINANCE`, `FINANCE_TECH`,
`BOTH`, or `UNKNOWN`. Finance-Tech covers finance transformation, finance
systems/automation, financial data and analytics, accounting technology,
ERP/EPM, payments, and RegTech/risk technology only where the vacancy contains
clear finance, accounting, risk, or regulatory context. Generic software,
engineering, AI, data-science, and cybersecurity roles remain excluded. This
label changes neither CareerDecision nor ATS scoring; it is shown in tracker
queue and ready output for visibility.

Preview a small batch without changing history, generating documents, or creating Gmail drafts (recommended first):

```powershell
python auto_apply.py --limit 3 --preview
```

Process up to 20 new, non-duplicate vacancies from the current cache:

```powershell
python auto_apply.py --limit 20
```

The limit applies to newly evaluated vacancies. Existing history records are
skipped automatically and do not consume the limit.

## Decisions and application routes

| Career score | Decision | Automatic action |
| --- | --- | --- |
| Below 70 | `SKIP` | Recorded only; no documents or Gmail draft. |
| 70 to 77.999 | `REVIEW` | Recorded for manual review; no automatic documents or Gmail draft. |
| 78 or above | `AUTO_APPLY` | The vacancy application method is checked. |

For `AUTO_APPLY` vacancies:

- An explicit instruction to send a CV, resume, or application to an email
  address creates Resume and Cover Letter DOCX files and a Gmail draft.
- A careers portal, web-only route, contact-only address, or no verified email
  is recorded as `MANUAL_WEB_REQUIRED` when a job URL is available.
- Recruiter, support, privacy, fraud, security, and general contact addresses
  are never used automatically.

Gmail remains draft-only by default:

```python
GMAIL_DRY_RUN = True
GMAIL_AUTO_SEND = False
```

Review every Gmail draft and its attachments before manually sending it.

## Application tracker

## First controlled real-job batch

Run these commands one at a time and review each result before continuing:

```powershell
# 1. Small, bounded discovery across the verified default sources.
python refresh_jobs.py --sources linkedin,indeed --count 5

# 2. Evaluate the same cache without writing history, files, or Gmail drafts.
python auto_apply.py --limit 5 --preview

# 3. Persist the approved pipeline outcomes. This may create Gmail drafts,
#    but never sends email automatically.
python auto_apply.py --limit 5

# 4. See the human-actionable queue and the WEB subset.
python job_tracker.py ready
python job_tracker.py queue
python job_tracker.py list --status MANUAL_WEB_REQUIRED
```

Preview stores a short-lived local evaluation snapshot only. When the vacancy
description, current profile, and scoring configuration are unchanged, the
following normal processing command reuses the exact reviewed Career score,
ATS score, decision, analysis, and remote-eligibility result. The snapshot is
consumed after use and expires after four hours; a changed or expired snapshot
causes a fresh evaluation instead.

`--count 5` requests up to five raw vacancies per target market for each
enabled source (up to 15 requested results per source across UK, USA, and
Australia). LinkedIn uses one compact actor call per market and rotates its
finance/professional-services query groups, rather than exhausting a tiny
global cap on the first role URL. If Indeed is temporarily unavailable because of an
actor plan limit, LinkedIn results are still retained. Cache refreshes merge
successful source/market results and replace only the scopes that refreshed
successfully; the SQLite tracker remains the authority for duplicate
application protection.

For a WEB vacancy, open the listed application URL (or job URL when no
application URL is supplied), submit the form yourself, then explicitly record
the submission:

```powershell
python job_tracker.py applied <ID>
python job_tracker.py note <ID> --notes "Applied through employer careers portal"
```

This records `applied_at`; opening a URL never marks an application as sent.
For an EMAIL vacancy, normal processing creates a Gmail `DRAFTED` record with
tailored documents only when the job explicitly authorizes email submission.
Review and manually send that draft, then run `python job_tracker.py applied <ID>`.

```powershell
python refresh_jobs.py
python auto_apply.py --limit 20
python job_tracker.py list --status MANUAL_WEB_REQUIRED
python job_tracker.py list --eligibility MANUAL_REVIEW
python job_tracker.py backfill-eligibility
python job_tracker.py ready
python job_tracker.py applied <ID>
python job_tracker.py list --status APPLIED
python job_tracker.py interview <ID> --stage "First interview" --date 2026-09-05
python job_tracker.py offer <ID>
python job_tracker.py rejected <ID>
python job_tracker.py withdrawn <ID>
python job_tracker.py note <ID> --notes "Applied through employer website."
python job_tracker.py summary
```

Use the numeric ID shown by `list`. The existing history database continues to
skip duplicate vacancies. A Gmail draft is not an application: mark it
`APPLIED` only after sending it or submitting the external web form.

## Daily application workflow

```powershell
python refresh_jobs.py --sources linkedin --count 5
python auto_apply.py --limit 15 --preview
python auto_apply.py --limit 15
python job_tracker.py queue
python job_tracker.py applied <ID>
python job_tracker.py today
python job_tracker.py pipeline
```

`queue` prioritizes ready WEB applications, then remote-eligibility review,
then CareerDecision review. It never includes already applied, withdrawn,
rejected, failed, skipped, or currently ineligible vacancies. URLs are shown
in the safe order: explicit application URL, then job URL.

Use manual remote-eligibility decisions only after obtaining reliable evidence:

```powershell
python job_tracker.py eligibility <ID> eligible --note "Employer confirmed international remote workers accepted"
python job_tracker.py eligibility <ID> ineligible --note "UK residence required"
python job_tracker.py review <ID> proceed --note "Relevant transferable experience; applying manually"
python job_tracker.py arrangement-review
```

Manual eligibility decisions retain the previous value, timestamp, reason, and
`MANUAL` source. They never alter the CareerDecision score or thresholds.

`ready` lists only current vacancies with an `AUTO_APPLY` career decision,
explicitly eligible remote work from the current location, and a verified web
or email route. `backfill-eligibility` safely fills missing remote-eligibility
metadata only when stored vacancy text is sufficient; it never re-scores,
generates documents, creates drafts, or changes lifecycle status.

## Optional Windows Task Scheduler setup

Create two basic tasks using the project folder as **Start in**:

1. Refresh vacancies: `python refresh_jobs.py`
2. Process the cache: `python auto_apply.py --limit 20`

Schedule the processing task after the refresh task. The workflow does not
send messages automatically; it only creates safe Gmail drafts when an
explicit application email is present.
# Application Answer Vault

`application_answers.py` provides a local, versioned answer vault for a future form client; it does not open or submit browser forms.  It resolves every question to `AUTO_FILL`, `AUTO_FILL_WITH_RULES`, or `MANUAL_REVIEW`, with confidence and provenance.  Only approved, high-confidence profile facts and rules may auto-fill; legal, demographic, compensation, notice-period, and unsupported answers remain manual by default.

```powershell
python application_answers.py summary
python application_answers.py list
python application_answers.py show WORK_AUTHORIZATION_UK
python application_answers.py resolve "Do you have the right to work in the UK?" --market united_kingdom
python application_answers.py learn NOTICE_PERIOD "Two weeks"
python application_answers.py approve NOTICE_PERIOD --reason "Candidate confirmed"
```
