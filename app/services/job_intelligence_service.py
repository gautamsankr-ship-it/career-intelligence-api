"""Computes the funnel's JobIntelligence result from an already-produced
JobEvaluation (ApplicationService.evaluate_job) plus, where available, the
raw discovery `opportunity` object.

Every dimension is read from an existing engine's own output -- nothing here
re-scores anything CareerDecisionEngine/ATSEngine/EmployerService/
RemoteWorkEligibilityClassifier already computed:

  vacancy_validity           -- job_analysis completeness/job description
                                 length + (where available) the discovery
                                 opportunity's duplicate/posted_date/
                                 application-route signals (Task 21.14C)
  hard_eligibility            -- RemoteWorkEligibilityClassifier.classify()
  opportunity_value           -- CareerDecisionEngine's own Industry/Career
                                 Growth scorecards + EmployerService's
                                 overall_score/career_growth_score
                                 (Task 21.14C)
  candidate_competitiveness   -- career_decision.overall_score PLUS verified
                                 requirement-evidence coverage from the
                                 Candidate Evidence Library (Task 21.14D)
  application_alignment       -- ats_result.ats_score.overall_score (ATSEngine)
  requirement_evidence        -- per-requirement STRONG/PARTIAL/NO_EVIDENCE/
                                 HARD_REQUIREMENT_GAP, from job_analysis's own
                                 required_skills/preferred_skills/education
                                 matched against verified profile + evidence
                                 -library facts (Task 21.14D)

`priority` is a small, precedence-ordered rule table -- never a weighted
composite:

  1. hard INELIGIBLE, vacancy INVALID/STALE, or a proven
     HARD_REQUIREMENT_GAP                                    -> REJECT
  2. hard eligibility/vacancy validity UNCERTAIN, or an uncertain
     (NO_EVIDENCE) mandatory factual requirement               -> HUMAN_REVIEW
  3. candidate_competitiveness LOW                             -> WATCH
  4. candidate_competitiveness STRETCH or INSUFFICIENT_DATA     -> HUMAN_REVIEW
  5. otherwise: existing screening_decision + ATS grade + opportunity_value
     decide APPLY / PRIORITY_APPLY / HUMAN_REVIEW / WATCH.

A LOW opportunity_value alone (tier 5) still only downgrades AUTO_APPLY to
WATCH; it never rejects on its own -- rejection only ever comes from hard
ineligibility, an invalid/stale vacancy, or a *proven* requirement gap.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.config import SCREENING_AUTO_APPLY, SCREENING_REVIEW, SCREENING_SKIP
from app.models.job_intelligence import (
    HARD_REQUIREMENT_GAP,
    NO_EVIDENCE,
    PARTIAL_EVIDENCE,
    STRONG_EVIDENCE,
    DimensionScore,
    JobIntelligence,
    Priority,
    RequirementEvidence,
)
from app.services.candidate_evidence_service import get_enriched_profile
from app.services.remote_work_eligibility import (
    INELIGIBLE,
    MANUAL_REVIEW,
    RemoteEligibilityResult,
    RemoteWorkEligibilityClassifier,
)

# Existing ATS letter grades (app/services/ats/ats_score.py::_grade) that
# already represent the strongest keyword-alignment tier -- reused as-is,
# not a new numeric threshold.
_STRONG_ATS_GRADES = {"A+", "A"}

# --- Vacancy validity policy constants (Task 21.14C) ------------------------
# Below this many characters, a job description is treated as an
# unanalyzable stub/placeholder rather than real vacancy content.
MIN_DESCRIPTION_CHARS = 60
# At or above this length, a description is "substantive" enough to count
# as positive verification evidence (alongside a confirmed application route).
STRONG_DESCRIPTION_CHARS = 200
# Vacancies posted longer ago than this, where a posting date is available,
# are treated as stale -- a deliberately simple, documented policy
# threshold, not a derived/fitted number.
STALE_POSTING_DAYS = 60

VERIFIED = "VERIFIED"
LIKELY_VALID = "LIKELY_VALID"
UNCERTAIN = "UNCERTAIN"
STALE = "STALE"
INVALID = "INVALID"

# --- Opportunity value policy constants (Task 21.14C) -----------------------
# 0-100 scale scores are bucketed into HIGH/MEDIUM/LOW at these thresholds.
# CareerDecisionEngine scorecard score-as-percent-of-weight values are
# already on this scale. EmployerService.overall_score/career_growth_score
# are NOT -- see _normalize_employer_score (Task 21.15A) for their native
# 0-10 scale and the single boundary where they are converted.
_HIGH_TIER_THRESHOLD = 75
_MEDIUM_TIER_THRESHOLD = 50

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def opportunity_shim_from_job_analysis(job_analysis: dict | None, job_description: str) -> SimpleNamespace:
    """RemoteWorkEligibilityClassifier.classify() expects an `opportunity`-
    like object (work_arrangement/remote_status/job_title/job_description).
    When the caller only has a JobEvaluation (no discovery-layer opportunity
    object -- e.g. most existing test/CLI callers), build the minimal
    duck-typed shim the classifier needs. job_analysis rarely carries
    remote-arrangement fields, so this shim almost always yields the
    classifier's own conservative NOT_APPLICABLE outcome -- the same
    "nothing to gate on" result a real non-remote opportunity would get,
    not a fabricated eligibility signal."""
    job_analysis = job_analysis or {}
    return SimpleNamespace(
        work_arrangement=job_analysis.get("work_arrangement") or "",
        remote_status=job_analysis.get("remote_status"),
        job_title=job_analysis.get("job_title") or "",
        job_description=job_description or "",
    )


def _posting_age_days(posted_date: str | None) -> int | None:
    """Best-effort ISO-ish date parse. Never fabricates an age when the
    format can't be parsed -- returns None (no freshness signal) instead."""
    if not posted_date:
        return None
    text = posted_date.strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).days
    return None


# --- Legitimate-intermediary / anonymous-employer policy (Task 21.24B) -----
# Generic phrasing an identifiable intermediary (a recruiting platform,
# agency, executive-search firm, or staffing firm named on the discovery
# `opportunity`) uses to affirmatively disclose that it is posting/recruiting
# on behalf of an undisclosed hiring client -- deliberately not tied to any
# single platform's name (e.g. not just "Jobgether").
_INTERMEDIARY_ON_BEHALF_PATTERN = re.compile(
    r"\bon behalf of (?:a|our|the)\s+(?:client|partner|customer|employer)\b"
    r"|\bacting on behalf of\b"
    r"|\brecruiting on behalf of\b"
    r"|\bour client\b[^.]{0,40}\b(?:is|are)\s+(?:looking|seeking|hiring)\b"
    r"|\bconfidential\s+(?:client|search)\b",
    re.IGNORECASE,
)

LEGITIMATE_INTERMEDIARY_EMPLOYER_ANONYMOUS = "LEGITIMATE_INTERMEDIARY_EMPLOYER_ANONYMOUS"


