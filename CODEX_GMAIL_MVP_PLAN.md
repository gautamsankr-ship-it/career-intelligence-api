# Codex CLI Plan — Career Intelligence Gmail Auto-Application MVP

## 1. Locked Product Goal

Build a **simple personal job-application automation tool**, not a commercial platform.

The MVP workflow is:

```text
Discover vacancy
    ↓
Extract job description
    ↓
Analyze job
    ↓
Score against candidate profile / experience / skills
    ↓
Decision
    ├── Score < 70      → SKIP / IGNORE
    ├── Score 70–77     → REVIEW queue; no automatic send
    └── Score >= 78     → AUTO-APPLICATION candidate
                               ↓
                        Tailor resume
                               ↓
                        Tailor cover letter
                               ↓
                        Identify a valid application email
                               ↓
                        Gmail draft / send with attachments
                               ↓
                        Record application history
```

### Important interpretation

- The **Career Decision overall score** should be the authoritative screening score for the MVP.
- ATS score remains an optimization/diagnostic score for tailoring the resume; do not create a second competing application threshold unless explicitly requested later.
- Recruiter score can remain advisory, but it must not override the locked `70 / 78` screening bands.
- Do not add unnecessary microservices, dashboards, databases, agents, or abstractions.
- Gmail is the first email provider. Outlook can be added later.

---

## 2. Current Repository Status Confirmed

The project already contains a functioning core pipeline:

- LinkedIn job discovery through **Apify**
- Cached job loading
- Duplicate removal
- OpenAI job-description analysis
- Employer analysis
- Deterministic career scoring
- ATS keyword extraction / evidence matching / ATS scoring
- Resume strategy
- Resume composition
- Markdown resume generation
- DOCX resume generation
- AI cover-letter generation
- Markdown + DOCX cover-letter generation
- FastAPI / Swagger endpoints
- Batch `CareerAgent` flow

Current AI responsibilities:

- `app/services/ai_service.py` → OpenAI `gpt-4.1-mini` for job analysis
- `app/services/employer_service.py` → OpenAI `gpt-4.1-mini`
- `app/services/cover_letter_service.py` → OpenAI `gpt-4.1-mini`
- Career scoring and ATS scoring are primarily deterministic Python logic.
- Job scraping uses Apify, not OpenAI.

### Current mismatches with the locked goal

1. `CareerDecisionEngine` currently uses:
   - `>= 90` → `APPROVE_AND_SEND`
   - `>= 75` → `GENERATE_AND_QUEUE`
   - `< 75` → `REJECT`

2. `app/config.py` currently contains:
   - `MINIMUM_MATCH_SCORE = 90`
   - `AUTO_APPLY_SCORE = 90`

3. `RecruiterReasoningService` currently uses:
   - `>= 85` → APPLY
   - `>= 70` → REVIEW
   - `< 70` → SKIP

4. These three threshold systems conflict with the frozen product rule:
   - `<70` skip
   - `70–77` review
   - `>=78` automatic-application candidate

5. `ApplicationService.generate_documents()` currently generates the resume and cover letter **before any screening gate is enforced**, so even unsuitable jobs can trigger OpenAI calls and document generation.

6. `ApplicationQueue` is in-memory only. It does not prevent duplicate applications across program runs.

7. Gmail integration does not exist.

8. No persistent application history exists.

9. The repository contains many generated/runtime artifacts:
   - `__pycache__/`
   - `.pyc`
   - `/output/`
   - `/applications/`
   - old test/generated documents

10. `requirements.txt` is encoded as UTF-16 and is a large environment dump rather than a clean project dependency file. It currently omits important declared dependencies such as FastAPI, Apify client, pytest, and future Gmail libraries.

11. `README.md` is currently empty.

12. PDF export is not implemented. **Do not prioritize PDF before Gmail.** DOCX is sufficient for the Gmail MVP.

---

## 3. Development Rules

