"""Task 21.14A: typed result for the job-intelligence funnel stage that sits
between career evaluation (CareerDecisionEngine/ATS/evidence -- all reused,
not replaced) and application preparation (ApplicationService).

Deliberately NOT a weighted composite score. Each dimension below is kept
separate, reuses an already-existing engine's own output, and carries its
own source/reasons so a human can see exactly why a priority was assigned.
`Priority` is a distinct concept from `CareerDecision.priority` (an existing
HIGH/MEDIUM/LOW urgency field on the career-fit scorecard) -- this one is
the funnel's own APPLY/HOLD/REJECT routing decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Priority(str, Enum):
    """Funnel routing decision. Letter values match the task's own scheme."""

    PRIORITY_APPLY = "A"
    APPLY = "B"
    HUMAN_REVIEW = "C"
    WATCH = "D"
    REJECT = "E"


@dataclass(frozen=True)
class DimensionScore:
    """One funnel dimension: a value plus where it came from and why. Never
    combined into a hidden composite -- every dimension stays independently
    inspectable."""

    value: float | str | None
    source: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


# Task 21.14D: per-requirement evidence classification. STRONG/PARTIAL/NO
# reflect what verified candidate evidence was found (or wasn't) for one
# vacancy requirement; HARD_REQUIREMENT_GAP is reserved for a *proven*
# conflict (never merely absent evidence -- see
# job_intelligence_service._classify_requirement).
STRONG_EVIDENCE = "STRONG_EVIDENCE"
PARTIAL_EVIDENCE = "PARTIAL_EVIDENCE"
NO_EVIDENCE = "NO_EVIDENCE"
HARD_REQUIREMENT_GAP = "HARD_REQUIREMENT_GAP"


@dataclass(frozen=True)
class RequirementEvidence:
    """One vacancy requirement's evidence assessment. `is_behavioural`
    marks generic soft-skill wording (e.g. "attention to detail") that
    verified evidence can neither confirm nor deny as a factual claim, so
    it can never become a HARD_REQUIREMENT_GAP. `supporting_evidence` is
    always a subset of already-VERIFIED candidate facts -- missing evidence
    is reported as NO_EVIDENCE, never fabricated into a claim.

    Task 21.15C: `criticality` (CRITICAL/NON_CRITICAL/AMBIGUOUS_CRITICALITY/
    NOT_APPLICABLE) distinguishes a factual requirement the vacancy's own
    wording frames as mandatory/essential from one that isn't -- only a
    CRITICAL (or AMBIGUOUS_CRITICALITY, handled conservatively) uncertain
    factual requirement forces HUMAN_REVIEW; NOT_APPLICABLE covers
    behavioural/non-mandatory items, for which criticality isn't assessed."""

    requirement: str
    classification: str
    is_mandatory: bool
    is_behavioural: bool
    supporting_evidence: tuple[str, ...]
    reason: str
    criticality: str = "NOT_APPLICABLE"


@dataclass(frozen=True)
class JobIntelligence:
    """The funnel's typed result for one vacancy.

    Task 21.14C: `vacancy_validity` and `opportunity_value` are real,
    rule-based dimensions. Task 21.14D: `candidate_competitiveness` is now a
    verified-evidence-aware band (VERY_STRONG..LOW..INSUFFICIENT_DATA), and
    `requirement_evidence` makes that evidence inspectable per-requirement.
    `priority` is a precedence-ordered rule table -- never a weighted
    composite:

      1. hard INELIGIBLE, vacancy INVALID/STALE, or a proven
         HARD_REQUIREMENT_GAP                                -> REJECT
      2. hard eligibility/vacancy validity UNCERTAIN, or an
         uncertain (NO_EVIDENCE) mandatory factual requirement -> HUMAN_REVIEW
      3. competitiveness LOW                                  -> WATCH
      4. competitiveness STRETCH or INSUFFICIENT_DATA          -> HUMAN_REVIEW
      5. competitiveness COMPETITIVE/STRONG/VERY_STRONG: existing
         screening_decision + ATS grade + opportunity_value decide
         APPLY / PRIORITY_APPLY / HUMAN_REVIEW / WATCH.

    A LOW opportunity_value alone (tier 5) still only downgrades to WATCH,
    never rejects on its own. Every branch carries its own reasons.
    """

    vacancy_validity: DimensionScore
    hard_eligibility: DimensionScore
    opportunity_value: DimensionScore
    candidate_competitiveness: DimensionScore
    application_alignment: DimensionScore
    requirement_evidence: tuple[RequirementEvidence, ...]
    priority: Priority
    priority_reasons: tuple[str, ...]
    evidence: tuple[str, ...] = field(default_factory=tuple)