def _legitimate_intermediary_anonymous_employer(job_description: str, opportunity: Any) -> str | None:
    """Affirmative evidence -- never assumed -- that an identifiable
    intermediary is explicitly disclosing that it recruits on behalf of an
    undisclosed hiring employer/client. Returns the intermediary's own name
    when both conditions hold, else None. Requires ALL of:

      1. a discovery `opportunity` is supplied (so there is an identifiable
         poster/intermediary name distinct from job_analysis's own -- absent
         -- `company` field); a bare JobEvaluation with no opportunity never
         qualifies, since there is then no intermediary identity at all;
      2. that opportunity carries a non-empty `company` (the intermediary's
         own name, e.g. "Jobgether", a named agency, or an executive-search
         firm) -- an unnamed/unidentifiable poster never qualifies;
      3. the job description itself affirmatively states it is recruiting on
         behalf of a client/partner/employer (not merely a missing company
         field, which is exactly the UNKNOWN/SUSPICIOUS case this must not
         cover).

    Never fires from the intermediary's name alone, and never substitutes
    for -- or weakens -- any other vacancy-validity, eligibility, or
    application-route evidence."""
    if opportunity is None:
        return None
    intermediary_name = (getattr(opportunity, "company", "") or "").strip()
    if not intermediary_name:
        return None
    if _INTERMEDIARY_ON_BEHALF_PATTERN.search(job_description or ""):
        return intermediary_name
    return None


def _vacancy_validity(job_analysis: dict | None, job_description: str, opportunity: Any) -> DimensionScore:
    """VERIFIED/LIKELY_VALID/UNCERTAIN/STALE/INVALID, from usable-description
    length, identifiable employer/title, and -- only where a discovery
    `opportunity` is actually supplied -- duplicate/freshness/application-
    route evidence. Never performs a live web/network check and never
    invents evidence a caller didn't supply.

    Task 21.24B: a missing underlying-employer `company` field alone does
    not make validity UNCERTAIN when there is affirmative evidence of a
    legitimate, identifiable intermediary explicitly recruiting on behalf of
    an anonymous client (see `_legitimate_intermediary_anonymous_employer`).
    This never touches hard eligibility, requirement evidence,
    competitiveness, or application-route resolution -- those remain fully
    independent gates."""
    job_analysis = job_analysis or {}
    source = "job_analysis (analyze_job)" + (" + discovery opportunity" if opportunity is not None else "")
    description_len = len((job_description or "").strip())
    has_title = bool(job_analysis.get("job_title"))
    has_company = bool(job_analysis.get("company"))

    if description_len < MIN_DESCRIPTION_CHARS:
        return DimensionScore(
            INVALID, source,
            (f"job description is only {description_len} characters -- too short to reliably "
             f"analyze (minimum {MIN_DESCRIPTION_CHARS})",),
        )

    if opportunity is not None:
        history_status = (getattr(opportunity, "metadata", None) or {}).get("history_status")
        if history_status == "DUPLICATE":
            return DimensionScore(
                INVALID, source, ("already recorded as a duplicate of a previously processed vacancy",),
            )

    if not has_title and not has_company:
        return DimensionScore(
            INVALID, source, ("vacancy analysis produced neither a job title nor a company",),
        )

    if opportunity is not None:
        age_days = _posting_age_days(getattr(opportunity, "posted_date", "") or "")
        if age_days is not None and age_days > STALE_POSTING_DAYS:
            return DimensionScore(
                STALE, source,
                (f"vacancy was posted {age_days} days ago, beyond the {STALE_POSTING_DAYS}-day "
                 "freshness threshold",),
            )

    intermediary_name = None
    if not has_title or not has_company:
        missing = "company" if has_title else "job title"
        if missing == "company":
            intermediary_name = _legitimate_intermediary_anonymous_employer(job_description, opportunity)
        if intermediary_name is None:
            return DimensionScore(UNCERTAIN, source, (f"vacancy analysis is missing: {missing}",))

    reasons: list[str] = []
    if intermediary_name:
        reasons.append(
            f"underlying hiring employer is intentionally undisclosed behind identifiable intermediary "
            f"'{intermediary_name}', which explicitly states it is recruiting on behalf of a client/partner "
            f"-- not treated as a missing-employer failure ({LEGITIMATE_INTERMEDIARY_EMPLOYER_ANONYMOUS})"
        )
    route_confirmed = False
    route_uncertain = False
    if opportunity is not None:
        route_status = getattr(opportunity, "application_route_status", "") or ""
        route_confidence = (getattr(opportunity, "application_route_confidence", "") or "").upper()
        has_any_url = bool(
            getattr(opportunity, "job_url", "") or getattr(opportunity, "application_url", "")
            or getattr(opportunity, "source_listing_url", "")
        )
        if route_status == "RESOLVED" and route_confidence == "HIGH":
            route_confirmed = True
            reasons.append("application route resolved with high confidence")
        elif route_status == "APPLICATION_ROUTE_UNRESOLVED" and not has_any_url:
            route_uncertain = True
            reasons.append("no verifiable application route (URL) could be identified for this vacancy")

    if route_uncertain:
        return DimensionScore(UNCERTAIN, source, tuple(reasons))

    identity_phrase = "job title and intermediary identity" if intermediary_name else "job title, company"
    if route_confirmed and description_len >= STRONG_DESCRIPTION_CHARS:
        reasons.append(
            f"{identity_phrase} and a substantive job description ({description_len} characters) are all present"
        )
        return DimensionScore(VERIFIED, source, tuple(reasons))

    reasons.append(
        f"{identity_phrase} and job description are present; no independent route/freshness "
        "confirmation was supplied" if opportunity is None else
        f"{identity_phrase} and job description are present; no stronger verification signal "
        "(resolved high-confidence route + substantive description) was available"
    )
    return DimensionScore(LIKELY_VALID, source, tuple(reasons))


def _hard_eligibility(eligibility_result: RemoteEligibilityResult) -> DimensionScore:
    reasons = [eligibility_result.reason] if eligibility_result.reason else []
    return DimensionScore(
        value=eligibility_result.decision,
        source="RemoteWorkEligibilityClassifier.classify",
        reasons=tuple(reasons),
    )


def _find_scorecard(career_decision: Any, category: str) -> Any:
    for card in getattr(career_decision, "scorecards", None) or []:
        if getattr(card, "category", "") == category:
            return card
    return None


def _tier(value_0_to_100: float | None) -> str | None:
    if value_0_to_100 is None:
        return None
    if value_0_to_100 >= _HIGH_TIER_THRESHOLD:
        return HIGH
    if value_0_to_100 >= _MEDIUM_TIER_THRESHOLD:
        return MEDIUM
    return LOW