1. Work on **one task at a time**.
2. Do not restart broad architectural refactoring.
3. Do not create compatibility wrappers for deprecated APIs.
4. Do not preserve obsolete interfaces merely to satisfy old tests.
5. Update direct callers when an active interface changes.
6. Keep the application runnable after every task.
7. Run only relevant tests/checks.
8. Use Windows PowerShell-compatible commands.
9. Do not use `grep`; use `rg`, `Select-String`, or PowerShell equivalents.
10. Do not modify the candidate's factual profile merely to improve a score.
11. Do not fabricate qualifications, experience, projects, achievements, or ATS evidence.
12. Never commit:
    - `.env`
    - Gmail `credentials.json`
    - Gmail OAuth `token.json`
    - API keys
    - secrets
13. Do not automatically email a recruiter/contact address unless the vacancy explicitly indicates that applications/CVs/resumes should be sent to that email.
14. If a vacancy only provides a web application link, classify it as `MANUAL_WEB_REQUIRED`; do not invent an email recipient.
15. Initially implement Gmail in dry-run/draft mode. Auto-send is enabled only after a successful test to a safe recipient.

---

# 4. Critical-Path Tasks

## Task 0 — Repository Hygiene and Baseline

**Estimated Codex time: 45–75 minutes**

Do only the cleanup required to make further development safe.

### Required work

- Confirm current active entrypoints:
  - `app/main.py`
  - `apply_jobs.py`
  - `refresh_jobs.py`
- Confirm `python -m compileall app` succeeds.
- Run a minimal deterministic test subset.
- Convert `requirements.txt` to UTF-8.
- Replace the environment dump with a minimal project dependency list derived from active imports.
- Include at minimum as required by active code:
  - `fastapi`
  - `uvicorn`
  - `pydantic`
  - `openai`
  - `python-dotenv`
  - `python-docx`
  - `apify-client`
  - `pytest`
- Gmail dependencies are added in Task 4, not necessarily here.
- Update `.gitignore` to exclude:
  - `__pycache__/`
  - `*.pyc`
  - `.pytest_cache/`
  - `.venv/`
  - `.env`
  - `/output/`
  - `/applications/`
  - runtime cache files where appropriate
  - `credentials*.json`
  - `client_secret*.json`
  - `token*.json`
- Remove generated caches/artifacts from the working tree if they are not intentional fixtures.
- Preserve `master_candidate_profile.json`, knowledge-base JSON files, and intentional test fixtures.
- Do **not** delete source modules just because they appear old. Only delete a source file if reference search proves it is unused by active code/tests and removal does not break compilation.

### Stop after Task 0.

---

## Task 1 — Lock the Screening Policy

**Estimated Codex time: 45–75 minutes**

Centralize the product decision thresholds in `app/config.py`.

Required policy:

```text
score < 70      → SKIP
70 <= score <78 → REVIEW
score >= 78     → AUTO_APPLY
```

Requirements:

- One authoritative screening policy.
- CareerDecision overall score is the MVP screening score.
- Remove conflicting hard-coded 75/85/90 screening thresholds from active decision/queue logic.
- ATS and recruiter scores remain visible as diagnostics/advisory metrics only.
- Add deterministic unit tests at boundary values:
  - 69.9
  - 70
  - 77.9
  - 78
  - 90
- Do not generate documents as part of this task.

### Stop after Task 1.

---

## Task 2 — Split Evaluation From Document Generation

**Estimated Codex time: 60–90 minutes**

Current `ApplicationService.generate_documents()` analyzes, scores, and generates files for every vacancy.

Change the flow so it is cheap to screen jobs first.

Target:

```text
evaluate_job(job_description)
    → profile
    → job_analysis
    → employer
    → career_decision
    → ats_result
    → recruiter/advisory score if retained

Only when score >= 78:
    generate_application_documents(evaluation)
        → resume strategy
        → resume composition
        → Resume.md
        → Resume.docx
        → CoverLetter.md
        → CoverLetter.docx
```

