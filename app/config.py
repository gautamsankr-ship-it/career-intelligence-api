"""
Career Intelligence Platform
Global Configuration
"""

# ==========================================================
# DEVELOPMENT
# ==========================================================

USE_CACHE = True

MAX_JOBS = 5

# ==========================================================
# JOB FILTERS
# ==========================================================

REMOTE_ONLY = True

MINIMUM_MATCH_SCORE = 90

AUTO_APPLY_SCORE = 90

# ==========================================================
# AI
# ==========================================================

OPENAI_MODEL = "gpt-4.1-mini"

# ==========================================================
# CACHE
# ==========================================================

CACHE_FOLDER = "app/data/cache"

RAW_JOB_CACHE = "raw_jobs.json"

JOB_ANALYSIS_CACHE = "analyzed_jobs.json"

EMPLOYER_CACHE = "employer_analysis.json"

RECRUITER_CACHE = "recruiter_analysis.json"

SCORING_CACHE = "scoring_results.json"

RESUME_CACHE = "resume_packages.json"

# ==========================================================
# PLATFORM
# ==========================================================

DEVELOPMENT_MODE = True