def _normalize_employer_score(value_0_to_10: float | None) -> float | None:
    """Employer.overall_score/career_growth_score are produced by
    EmployerService.analyze() (app/services/employer_service.py) on a native
    0-10 scale -- its OpenAI prompt never specifies a scale, and every real
    value observed across Task 21.15's 40-vacancy live validation run (e.g.
    3, 6.3, 7.5, 9.75) falls in [0, 10]. This is independently confirmed by
    two other real, pre-existing consumers of these same two fields:
    CareerDecisionEngine._score_employer/_score_career_growth
    (app/services/career_engine.py) clamp them against a scorecard weight of
    10, and RecruiterReasoningService.evaluate()
    (app/services/recruiter_reasoning_service.py) computes
    `recruiter.career_alignment = employer.career_growth_score * 10`
    (clamped to 100) -- the same *10 conversion applied here.

    _tier() expects a 0-100 scale (matching CareerDecisionEngine's own
    score-as-percent-of-weight scorecards, e.g. Industry). This is the single
    boundary where the raw 0-10 EmployerService values are converted before
    reaching _tier() -- apply it ONLY to these two raw Employer attributes,
    never to an already-0-100 CareerDecisionEngine scorecard percentage
    (e.g. the Career Growth scorecard fallback below), which would double-
    normalize an already-correct value."""
    if value_0_to_10 is None:
        return None
    return min(value_0_to_10 * 10, 100.0)


def _majority_band(tiers: list[str]) -> str:
    """Deterministic majority rule over the available (present) tiers only
    -- not a weighted average. A single available signal decides outright;
    with more than one, strict majority decides; a split (no strict
    majority) is reported as MEDIUM rather than guessed either way."""
    total = len(tiers)
    if tiers.count(HIGH) * 2 > total:
        return HIGH
    if tiers.count(LOW) * 2 > total:
        return LOW
    return MEDIUM


def _opportunity_value(employer: Any, career_decision: Any, opportunity: Any) -> DimensionScore:
    """HIGH/MEDIUM/LOW/INSUFFICIENT_DATA from employer quality (employer.
    overall_score), functional/industry relevance (CareerDecisionEngine's
    own Industry scorecard, as percent of its weight) and career-progression
    value (employer.career_growth_score, or the Career Growth scorecard as a
    fallback) -- a majority-of-available-tiers rule, not a weighted sum.
    Geography/work-arrangement and compensation are reported as
    supplementary evidence only (never scored), since neither is on a
    comparable scale and compensation data is often simply absent."""
    reasons: list[str] = []
    tiers: list[str] = []

    employer_score_raw = getattr(employer, "overall_score", None)
    employer_score = _normalize_employer_score(employer_score_raw)
    employer_tier = _tier(employer_score)
    if employer_tier:
        reasons.append(
            f"employer quality is {employer_tier} (employer.overall_score={employer_score_raw} "
            f"on EmployerService's native 0-10 scale, normalized to {employer_score}/100)"
        )
        tiers.append(employer_tier)

    industry_card = _find_scorecard(career_decision, "Industry")
    if industry_card is not None and getattr(industry_card, "weight", 0):
        industry_pct = (industry_card.score / industry_card.weight) * 100
        industry_tier = _tier(industry_pct)
        reasons.append(
            f"industry/functional relevance is {industry_tier} "
            f"({industry_card.score}/{industry_card.weight} on the Industry scorecard)"
        )
        tiers.append(industry_tier)

    career_growth_score_raw = getattr(employer, "career_growth_score", None)
    career_growth_score = _normalize_employer_score(career_growth_score_raw)
    if career_growth_score is not None:
        career_growth_tier = _tier(career_growth_score)
        reasons.append(
            f"career progression potential is {career_growth_tier} "
            f"(employer.career_growth_score={career_growth_score_raw} on EmployerService's native "
            f"0-10 scale, normalized to {career_growth_score}/100)"
        )
        tiers.append(career_growth_tier)
    else:
        # Already a 0-100 CareerDecisionEngine scorecard percentage --
        # must NOT be passed through _normalize_employer_score, or it would
        # be double-normalized.
        career_growth_card = _find_scorecard(career_decision, "Career Growth")
        if career_growth_card is not None and getattr(career_growth_card, "weight", 0):
            pct = (career_growth_card.score / career_growth_card.weight) * 100
            career_growth_tier = _tier(pct)
            reasons.append(
                f"career progression potential is {career_growth_tier} "
                f"({career_growth_card.score}/{career_growth_card.weight} on the Career Growth scorecard)"
            )
            tiers.append(career_growth_tier)

    if opportunity is not None:
        work_arrangement = getattr(opportunity, "work_arrangement", "") or ""
        if work_arrangement and work_arrangement != "UNKNOWN":
            reasons.append(f"work arrangement: {work_arrangement}")
        salary = getattr(opportunity, "salary", "") or ""
        if salary:
            reasons.append(f"stated compensation: {salary}")

    source = (
        "CareerDecisionEngine scorecards (Industry/Career Growth) + "
        "EmployerService (overall_score/career_growth_score)"
    )

    if not tiers:
        return DimensionScore(
            INSUFFICIENT_DATA, source,
            ("no employer-quality, industry-relevance, or career-growth signal was available",),
        )

    return DimensionScore(_majority_band(tiers), source, tuple(reasons))


# --- Requirement-level evidence assessment (Task 21.14D) -------------------

VERY_STRONG = "VERY_STRONG"
STRONG = "STRONG"
COMPETITIVE = "COMPETITIVE"
STRETCH = "STRETCH"

# At most this many requirements are assessed (required_skills + education,
# then preferred_skills, in that order) -- a documented safety cap, not a
# scoring parameter.
MAX_REQUIREMENTS_ASSESSED = 20

# Generic soft-skill/behavioural wording. A requirement matching one of
# these is never elevated to HARD_REQUIREMENT_GAP and is reported as
# "behavioural" -- verified evidence can support a *related* factual claim
# (e.g. "reviewed work performed by junior accountants" evidences
# leadership-adjacent activity) but can never prove or disprove a trait.
#
# Task 21.15B: real-market validation found this literal-phrase list was
# incomplete -- generic soft/interpersonal wording ("analytical skills",
# "cross-functional collaboration", "prioritization") was being treated as a
# factual mandatory requirement, forcing HUMAN_REVIEW purely because no
# candidate-evidence phrase happened to match the exact wording. Rather than
# growing this list indefinitely, three complementary, reusable checks are
# combined in _is_behavioural(): (1) this literal-phrase list, for
# well-established idioms; (2) _BEHAVIOURAL_STEMS, word-root substrings that
# generalize across tense/plural/spelling variants (e.g. "priorit" matches
# prioritization/prioritisation/priorities/prioritise) without enumerating
# every form; (3) _BEHAVIOURAL_PREFIXES, generic sentence-opening templates
# ("ability to ...", "willing to ...") that are soft/dispositional framing
# regardless of the verb that follows.
_BEHAVIOURAL_MARKERS = (
    "attention to detail", "team player", "communication skills", "problem solving",
    "problem-solving", "critical thinking", "critical-thinking", "time management",
    "organisational skills", "organizational skills", "work ethic", "self-starter",
    "self starter", "interpersonal skills", "adaptability", "proactive", "positive attitude",
    "fast learner", "multitasking", "collaborative", "detail oriented", "detail-oriented",
    "strong communicator", "excellent communication", "willingness to learn", "eager to learn",
    "curious", "motivated", "passionate", "flexible", "resilient", "growth mindset",
    "can-do attitude", "executive presence", "even temperament", "exacting standards",
    "clear writing", "facilitation", "presentation skills", "negotiation skills",
    "people skills", "listening skills", "ownership of", "sense of ownership",
)

