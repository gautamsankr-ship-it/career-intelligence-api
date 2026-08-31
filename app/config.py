"""
Career Intelligence Platform
Global Configuration
"""

import os

# ==========================================================
# DEVELOPMENT
# ==========================================================

USE_CACHE = True

MAX_JOBS = 5

# ==========================================================
# JOB FILTERS
# ==========================================================

REMOTE_ONLY = True

# Current personal operating context for the remote-work eligibility gate.
CURRENT_WORK_COUNTRY = "Nepal"
WORK_AUTHORIZATION = {
    "Nepal": True,
    "United Kingdom": False,
    "United States": False,
    "Australia": False,
}

# Only verified daily sources run by default. Optional sources remain available
# through ``python refresh_jobs.py --sources <source>`` for controlled tests.
JOB_SOURCES = ("linkedin", "indeed")
OPTIONAL_JOB_SOURCES = ("robert_half", "seek", "hays")
JOB_SOURCE_STATUS = {
    "linkedin": "VERIFIED",
    "indeed": "VERIFIED",
    "seek": "UNRELIABLE",
    "hays": "UNCONFIGURED",
    # The actor can report SUCCEEDED while its own logs show a failed scrape
    # and no listings. Keep it available for future retesting only.
    "robert_half": "UNRELIABLE",
}
JOB_SOURCE_MAX_RESULTS = 20
INDEED_APIFY_ACTOR_ID = "agentx/indeed-jobs-scraper"
INDEED_COUNTRY = "Australia"
INDEED_LOCATION = ""
SEEK_APIFY_ACTOR_ID = "soft_alexist/seek-jobs-search-scraper"
# Task 21.17A: the configured actor's own documented input schema scrapes a
# SEEK listing-page URL directly (it does not run a query against SEEK's
# search API) and expects SEEK's alternate `au.seek.com` category-listing
# domain/path convention (e.g. "https://au.seek.com/software-engineer-jobs/
# in-All-Sydney-NSW"), not the consumer-facing "www.seek.com.au/jobs?keywords="
# query-string search this was previously pointed at. The old URL was on the
# wrong domain/path shape for this actor and returned 0 items every run
# (confirmed via a live bounded test, not assumed). "jobs-in-accounting" is
# SEEK's own real category slug for accounting vacancies nationwide (verified
# to exist and list real content), matching the candidate's core target
# domain without narrowing to one city/state.
SEEK_SEARCH_URLS = ("https://au.seek.com/jobs-in-accounting",)
# No maintained Hays-specific actor was identified. Set this to an approved
# Apify actor ID before enabling live Hays runs; an empty value fails safely.
HAYS_APIFY_ACTOR_ID = ""
HAYS_SEARCH_URLS = ("https://www.hays.com.au/job-search",)
ROBERT_HALF_APIFY_ACTOR_ID = "alexist/roberthalf-jobs-search-scraper"
ROBERT_HALF_SEARCH_URLS = ("https://www.roberthalf.com/au/en/jobs/all/all?page=1",)

SCREENING_REVIEW_THRESHOLD = 70

SCREENING_AUTO_APPLY_THRESHOLD = 78

SCREENING_SKIP = "SKIP"
SCREENING_REVIEW = "REVIEW"
SCREENING_AUTO_APPLY = "AUTO_APPLY"


def screening_decision(score: float) -> str:
    """Return the authoritative MVP screening decision for a Career score."""
    if score < SCREENING_REVIEW_THRESHOLD:
        return SCREENING_SKIP
    if score < SCREENING_AUTO_APPLY_THRESHOLD:
        return SCREENING_REVIEW
    return SCREENING_AUTO_APPLY

# ==========================================================
# AI
# ==========================================================

OPENAI_MODEL = "gpt-4.1-mini"

# ==========================================================
# CACHE
# ==========================================================

CACHE_FOLDER = "app/data/cache"

APPLICATION_HISTORY_DB = "app/data/application_history.db"

GMAIL_DRY_RUN = True
GMAIL_AUTO_SEND = False
GMAIL_CREDENTIALS_PATH = "credentials.json"
GMAIL_TOKEN_PATH = "token.json"
GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.compose",)
# The authenticated account's own primary address. Without an explicit From
# header, drafts/sends fall back to whichever "Send As" alias is currently
# marked default in the account's own Gmail settings, which may not be this
# address -- so application email must always set From explicitly.
GMAIL_SENDER_ADDRESS = "gautamsankr@gmail.com"

# Browser application preview is deliberately read-only in Task 21.
APPLICATION_DRY_RUN = True
APPLICATION_AUTO_SUBMIT = False
APPLICATION_BROWSER_TIMEOUT_MS = 30_000
APPLICATION_PREVIEW_FOLDER = "app/data/application_previews"

# Task 21.28: browser session mode. ISOLATED (fresh, cookie-less context per
# call -- "isolated: no imported browser profile/cookies") remains the
# default for all existing Greenhouse/Lever/ATS preparation and tests.
# PERSISTENT_AUTHENTICATED is an explicit, opt-in mode where a human has
# already logged into a dedicated local Chromium profile by hand; the
# software only ever reuses that already-authenticated session and never
# performs credential login itself (see
# ApplicationBrowserService.open_persistent_session).
APPLICATION_BROWSER_SESSION_MODE_ISOLATED = "ISOLATED"
APPLICATION_BROWSER_SESSION_MODE_PERSISTENT_AUTHENTICATED = "PERSISTENT_AUTHENTICATED"
APPLICATION_BROWSER_SESSION_MODE = os.getenv(
    "APPLICATION_BROWSER_SESSION_MODE", APPLICATION_BROWSER_SESSION_MODE_ISOLATED
)
# Local, outside-repository directory a human has manually logged into.
# Deliberately supplied only through local environment -- never a literal
# path here, and never a repository-relative default -- so a profile
# directory can never be accidentally committed. Persistent mode fails
# closed (PersistentProfileNotConfiguredError) when this is unset, and
# fails closed again (PersistentProfileInsideRepositoryError) if it ever
# resolves to a path inside this repository.
APPLICATION_PERSISTENT_BROWSER_PROFILE_DIR = os.getenv(
    "APPLICATION_PERSISTENT_BROWSER_PROFILE_DIR", ""
)

RAW_JOB_CACHE = "raw_jobs.json"

JOB_ANALYSIS_CACHE = "analyzed_jobs.json"

EMPLOYER_CACHE = "employer_analysis.json"

RECRUITER_CACHE = "recruiter_analysis.json"

SCORING_CACHE = "scoring_results.json"

RESUME_CACHE = "resume_packages.json"

# Short-lived local hand-off from a user-reviewed preview to normal processing.
# This is deliberately separate from application history and never blocks jobs.
PREVIEW_EVALUATION_SNAPSHOT_TTL_SECONDS = 4 * 60 * 60

# ==========================================================
# PLATFORM
# ==========================================================

DEVELOPMENT_MODE = True
