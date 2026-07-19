# Refactor Plan

Source note: `ARCHITECTURE_REVIEW.md` was not present in the workspace at review time. This plan is based on the repository architecture review findings from the read-only inspection.

## Phase 1 - Critical

These items block normal application startup, core workflows, or reliable installation.

1. Restore import correctness for active entrypoints.
   - `app/main.py` imports `optimize_resume`, but `app/services/resume_optimizer.py` exposes `ResumeOptimizer`.
   - `app/main.py` imports `build_application`, but `app/services/application_service.py` only exposes `build_decision`.
   - `app/services/career_agent.py` imports `build_decision` from `application_service`, which currently imports missing modules.

2. Fix stale module references in `application_service.py`.
   - Replace references to removed modules:
     - `app.services.candidate_service`
     - `app.services.job_analysis`
     - `app.services.application_context`
     - `app.services.career_decision_engine`
   - Align with current modules:
     - `app.services.profile_service`
     - `app.services.ai_service`
     - `app.models.application_context`
     - `app.services.career_engine`

3. Fix `ResumeGenerator` package import.
   - `app/services/resume_generator.py` imports `services.resume_composer`.
   - It should use the package-local path used elsewhere in the repo.

4. Repair active resume-generation API flow.
   - Decide whether `/generate-resume` should use the old function-style DOCX path or the newer `ResumeGenerator` class pipeline.
   - Make the endpoint call signatures match the selected implementation.
   - Ensure generated file paths and returned metadata are consistent.

5. Complete dependency declarations.
   - Add missing runtime dependencies such as `fastapi` and `apify-client`.
   - Remove or separate unrelated notebook/UI dependencies if they are not required by the API.

6. Establish one runnable smoke path.
   - Define the supported startup command.
   - Confirm `/health` imports without OpenAI or Apify credentials.
   - Confirm one non-network test path for scoring and profile matching.

## Phase 2 - Important

These items do not all block startup, but they cause inconsistent behavior, unreliable tests, or confusing architecture.

1. Consolidate service boundaries.
   - Keep OpenAI analysis, deterministic scoring, ATS analysis, resume composition, and document rendering as separate layers.
   - Remove duplicate or obsolete service modules once callers have migrated.
   - Clarify whether `application_service.py` is the primary orchestration layer or whether `CareerAgent` owns batch orchestration.

2. Normalize the candidate profile schema.
   - `master_candidate_profile.json` uses top-level `candidate`.
   - `profile_builder.py` reads `personal_information`, which likely produces empty candidate metadata in rebuilt profile intelligence.
   - Choose one canonical profile schema and update all readers.

3. Centralize configuration.
   - Use `app/config.py` for model names, cache locations, score thresholds, and development mode.
   - Remove hard-coded `"gpt-4.1-mini"` values from OpenAI service modules.
   - Decide which settings are environment variables versus committed defaults.

4. Make generated data boundaries explicit.
   - Decide whether `app/data/cache`, `output`, and `applications` are source-controlled fixtures or runtime artifacts.
   - Update `.gitignore` accordingly.
   - Keep sample fixtures separate from live scraped job data.

5. Convert tests from scripts to real tests.
   - Remove top-level execution from test files.
   - Replace prints with assertions.
   - Mock OpenAI and Apify calls.
   - Use temporary directories for file generation tests.

6. Split network-dependent tests from deterministic tests.
   - Mark OpenAI and Apify integration tests explicitly.
   - Keep the default test suite offline and deterministic.
   - Add fixtures for representative job analyses, employer analyses, ATS results, and candidate profile slices.

7. Align queue and dashboard vocabulary.
   - `ApplicationQueue` exposes `ready`, `pending`, and `rejected`.
   - Some callers/tests refer to `approved`, `ready`, `review`, or `rejected` inconsistently.
   - Define canonical statuses and map user-facing labels separately.

8. Reconcile old scoring helpers with newer scorer classes.
   - Legacy modules such as `scoring/skills.py`, `scoring/experience.py`, and placeholder files coexist with newer class-based scorers.
   - Either keep compatibility wrappers intentionally or remove old APIs after tests are migrated.

9. Add error handling around AI JSON parsing.
   - OpenAI services assume valid JSON and do not handle malformed responses.
   - Introduce validation, graceful failures, and observability around AI response parsing.

10. Clarify cache serialization contracts.
    - `CacheService` serializes dataclasses by walking `__dict__`, stringifying unsupported values.
    - This can degrade nested types such as timeline events.
    - Define explicit serialization/deserialization for `CareerOpportunity`.

## Phase 3 - Cleanup

These items improve maintainability, presentation, and long-term quality after the critical paths are stable.

1. Fix text encoding issues.
   - Replace mojibake in console output, comments, docstrings, and `index.html`.
   - Standardize all source files as UTF-8.

2. Create a real README.
   - Document project purpose, architecture, setup, required environment variables, run commands, test commands, and generated artifact locations.

3. Add an architecture diagram or ADR.
   - Document the intended pipeline from discovery through resume generation.
   - Record decisions about service ownership, data contracts, and external integrations.

4. Remove unused imports and dead code.
   - Examples include unused imports in cache/document services and unreachable code after early returns.
   - Do this after Phase 1 import repairs to avoid removing code that is still needed during migration.

5. Standardize naming.
   - Pick consistent names for job analysis, career decision, recruiter decision, resume strategy, ATS result, and application context.
   - Avoid multiple names for the same concept across API, CLI, services, and tests.

6. Replace print-heavy service internals with logging.
   - Keep CLI presentation in scripts.
   - Move service diagnostics to structured logging or caller-controlled verbosity.

7. Add type validation for major service contracts.
   - Consider Pydantic models or explicit dataclasses for job analysis, ATS result, resume strategy, and serialized opportunities.
   - Reduce raw `dict` coupling across service boundaries.

8. Separate API templates from operational dashboard concerns.
   - The current HTML page mixes analysis, resume generation fields, and apply behavior.
   - Align the UI with the supported backend endpoints after Phase 1.

9. Review dependency footprint.
   - The requirements file includes many packages that appear unrelated to this API.
   - Split runtime, development, and notebook/tooling dependencies if needed.

10. Add repository hygiene rules.
    - Ignore generated resumes, reports, cache files, virtual environments, and local secrets consistently.
    - Keep only intentional sample fixtures under version control.