# Word-root substrings, not full phrases -- a requirement matching one of
# these stems is behavioural regardless of its exact grammatical form
# (plural/verb/adjective), so a single stem generalizes to variants never
# explicitly listed above.
_BEHAVIOURAL_STEMS = (
    "priorit",   # prioritization/prioritisation/priorities/prioritise/prioritize
    "collabor",  # collaboration/collaborative/collaborate/cross-functional collaboration
    "analytic",  # analytical/analytical skills/analytics-minded
    "stakeholder",  # stakeholder management/influencing stakeholders/stakeholder skills
)

# Generic sentence-opening templates that frame a requirement as a
# disposition/capacity rather than a factual, checkable attribute -- "ability
# to work independently" is behavioural no matter what follows the template,
# so this is checked as a prefix, not a phrase.
_BEHAVIOURAL_PREFIXES = (
    "ability to ", "the ability to ", "capacity to ", "capable of ",
)

# "willing to .../willingness to ..." is handled separately from the prefixes
# above: the SAME phrasing covers both a genuine disposition ("willingness
# to learn", "willing to embrace change" -- behavioural) and an objectively
# verifiable candidate constraint expressed as a willingness statement
# ("willing to relocate", "willingness to travel", "willing to obtain a
# security clearance", "willing to work weekends on-site" -- a factual,
# checkable condition, not a soft trait). Treating every "willing(ness) to"
# requirement as behavioural would silently exempt real relocation/travel/
# licensing/work-location constraints from the uncertain-critical-
# requirement gate. So "willing(ness) to" is behavioural UNLESS the rest of
# the phrase names one of these objectively verifiable constraint markers.
_WILLING_PREFIXES = ("willing to ", "willingness to ")
_FACTUAL_CONSTRAINT_MARKERS = (
    "relocat", "travel", "licens", "licenc", "certif", "qualif", "accredit",
    "course", "clearance", "background check", "visa", "work authoriz",
    "work authoris", "on-site", "onsite", "on site", "shift", "weekend",
    "overtime", "roster",
)

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "is", "it", "of", "on", "or", "our", "that", "the", "to", "with", "you",
    "your", "will", "we", "this", "years", "year", "experience", "required", "must",
    "preferred", "strong", "excellent", "good", "ability",
}
_WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+./-]{1,}")


def _tokenize_words(text: str) -> set[str]:
    if not text:
        return set()
    return {t.lower() for t in _WORD_PATTERN.findall(text) if t.lower() not in _STOPWORDS and len(t) > 1}


def _is_behavioural(requirement_text: str) -> bool:
    lowered = requirement_text.lower().strip()
    if any(marker in lowered for marker in _BEHAVIOURAL_MARKERS):
        return True
    if any(stem in lowered for stem in _BEHAVIOURAL_STEMS):
        return True
    if any(lowered.startswith(prefix) for prefix in _BEHAVIOURAL_PREFIXES):
        return True
    if any(lowered.startswith(prefix) for prefix in _WILLING_PREFIXES):
        # A "willing(ness) to ..." constraint stays factual (not
        # behavioural) when it names an objectively verifiable condition
        # (relocation, travel, licensing/certification, work location/
        # schedule) -- only a genuine disposition is behavioural.
        return not any(marker in lowered for marker in _FACTUAL_CONSTRAINT_MARKERS)
    return False


# --- Task 21.15C: factual-requirement criticality ---------------------------
# Real-market validation (Task 21.15/21.15B) found that EVERY factual item
# the upstream extractor placed in required_skills was treated as equally
# critical -- a named tool/technology got the same uncertain-mandatory
# HUMAN_REVIEW treatment as an explicitly required professional licence. This
# distinguishes CRITICAL (uncertainty must still block automatic progression)
# from NON_CRITICAL (missing evidence reduces Candidate Competitiveness
# through the existing coverage-ratio mechanism, but does not by itself force
# HUMAN_REVIEW) -- using ONLY the vacancy's own already-available wording
# (job_description), never a new LLM call, and never assuming a requirement's
# category (e.g. "it's a qualification, so it must be critical") without
# confirming actual vacancy phrasing.
CRITICAL = "CRITICAL"
NON_CRITICAL = "NON_CRITICAL"
AMBIGUOUS_CRITICALITY = "AMBIGUOUS_CRITICALITY"
NOT_APPLICABLE_CRITICALITY = "NOT_APPLICABLE"

# Explicit vacancy-language cues that mark a requirement as genuinely
# critical -- checked against the raw job_description text surrounding
# wherever the requirement's own wording appears, not the requirement's
# short extracted phrase alone (which rarely repeats these words verbatim).
_CRITICALITY_MARKERS = (
    "must have", "must hold", "must possess", "must be", "you must", "candidates must",
    "required", "requires ", "requirement", "essential", "mandatory", "prerequisite",
    "minimum of", "at least", "non-negotiable",
    "licence required", "license required", "licence is required", "license is required",
    "licensing required", "certification required", "certified required",
    "qualification required", "qualification is required", "qualified required",
    "authorised to work", "authorized to work", "work authorization", "work authorisation",
    "legally required", "legally entitled", "eligible to work", "right to work",
    "clearance required", "security clearance", "background check required",
)

# Fallback character budget when no clause boundary (newline/semicolon) is
# found nearby -- e.g. a job_description with no bullet-list formatting.
_CRITICALITY_WINDOW_CHARS = 160

# Requirement lists are almost always bullet/semicolon-separated ("- MS
# Office proficiency essential.\n- Languages preferred;"), where criticality
# genuinely differs bullet-to-bullet. A flat character window bleeds across
# bullet boundaries and can pick up a NEIGHBOURING bullet's softening/
# criticality wording instead of the requirement's own -- caught by Task
# 21.15C's own manual audit ("MS Office proficiency essential" was wrongly
# read as non-critical because "Languages preferred" sat in the same flat
# window). So the window is narrowed to the clause actually containing the
# requirement mention wherever such a boundary exists.
_CLAUSE_BOUNDARY_CHARS = ("\n", ";")