For score 70–77:
- retain evaluation in REVIEW
- no automatic Gmail send

For score <70:
- SKIP
- do not call cover-letter generation
- do not generate CV files

Update `CareerAgent`, queue logic, and API callers accordingly.

### Stop after Task 2.

---

## Task 3 — Persistent Application History and Duplicate Prevention

**Estimated Codex time: 60–90 minutes**

Use **SQLite from Python's standard library** to keep the project simple.

Create a lightweight application history service.

Minimum fields:

- id
- job fingerprint
- source
- job_url
- company
- job_title
- score
- ats_score
- decision (`SKIP`, `REVIEW`, `AUTO_APPLY`)
- application_method
- recipient_email
- resume_path
- cover_letter_path
- status
- discovered_at
- processed_at
- sent_at
- error_message

Create a stable unique fingerprint from preferably:
1. source + external job id, or
2. normalized job URL, or
3. company + title + location + description hash

Before processing/sending:
- check history
- never send the same vacancy twice

Keep the current in-memory queue only if it remains useful for the current run; SQLite is the persistent source of truth.

### Stop after Task 3.

---

## Task 4 — Gmail OAuth and Gmail Service

**Estimated Codex time: 90–150 minutes plus 20–40 minutes manual Google OAuth setup**

Add only the dependencies needed for Gmail:

- `google-api-python-client`
- `google-auth`
- `google-auth-oauthlib`
- `google-auth-httplib2`

Implement `GmailService`.

Responsibilities:

- OAuth authentication for one personal Gmail account
- store `token.json` locally but gitignore it
- create draft
- optionally send draft/message
- MIME attachments
- attach:
  - `Resume.docx`
  - `CoverLetter.docx`
- plain professional email body
- return Gmail message/draft id

Configuration:

```text
GMAIL_DRY_RUN = True
GMAIL_AUTO_SEND = False
```

First test:
- send/draft to a safe test address controlled by the user
- never auto-send to a real employer during initial verification

Do not add Outlook in this task.

### Stop after Task 4.

---

## Task 5 — Application Email Detection and Safety Gate

**Estimated Codex time: 60–90 minutes**

Extract possible application email addresses from the vacancy/job metadata.

Important: an email appearing in a job description is not automatically an application address.

Classify:

- `EXPLICIT_APPLICATION_EMAIL`
- `CONTACT_ONLY_EMAIL`
- `NO_EMAIL`
- `WEB_APPLICATION_ONLY`

Auto-email is permitted only when text explicitly indicates an application instruction, e.g.:

- "email your resume to..."
- "send your CV to..."
- "apply by email..."
- "email your application to..."

If wording is only:

- "contact X for a confidential discussion"
- "for questions email..."
- "fraud queries..."
- general corporate contact

then do **not** auto-send.

If no explicit application email exists:
- status = `MANUAL_WEB_REQUIRED` for >=78 roles
- preserve the job URL
- do not invent a recipient

Add deterministic tests with realistic examples.

### Stop after Task 5.

---

## Task 6 — End-to-End Gmail Auto-Application Orchestrator

**Estimated Codex time: 90–150 minutes**

Create one simple orchestration command/script.

Example:

```powershell
python auto_apply.py
```

Flow:

```text
load cached/discovered jobs
    ↓
skip already processed fingerprints
    ↓
evaluate vacancy
    ↓
<70
    → save SKIP
    → next job

70–77
    → save REVIEW
    → next job

>=78
    → determine application method

        explicit application email
            → generate resume + cover letter
            → Gmail draft while dry-run
            → record DRAFTED

        no valid application email / ATS portal
            → generate documents if useful
            → record MANUAL_WEB_REQUIRED
```

After successful dry-run validation, support configuration:

```text
GMAIL_AUTO_SEND = True
```

When enabled:
- only `>=78`
- only explicit application-email vacancies
- only unsent fingerprints
- send attachments
- persist Gmail message id + sent timestamp
- failure must not mark job as sent

