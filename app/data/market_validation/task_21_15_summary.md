# Task 21.15 — Real-Market Validation Summary

**Date:** 2026-08-29
**Mode:** Analysis only. No application documents generated, no Gmail drafts created/modified, no browser actions, no tracker writes, no APPLIED status set.

## Dataset

- Inspected 3 real data sources: `application_history.db` tracker (40 rows), `app/data/cache/raw_jobs.json` (12), `app/data/cache/arrangement_review_jobs.json` (27).
- 70 unique real vacancies by (company, job_title) across all 3 sources; 40 carry a usable job_description.
- All 40 processed through the frozen funnel (real `ApplicationService.evaluate_job()` + `JobIntelligenceService.evaluate()`, real OpenAI calls, zero synthetic vacancies).
- 100% of real data is LinkedIn-sourced. Indeed is configured but has produced zero real cached/tracked vacancies.

## Funnel result

Unique 40 → Valid 31 (77.5%) → Eligible 20 (64.5% of valid) → Competitive 19 (95.0% of eligible) → **A/B 0 (0.0% of competitive)**.

## Priority distribution

C (HUMAN_REVIEW) 34/40 (85.0%), D (WATCH) 2/40 (5.0%), E (REJECT) 4/40 (10.0%), A/B 0/40 (0.0%).

## Headline finding

**Zero A/B outcomes is a calibration artifact, not a market-quality result.** `opportunity_value` came out LOW for 40/40 (100%) real vacancies because `EmployerService`'s employer-quality scores (returned on a conventional 0-10 scale by the model) are compared against `_tier()` thresholds written for a 0-100 scale (50/75). This structurally makes PRIORITY_APPLY (A) unreachable and forces AUTO_APPLY-screened vacancies to WATCH (D) instead of APPLY (B) — independent of actual employer or role quality. See `calibration_findings[0]` in the JSON artifact for full evidence.

A secondary, medium-severity finding: the behavioural-marker list used to distinguish soft-skill phrasing from factual requirements is missing several observed real-world phrases ("analytical skills", "cross-functional collaboration", "prioritization", etc.), inflating the HUMAN_REVIEW volume beyond genuine factual uncertainty.

## Quality-Adjusted Vacancy Coverage

`NOT_YET_MEASURABLE` — no independent benchmark-universe estimate exists yet, and the funnel currently returns 0 A/B outcomes for a calibration reason, so even a trustworthy numerator isn't available.

## Recommendation

`CALIBRATE_FIRST` — see full JSON artifact (`task_21_15_validation.json`) for the complete per-vacancy table, source benchmark, and calibration/source-gap findings.