def _requirement_context_window(requirement_text: str, job_description: str) -> str | None:
    """The lowercase job_description text of the clause/bullet containing
    wherever this requirement's own wording appears -- bounded by the
    nearest newline/semicolon on each side when one exists nearby, else a
    flat character window -- or None if the requirement can't be located at
    all (an extraction-ambiguity signal: the requirement phrase doesn't
    recognizably correspond to any part of the actual vacancy text, e.g. a
    fragment like "related field" split out of a longer degree-subject
    list)."""
    if not job_description:
        return None
    lowered_desc = job_description.lower()
    lowered_req = requirement_text.lower().strip()

    idx = lowered_desc.find(lowered_req)
    if idx != -1:
        match_start, match_end = idx, idx + len(lowered_req)
    else:
        # Loose fallback: the requirement's own significant words all appear
        # somewhere in the description -- span the positions they occupy.
        req_words = [w for w in _tokenize_words(requirement_text) if len(w) > 2]
        if not req_words:
            return None
        positions = [lowered_desc.find(w) for w in req_words]
        if any(p == -1 for p in positions):
            return None
        match_start = min(positions)
        match_end = max(p + len(w) for p, w in zip(positions, req_words))

    boundary_starts = [lowered_desc.rfind(ch, 0, match_start) for ch in _CLAUSE_BOUNDARY_CHARS]
    boundary_starts = [b for b in boundary_starts if b != -1]
    start = max(boundary_starts) + 1 if boundary_starts else max(0, match_start - _CRITICALITY_WINDOW_CHARS)

    boundary_ends = [lowered_desc.find(ch, match_end) for ch in _CLAUSE_BOUNDARY_CHARS]
    boundary_ends = [e for e in boundary_ends if e != -1]
    end = min(boundary_ends) if boundary_ends else min(len(lowered_desc), match_end + _CRITICALITY_WINDOW_CHARS)

    return lowered_desc[start:end]


# Credential/licence/clearance/work-authorization requirements are handled
# with the OPPOSITE default polarity from ordinary tool/domain-terminology
# requirements: real job postings routinely list a professional qualification
# ("CA ANZ (or equivalent) qualification", "CPA") under a heading like
# "Skills and Attributes for Success" or "Qualifications" without repeating
# the word "required" next to every bullet -- unlike a named tool or domain
# activity, silently treating that as non-critical is unsafe (a manual audit
# of Task 21.15C's own before/after replay caught exactly this: "CA ANZ (or
# equivalent) qualification" was wrongly downgraded to NON_CRITICAL purely
# because "required" didn't appear nearby, which flipped one real vacancy
# all the way to APPLY). So a requirement matching one of these credential
# markers defaults CRITICAL, and is downgraded to NON_CRITICAL only when the
# vacancy's own wording explicitly softens it (preferred/nice to have/a
# plus/etc.) -- checked against the requirement's own text AND its
# job_description context together, since a softening cue sometimes appears
# in the extracted phrase itself (e.g. "MBA a plus").
_CREDENTIAL_REQUIREMENT_MARKERS = (
    "cpa", "ca anz", "aca", "acca", "cima", "cfa", "arita", "chartered accountant",
    "chartered", "license", "licence", "clearance", "work authoriz", "work authoris",
    "visa", "qualification", "qualified", "degree", "bachelor", "master's", "masters",
    "mba", "accreditation", "accredited", "certification", "certified",
)
_SOFTENING_MARKERS = (
    "preferred", "nice to have", "nice-to-have", "advantageous", "desirable",
    "a plus", "is a plus", "beneficial", "ideally", "bonus", "not essential",
    "not required", "would be beneficial",
)


def _is_credential_requirement(requirement_text: str) -> bool:
    lowered = requirement_text.lower()
    return any(marker in lowered for marker in _CREDENTIAL_REQUIREMENT_MARKERS)


def _requirement_criticality(requirement_text: str, job_description: str) -> str:
    """CRITICAL when the vacancy's own wording, around wherever this
    requirement appears in job_description, uses explicit mandatory/
    essential/required-style language. NON_CRITICAL when the requirement's
    wording is located but no such language is present nearby (e.g. a named
    tool mentioned only as part of a general skills list). AMBIGUOUS_CRITICALITY
    when the requirement's own wording can't be located in job_description at
    all -- handled conservatively (still blocks, like CRITICAL) rather than
    silently downgraded.

    A credential/licence/clearance/work-authorization requirement (see
    _CREDENTIAL_REQUIREMENT_MARKERS) inverts this default: CRITICAL unless
    the vacancy's own wording explicitly softens it. An explicit softening
    cue always wins first, for every requirement -- this also protects
    against "required" matching inside a negation like "preferred but not
    required" in the ordinary (non-credential) branch below."""
    window = _requirement_context_window(requirement_text, job_description)
    combined = requirement_text.lower() + " " + (window or "")

    if any(marker in combined for marker in _SOFTENING_MARKERS):
        return NON_CRITICAL

    if _is_credential_requirement(requirement_text):
        return CRITICAL

    if window is None:
        return AMBIGUOUS_CRITICALITY
    if any(marker in window for marker in _CRITICALITY_MARKERS):
        return CRITICAL
    return NON_CRITICAL


def _verified_evidence_text(profile: dict | None) -> str:
    """Flatten every VERIFIED-only fact reachable from the profile (already
    evidence-library-enriched, so it includes the same richer per-employer
    facts the resume/custom-response generators use -- and, exactly like
    them, never a NEEDS_CONFIRMATION/CONFLICTING one) into one lowercase
    text blob for requirement matching."""
    profile = profile or {}
    try:
        enriched = get_enriched_profile(profile)
    except Exception:
        # Evidence-library enrichment is a best-effort augmentation; if the
        # library file isn't reachable in this context, fall back to the
        # raw (still real, still candidate-authored) profile rather than
        # failing the whole competitiveness computation.
        enriched = profile

    parts: list[str] = []

    def _extend_flat(value):
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                _extend_flat(item)
        elif isinstance(value, dict):
            for item in value.values():
                _extend_flat(item)

    for key in (
        "skills", "technology", "software", "technical_capabilities", "finance_domains",
        "industry_expertise", "achievements", "career_highlights", "professional_memberships",
        "certifications", "education",
    ):
        _extend_flat(enriched.get(key))

    for entry in enriched.get("employment_history") or []:
        _extend_flat(entry.get("company"))
        _extend_flat(entry.get("position"))
        _extend_flat(entry.get("responsibilities"))
        _extend_flat(entry.get("technologies"))
        _extend_flat(entry.get("achievements"))

    for entry in enriched.get("board_positions") or []:
        _extend_flat(entry.get("responsibilities"))
        _extend_flat(entry.get("achievements"))

    for entry in enriched.get("entrepreneurship") or []:
        _extend_flat(entry.get("description"))
        _extend_flat(entry.get("achievements"))

    summary = (enriched.get("professional_summary") or {}).get("headline")
    _extend_flat(summary)

    return " ".join(parts)


def _requirement_specs(job_analysis: dict | None) -> list[tuple[str, bool]]:
    """(requirement_text, is_mandatory) pairs, reusing job_analysis's own
    existing required_skills/education (mandatory) and preferred_skills
    (not mandatory) fields -- no new extraction logic invented."""
    job_analysis = job_analysis or {}
    specs: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def _add(items, mandatory):
        for text in items or []:
            if isinstance(text, str) and text.strip() and text.strip().lower() not in seen:
                specs.append((text.strip(), mandatory))
                seen.add(text.strip().lower())

    _add(job_analysis.get("required_skills"), True)
    _add(job_analysis.get("education"), True)
    _add(job_analysis.get("preferred_skills"), False)
    return specs[:MAX_REQUIREMENTS_ASSESSED]