Produce a run summary:

- discovered
- skipped duplicate
- score <70
- review 70–77
- eligible >=78
- Gmail drafts/sent
- manual-web-required
- failed

### Stop after Task 6.

---

## Task 7 — Final MVP Validation and Simple Scheduling

**Estimated Codex time: 60–90 minutes**

Run an end-to-end test against a small controlled sample.

Validate:

- job discovery/cache
- job analysis
- scoring boundaries
- ATS diagnostics
- no document generation for <78 unless specifically requested
- tailored resume
- tailored cover letter
- duplicate prevention
- email-address classification
- Gmail draft
- attachment integrity
- application-history update
- failure handling

Then document:

```powershell
python refresh_jobs.py
python auto_apply.py
uvicorn app.main:app --reload
```

Optionally provide a short Windows Task Scheduler setup for periodic execution.

Do not build a complex scheduler service.

### Stop after Task 7.

---

# 5. Deferred Until After the Gmail MVP

Do not spend MVP time on:

- Outlook integration
- PDF export
- commercial multi-user architecture
- complex dashboard
- cloud deployment
- browser automation for Workday/LinkedIn/Indeed
- web-form auto-submission
- unnecessary service restructuring
- large UI redesign
- full historic test-suite rewrite

These can be considered only after the first successful Gmail-based application workflow.

---

# 6. Estimated Remaining Effort

| Task | Estimated focused time |
|---|---:|
| Task 0 — Cleanup/baseline | 0.75–1.25 h |
| Task 1 — 70/78 policy | 0.75–1.25 h |
| Task 2 — Evaluation/document gate | 1–1.5 h |
| Task 3 — SQLite history/dedup | 1–1.5 h |
| Task 4 — Gmail OAuth/service | 1.5–2.5 h + OAuth setup |
| Task 5 — Email safety classifier | 1–1.5 h |
| Task 6 — Auto-apply orchestrator | 1.5–2.5 h |
| Task 7 — End-to-end validation | 1–1.5 h |

**Expected engineering effort:** approximately **8–13 focused hours**, plus Google OAuth setup.

With Codex CLI, this should be treated as approximately **8 focused Codex tasks/prompts**, not another broad multi-week refactor.

---

# 7. FIRST CODEX PROMPT

Use this prompt first. Do not ask Codex to perform all tasks simultaneously.

```text
Read this file completely and treat it as the locked product plan:

CODEX_GMAIL_MVP_PLAN.md

We are no longer doing a broad refactor.

The objective is the simplest personal MVP that can:
discover jobs → analyze → score → skip/review/auto-apply → tailor CV and cover letter → apply through Gmail when an explicit application email exists.

Execute TASK 0 ONLY: Repository Hygiene and Baseline.

Rules:
- Do not start Task 1.
- Do not redesign the architecture.
- Do not create compatibility wrappers.
- Do not delete source code unless reference search proves it is unused.
- Preserve candidate/profile/knowledge data.
- Convert requirements.txt to clean UTF-8 and make it reflect actual active dependencies.
- Clean runtime/generated artifacts and improve .gitignore.
- Run compile/static checks and only relevant tests.
- Use Windows PowerShell-compatible commands.
- Summarize exactly:
  1. files changed,
  2. files deleted,
  3. checks run,
  4. remaining blockers.
- Stop and wait for approval.
```

---

# 8. Definition of Done

The Gmail MVP is complete when one command can process real vacancies and safely achieve:

```text
<70        → ignored and recorded
70–77      → review queue and recorded
>=78       → application candidate

AND, for >=78:
    explicit apply-by-email address
        → tailored Resume.docx
        → tailored CoverLetter.docx
        → Gmail draft/send
        → persistent sent record
        → duplicate protection

    no explicit apply-by-email address
        → MANUAL_WEB_REQUIRED
        → job URL retained
        → no inappropriate email sent
```

No additional refactoring is required to call this MVP complete.