def _classify_requirement(
    requirement_text: str, is_mandatory: bool, verified_text: str, verified_words: set[str],
    job_description: str = "",
) -> RequirementEvidence:
    """Word-overlap classification against verified evidence only -- this
    function never claims proven absence, only what evidence is/isn't
    present (Task 21.14D section 3). The one genuine "proven absence" check
    this module makes -- a years-of-experience shortfall -- is handled
    separately in `_years_requirement`, since it's a direct numeric
    comparison against a structured field, not a word-overlap guess.

    Task 21.15C: criticality is assessed only for factual mandatory
    requirements (not behavioural, not preferred/non-mandatory) -- it's the
    signal that decides whether an uncertain requirement forces HUMAN_REVIEW
    or instead only reduces Candidate Competitiveness."""
    is_behavioural = _is_behavioural(requirement_text)
    criticality = (
        _requirement_criticality(requirement_text, job_description)
        if is_mandatory and not is_behavioural
        else NOT_APPLICABLE_CRITICALITY
    )

    req_words = _tokenize_words(requirement_text)
    if not req_words:
        return RequirementEvidence(
            requirement_text, NO_EVIDENCE, is_mandatory, is_behavioural, (),
            "requirement text has no meaningful terms to match", criticality,
        )

    matched = sorted(req_words & verified_words)
    ratio = len(matched) / len(req_words)

    if ratio >= 0.8 or requirement_text.lower() in verified_text:
        classification = STRONG_EVIDENCE
        reason = f"verified evidence matches {len(matched)}/{len(req_words)} key terms"
    elif ratio > 0:
        classification = PARTIAL_EVIDENCE
        reason = f"verified evidence partially matches {len(matched)}/{len(req_words)} key terms"
    else:
        classification = NO_EVIDENCE
        reason = (
            "behavioural/soft-skill wording -- not something factual evidence can confirm or deny"
            if is_behavioural else "no verified evidence matches this requirement's key terms"
        )

    return RequirementEvidence(requirement_text, classification, is_mandatory, is_behavioural, tuple(matched), reason, criticality)


def _years_requirement(job_analysis: dict | None, profile: dict | None) -> RequirementEvidence | None:
    """The one deliberately narrow "proven absence" check this module makes:
    a stated years-of-experience minimum (analyze_job's own structured
    `experience_required` field -- not a regex guess over requirement text)
    directly compared against the candidate's own verified years figure.
    Returns None when the vacancy states no requirement to check.

    Always CRITICAL: this is a structured, explicit numeric minimum straight
    from analyze_job(), never an inferred/ambiguous phrase -- exactly the
    "explicit minimum years experience" example the criticality model is
    meant to catch."""
    required_years = (job_analysis or {}).get("experience_required") or None
    if not required_years:
        return None

    requirement_text = f"{required_years}+ years of experience"
    candidate_years = ((profile or {}).get("experience") or {}).get("years")

    if candidate_years is None:
        return RequirementEvidence(
            requirement_text, NO_EVIDENCE, True, False, (),
            "candidate's verified years of experience is not recorded", CRITICAL,
        )
    if candidate_years < required_years:
        return RequirementEvidence(
            requirement_text, HARD_REQUIREMENT_GAP, True, False,
            (f"verified experience: {candidate_years} years",),
            f"vacancy requires {required_years}+ years of experience; verified candidate experience is {candidate_years} years",
            CRITICAL,
        )
    return RequirementEvidence(
        requirement_text, STRONG_EVIDENCE, True, False,
        (f"verified experience: {candidate_years} years",),
        f"verified candidate experience ({candidate_years} years) meets the {required_years}+ year requirement",
        CRITICAL,
    )


def _assess_requirement_evidence(
    job_analysis: dict | None, profile: dict | None, job_description: str = "",
) -> tuple[RequirementEvidence, ...]:
    assessments: list[RequirementEvidence] = []

    years_requirement = _years_requirement(job_analysis, profile)
    if years_requirement is not None:
        assessments.append(years_requirement)

    specs = _requirement_specs(job_analysis)
    if specs:
        verified_text = _verified_evidence_text(profile).lower()
        verified_words = _tokenize_words(verified_text)
        assessments.extend(
            _classify_requirement(text, mandatory, verified_text, verified_words, job_description)
            for text, mandatory in specs
        )

    return tuple(assessments)


def _tier_ratio(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio >= 0.75:
        return HIGH
    if ratio >= 0.4:
        return MEDIUM
    return LOW


# Explicit, fully-specified competitiveness table: (career_tier, coverage_tier)
# -> band. `None` coverage_tier means no mandatory factual requirement was
# identified, so the band relies on career fit alone. A literal lookup
# table, not a formula -- every cell is a deliberate, inspectable choice.
_COMPETITIVENESS_TABLE: dict[tuple[str, str | None], str] = {
    (HIGH, HIGH): VERY_STRONG, (HIGH, MEDIUM): STRONG, (HIGH, LOW): COMPETITIVE, (HIGH, None): VERY_STRONG,
    (MEDIUM, HIGH): STRONG, (MEDIUM, MEDIUM): COMPETITIVE, (MEDIUM, LOW): STRETCH, (MEDIUM, None): COMPETITIVE,
    (LOW, HIGH): COMPETITIVE, (LOW, MEDIUM): STRETCH, (LOW, LOW): LOW, (LOW, None): LOW,
}


def _candidate_competitiveness(
    career_decision: Any, requirement_evidence: tuple[RequirementEvidence, ...],
) -> DimensionScore:
    source = "CareerDecisionEngine.evaluate (career_decision.overall_score) + requirement-evidence coverage"
    career_score = getattr(career_decision, "overall_score", None)

    hard_gaps = [r for r in requirement_evidence if r.classification == HARD_REQUIREMENT_GAP]
    if hard_gaps:
        reasons = ["a hard requirement gap was found -- competitiveness capped at LOW regardless of career fit"]
        reasons.extend(f"{gap.requirement}: {gap.reason}" for gap in hard_gaps)
        return DimensionScore(LOW, source, tuple(reasons))

    if career_score is None:
        return DimensionScore(INSUFFICIENT_DATA, source, ("career fit score unavailable",))

    mandatory_factual = [r for r in requirement_evidence if r.is_mandatory and not r.is_behavioural]
    coverage_ratio = None
    if mandatory_factual:
        covered = sum(1 for r in mandatory_factual if r.classification in (STRONG_EVIDENCE, PARTIAL_EVIDENCE))
        coverage_ratio = covered / len(mandatory_factual)
    coverage_tier = _tier_ratio(coverage_ratio)
    career_tier = _tier(career_score)

    reasons = [f"career fit is {career_tier} (career_decision.overall_score={career_score})"]
    if coverage_ratio is not None:
        reasons.append(
            f"verified-evidence coverage of {len(mandatory_factual)} mandatory factual requirement(s) "
            f"is {coverage_tier} ({coverage_ratio:.0%})"
        )
    else:
        reasons.append("no mandatory factual requirement was identified to assess evidence coverage against")

    band = _COMPETITIVENESS_TABLE[(career_tier, coverage_tier)]
    return DimensionScore(band, source, tuple(reasons))


def _application_alignment(ats_result: dict | None) -> DimensionScore:
    ats_score = ((ats_result or {}).get("ats_score") or {})
    score = ats_score.get("overall_score")
    grade = ats_score.get("grade")
    if score is None:
        return DimensionScore(value=None, source="ATSEngine.analyze", reasons=("ATS score unavailable",))
    reasons = [f"ats_score.overall_score={score}"]
    if grade:
        reasons.append(f"ats_score.grade={grade}")
    return DimensionScore(
        value=score, source="ATSEngine.analyze (ats_result.ats_score.overall_score)",
        reasons=tuple(reasons),
    )


def _decide_priority(
    hard_eligibility: DimensionScore,
    vacancy_validity: DimensionScore,
    screening_decision: str | None,
    ats_result: dict | None,
    opportunity_value: DimensionScore,
    candidate_competitiveness: DimensionScore,
    requirement_evidence: tuple[RequirementEvidence, ...],
) -> tuple[Priority, tuple[str, ...]]:
    # Tier 1: hard reject -- ineligibility, an invalid/stale vacancy, or a
    # *proven* requirement gap, checked together and unconditionally before
    # anything else. A strong ATS/career score can never rescue any of these.
    reject_reasons: list[str] = []
    if hard_eligibility.value == INELIGIBLE:
        reject_reasons.append("Hard eligibility check failed -- candidate is not eligible for this vacancy.")
        reject_reasons.extend(hard_eligibility.reasons)
    if vacancy_validity.value in (INVALID, STALE):
        reject_reasons.append(f"Vacancy validity is {vacancy_validity.value}.")
        reject_reasons.extend(vacancy_validity.reasons)
    hard_gaps = [r for r in requirement_evidence if r.classification == HARD_REQUIREMENT_GAP]
    if hard_gaps:
        for gap in hard_gaps:
            reject_reasons.append(f"Proven requirement gap: {gap.requirement} -- {gap.reason}")
    if reject_reasons:
        return Priority.REJECT, tuple(reject_reasons)

    # Tier 2: human review -- uncertain eligibility/validity, or a mandatory
    # factual requirement with no verified evidence either way (missing
    # evidence is never treated as proven absence, but it also must not be
    # silently waved through to automatic preparation).
    review_reasons: list[str] = []
    if hard_eligibility.value == MANUAL_REVIEW:
        review_reasons.append(
            "Hard eligibility is uncertain (geographic/work-authorization evidence is "
            "unclear) -- routed to human review; automatic application preparation must not proceed."
        )
    if vacancy_validity.value == UNCERTAIN:
        review_reasons.append("Vacancy validity is uncertain.")
        review_reasons.extend(vacancy_validity.reasons)
    # Task 21.15C: only a CRITICAL (vacancy wording frames it as mandatory/
    # essential) or AMBIGUOUS_CRITICALITY (can't locate the requirement's own
    # wording in job_description at all -- handled conservatively, same as
    # CRITICAL) uncertain factual requirement forces HUMAN_REVIEW.
    # NON_CRITICAL uncertain requirements (e.g. a named tool mentioned with
    # no mandatory-language context) do NOT by themselves force review --
    # they still reduce Candidate Competitiveness via the existing coverage-
    # ratio mechanism in _candidate_competitiveness().
    uncertain_mandatory = [
        r for r in requirement_evidence
        if r.is_mandatory and not r.is_behavioural and r.classification == NO_EVIDENCE
        and r.criticality in (CRITICAL, AMBIGUOUS_CRITICALITY)
    ]
    if uncertain_mandatory:
        review_reasons.append(
            "At least one critical factual requirement has no verified evidence either way "
            "(not proven absent, but not confirmed) -- requires human confirmation before automatic "
            "application preparation."
        )
        for item in uncertain_mandatory:
            review_reasons.append(
                f"Uncertain requirement ({item.criticality}): {item.requirement} -- {item.reason}"
            )
    if review_reasons:
        return Priority.HUMAN_REVIEW, tuple(review_reasons)

    # Tier 3/4: candidate competitiveness gates before the existing
    # screening/ATS/opportunity-value logic even runs.
    if candidate_competitiveness.value == LOW:
        reasons = ["Candidate competitiveness is LOW -- downgraded to WATCH rather than applying."]
        reasons.extend(candidate_competitiveness.reasons)
        return Priority.WATCH, tuple(reasons)
    if candidate_competitiveness.value in (STRETCH, INSUFFICIENT_DATA):
        reasons = [f"Candidate competitiveness is {candidate_competitiveness.value} -- routed to human review."]
        reasons.extend(candidate_competitiveness.reasons)
        return Priority.HUMAN_REVIEW, tuple(reasons)

    # From here: hard_eligibility ELIGIBLE/NOT_APPLICABLE, vacancy_validity
    # VERIFIED/LIKELY_VALID, no uncertain mandatory requirement, and
    # candidate_competitiveness is COMPETITIVE/STRONG/VERY_STRONG -- no
    # blocking issue found.
    reasons = [
        f"Hard eligibility: {hard_eligibility.value}; vacancy validity: {vacancy_validity.value}; "
        f"candidate competitiveness: {candidate_competitiveness.value} -- no blocking issues found.",
    ]

    if screening_decision == SCREENING_AUTO_APPLY:
        reasons.append(f"Existing career screening decision is {SCREENING_AUTO_APPLY}.")

        if opportunity_value.value == LOW:
            reasons.append(
                "Opportunity value is LOW -- downgraded to WATCH rather than applying, per the "
                "platform's value-over-volume policy (LOW alone never hard-rejects)."
            )
            reasons.extend(opportunity_value.reasons)
            return Priority.WATCH, tuple(reasons)

        grade = ((ats_result or {}).get("ats_score") or {}).get("grade")
        if grade in _STRONG_ATS_GRADES and opportunity_value.value == HIGH:
            reasons.append(f"ATS grade {grade} is in the strongest existing tier and opportunity value is HIGH.")
            reasons.extend(opportunity_value.reasons)
            return Priority.PRIORITY_APPLY, tuple(reasons)

        reasons.append(f"Opportunity value is {opportunity_value.value}.")
        return Priority.APPLY, tuple(reasons)

    if screening_decision == SCREENING_REVIEW:
        reasons.append(f"Existing career screening decision is {SCREENING_REVIEW}.")
        return Priority.HUMAN_REVIEW, tuple(reasons)

    if screening_decision == SCREENING_SKIP:
        reasons.append(
            f"Existing career screening decision is {SCREENING_SKIP} -- not eligibility-blocked, "
            "kept for future reconsideration rather than rejected outright."
        )
        return Priority.WATCH, tuple(reasons)

    reasons.append(f"Unrecognized screening decision ({screening_decision!r}) -- defaulting to human review.")
    return Priority.HUMAN_REVIEW, tuple(reasons)


# --- Prepare-for-human-review package gate (Task 21.24C) --------------------
PREPARE_FOR_HUMAN_REVIEW = "PREPARE_FOR_HUMAN_REVIEW"

_PACKAGE_GATE_QUALIFYING_VALIDITY = (VERIFIED, LIKELY_VALID)
_PACKAGE_GATE_QUALIFYING_OPPORTUNITY_VALUE = (HIGH, MEDIUM)
_PACKAGE_GATE_QUALIFYING_COMPETITIVENESS = (STRONG, VERY_STRONG)


def _package_preparation_gate(
    priority: Priority,
    hard_eligibility: DimensionScore,
    vacancy_validity: DimensionScore,
    opportunity_value: DimensionScore,
    candidate_competitiveness: DimensionScore,
    requirement_evidence: tuple[RequirementEvidence, ...],
) -> tuple[str, tuple[str, ...]]:
    """A narrow, additive distinction -- separate from `priority`, which
    always stays HUMAN_REVIEW (C) here -- for whether *internal*
    application-package preparation may proceed for a strong C opportunity
    whose only remaining blocker is human-resolvable uncertainty (e.g.
    unresolved international-remote eligibility behind a legitimate
    intermediary). External execution/FinalReview/submission are entirely
    unaffected: they gate purely on `priority` via
    application_eligibility_policy.intelligence_priority_gate, which never
    consults this. Never converts C to B, never fires for A/B/D/E.

    Qualifies ONLY when ALL of:
      - priority is HUMAN_REVIEW;
      - vacancy_validity is VERIFIED or LIKELY_VALID (never UNCERTAIN/
        INVALID/STALE);
      - opportunity_value is HIGH or MEDIUM;
      - candidate_competitiveness is STRONG or VERY_STRONG (never STRETCH/
        INSUFFICIENT_DATA/COMPETITIVE/LOW);
      - hard_eligibility is not INELIGIBLE;
      - no requirement_evidence item is a proven HARD_REQUIREMENT_GAP;
      - no requirement_evidence item is an uncertain CRITICAL/
        AMBIGUOUS_CRITICALITY mandatory factual requirement -- a genuine
        credential/skill gap is NOT the "human-resolvable" uncertainty this
        rule is for, and this is always read from structured
        requirement_evidence, never string-matched against priority_reasons.

    The last two checks are already implied by `priority` not being REJECT,
    but are re-checked explicitly here so this function's own contract does
    not silently depend on `_decide_priority`'s exact tier ordering. Once all
    of the above hold, the only thing left that can still be driving
    HUMAN_REVIEW is hard_eligibility == MANUAL_REVIEW; if it somehow isn't,
    this fails closed rather than guessing why priority is C."""
    if priority != Priority.HUMAN_REVIEW:
        return "", ()
    if vacancy_validity.value not in _PACKAGE_GATE_QUALIFYING_VALIDITY:
        return "", ()
    if opportunity_value.value not in _PACKAGE_GATE_QUALIFYING_OPPORTUNITY_VALUE:
        return "", ()
    if candidate_competitiveness.value not in _PACKAGE_GATE_QUALIFYING_COMPETITIVENESS:
        return "", ()
    if hard_eligibility.value == INELIGIBLE:
        return "", ()
    if any(r.classification == HARD_REQUIREMENT_GAP for r in requirement_evidence):
        return "", ()
    uncertain_critical = [
        r for r in requirement_evidence
        if r.is_mandatory and not r.is_behavioural and r.classification == NO_EVIDENCE
        and r.criticality in (CRITICAL, AMBIGUOUS_CRITICALITY)
    ]
    if uncertain_critical:
        return "", ()
    if hard_eligibility.value != MANUAL_REVIEW:
        return "", ()
    reasons = (
        f"Strong C opportunity: vacancy validity is {vacancy_validity.value}, opportunity value is "
        f"{opportunity_value.value}, candidate competitiveness is {candidate_competitiveness.value}, "
        "and no hard requirement gap or uncertain critical requirement was found -- the only remaining "
        "blocker is unresolved hard eligibility (geographic/work-authorization), which is "
        "human-resolvable. Internal package preparation may proceed for human review; external "
        "execution remains blocked.",
        *hard_eligibility.reasons,
    )
    return PREPARE_FOR_HUMAN_REVIEW, reasons


class JobIntelligenceService:
    """Computes JobIntelligence for an already-produced JobEvaluation,
    reusing CareerDecisionEngine/ATSEngine/EmployerService's own outputs
    (already on JobEvaluation) plus RemoteWorkEligibilityClassifier for the
    hard-eligibility dimension."""

    def __init__(self, eligibility_classifier: RemoteWorkEligibilityClassifier | None = None) -> None:
        self.eligibility_classifier = eligibility_classifier or RemoteWorkEligibilityClassifier()

    def classify_eligibility(self, evaluation: Any, opportunity: Any = None) -> RemoteEligibilityResult:
        """The hard-eligibility check alone, reusable by ApplicationService
        without requiring a full JobIntelligence computation."""
        subject = opportunity or opportunity_shim_from_job_analysis(evaluation.job_analysis, evaluation.job_description)
        return self.eligibility_classifier.classify(subject)

    def evaluate(self, evaluation: Any, opportunity: Any = None) -> JobIntelligence:
        eligibility_result = getattr(evaluation, "hard_eligibility", None) or self.classify_eligibility(evaluation, opportunity)

        hard_eligibility = _hard_eligibility(eligibility_result)
        vacancy_validity = _vacancy_validity(evaluation.job_analysis, evaluation.job_description, opportunity)
        opportunity_value = _opportunity_value(evaluation.employer, evaluation.career_decision, opportunity)

        profile = getattr(evaluation, "profile", None)
        requirement_evidence = _assess_requirement_evidence(evaluation.job_analysis, profile, evaluation.job_description)
        candidate_competitiveness = _candidate_competitiveness(evaluation.career_decision, requirement_evidence)

        priority, priority_reasons = _decide_priority(
            hard_eligibility, vacancy_validity, evaluation.screening_decision, evaluation.ats_result,
            opportunity_value, candidate_competitiveness, requirement_evidence,
        )
        package_gate, package_gate_reasons = _package_preparation_gate(
            priority, hard_eligibility, vacancy_validity, opportunity_value,
            candidate_competitiveness, requirement_evidence,
        )

        return JobIntelligence(
            vacancy_validity=vacancy_validity,
            hard_eligibility=hard_eligibility,
            opportunity_value=opportunity_value,
            candidate_competitiveness=candidate_competitiveness,
            application_alignment=_application_alignment(evaluation.ats_result),
            requirement_evidence=requirement_evidence,
            priority=priority,
            priority_reasons=priority_reasons,
            evidence=(eligibility_result.evidence,) if eligibility_result.evidence else (),
            package_gate=package_gate,
            package_gate_reasons=package_gate_reasons,
        )
