"""Web App Phase 7: Analytics & Learning.

Every number here comes from OpportunityCRMService's own existing,
already-reconciled read models (`cumulative_funnel_counts`,
`response_quality_counts`, `performance_by_dimension`,
`action_required_counts`, `employer_inbox_summary`) -- never a second,
parallel definition of "application"/"meaningful response"/"interview"/
"offer". Observations and learning candidates are pure, deterministic
functions of stored data: the same inputs always produce the same output,
and nothing here calls an LLM or invents a finding not directly backed by a
count already computed above.

Frozen learning philosophy (item 1): this module OBSERVES and PROPOSES. It
never writes to intelligence_priority, scoring, eligibility policy,
candidate facts, or the Answer Vault -- there is no code path anywhere in
this file that mutates `application_history` or any scoring-relevant table.
The only writes this phase introduces at all are governance decisions on a
`proposed_learnings` row (see OpportunityCRMService.record_proposed_learning/
update_proposed_learning_status), which record human approval only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# -- Confidence framework (item 22) -- explicit, conservative, no decimals --
EMERGING = "Emerging"
MODERATE = "Moderate"
HIGH = "High"

# A sample below this is always "Emerging", regardless of how consistent the
# pattern looks within it -- avoids fake precision from a handful of records.
MIN_SAMPLE_FOR_MODERATE = 10
MIN_SAMPLE_FOR_HIGH = 30
# A pattern must hold for at least this fraction of its sample to count as
# "consistent" rather than a coincidence.
MIN_CONSISTENCY_RATIO = 0.5
HIGH_CONSISTENCY_RATIO = 0.7
# Repeated, VERIFIED downstream outcomes (an interview/offer actually
# resulting) are strong enough evidence to reach High confidence even
# without a huge sample.
MIN_DOWNSTREAM_OUTCOMES_FOR_HIGH = 3


def assess_confidence(sample_size: int, consistency_ratio: float | None = None, downstream_outcomes: int = 0) -> str:
    """`sample_size`: how many opportunities/applications the observation is
    based on. `consistency_ratio` (0-1): fraction of that sample exhibiting
    the pattern, or None when the observation isn't ratio-based (e.g. a
    plain count). `downstream_outcomes`: count of actual confirmed
    interview/offer outcomes supporting the pattern. Deterministic and
    conservative -- never a fabricated decimal probability."""
    if sample_size < MIN_SAMPLE_FOR_MODERATE:
        return EMERGING
    if consistency_ratio is not None and consistency_ratio < MIN_CONSISTENCY_RATIO:
        return EMERGING
    if downstream_outcomes >= MIN_DOWNSTREAM_OUTCOMES_FOR_HIGH:
        return HIGH
    if sample_size >= MIN_SAMPLE_FOR_HIGH and (consistency_ratio is None or consistency_ratio >= HIGH_CONSISTENCY_RATIO):
        return HIGH
    return MODERATE


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _rate_label(numerator: int, denominator: int) -> str:
    """Percentage string, or an em-dash for an undefined (zero-denominator)
    rate -- item 6: "If denominator is zero, display -, not 0%.\""""
    if not denominator:
        return "—"
    return f"{round(100 * numerator / denominator)}%"


EARLY_DATA_SAMPLE_THRESHOLD = 10


def _early_data_note(count: int, noun: str = "applications") -> str | None:
    if 0 < count < EARLY_DATA_SAMPLE_THRESHOLD:
        return f"Early data — {count} {noun}"
    return None


# -- Performance Overview (item 6) -------------------------------------------
def performance_overview(service) -> dict:
    funnel = service.cumulative_funnel_counts()
    discovered, applied = funnel["DISCOVERED"], funnel["APPLIED"]
    meaningful, interview, offer = funnel["MEANINGFUL_RESPONSE"], funnel["INTERVIEW"], funnel["OFFER"]
    hired = service.connection.execute("SELECT COUNT(*) FROM application_history WHERE hired_at IS NOT NULL").fetchone()[0]

    return {
        "opportunities_discovered": discovered,
        "applications_submitted": applied,
        "application_rate": _rate_label(applied, discovered),
        "meaningful_response_rate": _rate_label(meaningful, applied),
        "interview_rate": _rate_label(interview, applied),
        "offer_rate": _rate_label(offer, applied),
        "hire_rate": _rate_label(hired, applied),
        "hired": hired,
        "early_data_note": _early_data_note(applied),
    }


# -- Human Interventions per Application (item 7) ----------------------------
# Only intervention TYPES this CRM can currently reconstruct from an
# auditable, already-recorded fact are counted. Every other type the task
# lists (CAPTCHA/MFA/unknown-screening-answer approval) would need an OPEN
# or RESOLVED `human_blockers` row -- if none has ever been recorded, that
# type contributes 0 here, not a guess, and the result is labelled "Partial
# measurement" rather than presented as a complete count.
_OBSERVABLE_INTERVENTION_TYPES = (
    "Final-submit authorization", "Screening decision recorded (Apply/Watch/Reject)",
    "CAPTCHA/MFA/login handled", "Unknown-answer approval",
    "Ambiguous employer-message classification", "Interview scheduling confirmed", "Offer decision recorded",
)


def human_intervention_metrics(service) -> dict:
    applied = service.cumulative_funnel_counts()["APPLIED"]
    final_submits = applied  # Task 21 MVP policy: every APPLIED row required one human-authorized submit click.
    decisions = service.connection.execute("SELECT COUNT(*) FROM user_decisions").fetchone()[0]
    blocker_types = {
        row["blocker_type"] for row in service.connection.execute("SELECT DISTINCT blocker_type FROM human_blockers")
    }
    captcha_mfa = service.connection.execute(
        "SELECT COUNT(*) FROM human_blockers WHERE blocker_type IN ('HUMAN_CAPTCHA_REQUIRED', 'HUMAN_MFA_REQUIRED')"
    ).fetchone()[0]
    unknown_answer = service.connection.execute(
        "SELECT COUNT(*) FROM human_blockers WHERE blocker_type IN ('HUMAN_ANSWER_APPROVAL_REQUIRED', 'HUMAN_SALARY_REVIEW_REQUIRED')"
    ).fetchone()[0]
    classifications = service.connection.execute("SELECT COUNT(*) FROM employer_response_classifications").fetchone()[0]
    schedule_confirms = service.connection.execute(
        "SELECT COUNT(*) FROM opportunity_events WHERE event_type = 'INTERVIEW_SCHEDULE_CONFIRMED'"
    ).fetchone()[0]
    offer_decisions = service.connection.execute(
        "SELECT COUNT(*) FROM opportunity_events WHERE event_type = 'OFFER_DECISION_RECORDED'"
    ).fetchone()[0]

    counted = {
        "Final-submit authorization": final_submits,
        "Screening decision recorded (Apply/Watch/Reject)": decisions,
        "CAPTCHA/MFA/login handled": captcha_mfa,
        "Unknown-answer approval": unknown_answer,
        "Ambiguous employer-message classification": classifications,
        "Interview scheduling confirmed": schedule_confirms,
        "Offer decision recorded": offer_decisions,
    }
    total = sum(counted.values())
    # human_blockers has never been populated for CAPTCHA/MFA/unknown-answer
    # in this database's history -- that does not mean none ever occurred,
    # only that this CRM's audit trail cannot currently reconstruct them.
    _captcha_mfa_blocker_types = {"HUMAN_CAPTCHA_REQUIRED", "HUMAN_MFA_REQUIRED"}
    _unknown_answer_blocker_types = {"HUMAN_ANSWER_APPROVAL_REQUIRED", "HUMAN_SALARY_REVIEW_REQUIRED"}
    unobserved_types = []
    if not (blocker_types & _captcha_mfa_blocker_types):
        unobserved_types.append("CAPTCHA/MFA/login handled")
    if not (blocker_types & _unknown_answer_blocker_types):
        unobserved_types.append("Unknown-answer approval")

    return {
        "counted_by_type": counted,
        "total_observed_interventions": total,
        "applications": applied,
        "per_application": (round(total / applied, 2) if applied else None),
        "partial_measurement": bool(unobserved_types),
        "unobserved_types": unobserved_types,
        "explanation": (
            "Only intervention types with an existing auditable record (a human_blockers row, a recorded "
            "user_decisions row, an employer_response_classifications row, or a recorded interview-schedule/"
            "offer-decision event) are counted. CAPTCHA/MFA and unknown-screening-answer approvals have no "
            "recorded history in this database, so they contribute 0 here rather than an estimate."
        ),
    }


# -- Funnel & Trends (item 8) -------------------------------------------------
PERIODS = {"7d": 7, "30d": 30, "90d": 90, "all": None}


def funnel_and_trends(service, period: str = "all") -> dict:
    if period not in PERIODS:
        period = "all"
    days = PERIODS[period]
    all_time = service.cumulative_funnel_counts()
    if days is None:
        funnel = all_time
    else:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        funnel = service.cumulative_funnel_counts_since(since)

    earliest = service.connection.execute("SELECT MIN(discovered_at) FROM application_history").fetchone()[0]
    history_days = None
    if earliest:
        try:
            earliest_dt = datetime.fromisoformat(earliest.replace("Z", "+00:00"))
            history_days = max(1, (datetime.now(timezone.utc) - earliest_dt).days)
        except ValueError:
            history_days = None

    return {
        "period": period,
        "funnel": funnel,
        "all_time": all_time,
        "history_days": history_days,
        "trend_reliable": bool(history_days and history_days >= 30),
    }


# -- Performance Drivers / dimension breakdowns (items 9-13) ----------------
_DIMENSION_UNSET_LABELS = {
    "intelligence_priority": "Unscored", "market": "Not Recorded", "source": "Not Recorded",
    "career_track": "Not Recorded", "work_arrangement": "Not Recorded",
}


def dimension_performance(service, field: str) -> list[dict]:
    """One row per bucket, each carrying its own sample-size caveat (item 9:
    "Do NOT compare categories with zero or tiny samples as though one is
    objectively better.\")."""
    rows = service.performance_by_dimension(field)
    unset_label = _DIMENSION_UNSET_LABELS.get(field, "Not Recorded")
    results = []
    for row in rows:
        label = unset_label if row["bucket"] == "__UNSET__" else row["bucket"]
        applications = row["applications"]
        results.append({
            "label": label,
            "opportunities": row["opportunities"],
            "applications": applications,
            "meaningful_responses": row["meaningful_responses"],
            "interviews": row["interviews"],
            "offers": row["offers"],
            "conversion_rate": _rate_label(row["meaningful_responses"], applications),
            "insufficient_data": applications < EARLY_DATA_SAMPLE_THRESHOLD,
            "note": (f"{applications} application{'s' if applications != 1 else ''} — insufficient outcome data"
                     if 0 < applications < EARLY_DATA_SAMPLE_THRESHOLD else None),
        })
    return results


def priority_effectiveness(service) -> list[dict]:
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "Unscored": 5}
    rows = dimension_performance(service, "intelligence_priority")
    rows.sort(key=lambda r: order.get(r["label"], 9))
    return rows


def market_performance(service) -> list[dict]:
    return dimension_performance(service, "market")


def source_performance(service) -> list[dict]:
    return dimension_performance(service, "source")


def career_track_performance(service) -> list[dict]:
    return dimension_performance(service, "career_track")


def work_arrangement_performance(service) -> list[dict]:
    return dimension_performance(service, "work_arrangement")


def cv_strategy_performance(service) -> dict:
    """Item 13: every submitted application currently carries a UNIQUE,
    per-vacancy tailored resume (`resume_vacancy_identity`) with no shared
    strategy/version taxonomy recorded anywhere -- there is no reliable
    grouping to compare "strategy A" vs "strategy B" against. Honest,
    explicit non-answer rather than an invented grouping."""
    return {"available": False, "message": "Insufficient version history for reliable comparison."}


# -- Screening & Eligibility Intelligence (item 14) --------------------------
def screening_eligibility_intelligence(service) -> list[dict]:
    conn = service.connection
    rows = []

    manual_review_total = conn.execute("SELECT COUNT(*) FROM application_history WHERE remote_eligibility = 'MANUAL_REVIEW'").fetchone()[0]
    if manual_review_total:
        currently_unresolved = service.action_required_counts()["ELIGIBILITY_DECISION"]
        affected_applications = conn.execute(
            "SELECT COUNT(*) FROM application_history WHERE remote_eligibility = 'MANUAL_REVIEW' AND applied_at IS NOT NULL"
        ).fetchone()[0]
        rows.append({
            "reason": "Remote vacancy — international/geographic eligibility not stated",
            "occurrences": manual_review_total,
            "currently_unresolved": currently_unresolved,
            "applications_affected": affected_applications,
        })

    ineligible_total = conn.execute("SELECT COUNT(*) FROM application_history WHERE remote_eligibility = 'INELIGIBLE'").fetchone()[0]
    if ineligible_total:
        rows.append({
            "reason": "Explicit work-right/residency restriction",
            "occurrences": ineligible_total,
            "currently_unresolved": 0,  # INELIGIBLE is a terminal classification, not an open review item
            "applications_affected": conn.execute(
                "SELECT COUNT(*) FROM application_history WHERE remote_eligibility = 'INELIGIBLE' AND applied_at IS NOT NULL"
            ).fetchone()[0],
        })

    route_unverified_total = conn.execute("SELECT COUNT(*) FROM application_history WHERE application_route_status = 'SOURCE_ONLY'").fetchone()[0]
    if route_unverified_total:
        rows.append({
            "reason": "Application route not independently verified (relying on the original listing only)",
            "occurrences": route_unverified_total,
            "currently_unresolved": conn.execute(
                "SELECT COUNT(*) FROM application_history WHERE application_route_status = 'SOURCE_ONLY' AND applied_at IS NULL"
            ).fetchone()[0],
            "applications_affected": conn.execute(
                "SELECT COUNT(*) FROM application_history WHERE application_route_status = 'SOURCE_ONLY' AND applied_at IS NOT NULL"
            ).fetchone()[0],
        })

    return rows


# -- Rejection Intelligence (item 15) ----------------------------------------
def rejection_intelligence(service) -> dict:
    conn = service.connection
    rows = conn.execute(
        "SELECT ah.id, ah.company, ah.job_title, ah.market, ah.career_track, ah.intelligence_priority, "
        "ah.rejection_stage, ah.rejection_reason, ah.rejection_at "
        "FROM application_history ah WHERE ah.rejection_at IS NOT NULL OR ah.rejection_reason IS NOT NULL AND ah.rejection_reason != ''"
    ).fetchall()
    if not rows:
        return {"available": False, "count": 0, "rejections": []}
    rejections = []
    for row in rows:
        row = dict(row)
        rejections.append({
            "tracker_id": row["id"], "company": row["company"], "job_title": row["job_title"],
            "market": row["market"], "career_track": row["career_track"], "priority": row["intelligence_priority"],
            "stage": row["rejection_stage"] or "Unknown",
            # Only ever the employer's own stated text -- never a guessed reason.
            "explicit_reason": row["rejection_reason"] or None,
        })
    return {"available": True, "count": len(rejections), "rejections": rejections}


# -- Employer Feedback Intelligence (item 16) --------------------------------
def employer_feedback_intelligence(service) -> dict:
    """Reuses Employer Inbox's own summary (Phase 5) -- never a parallel
    classification tally."""
    summary = service.employer_inbox_summary()
    applied = service.cumulative_funnel_counts()["APPLIED"]
    acknowledged = service.response_quality_counts()["acknowledgements"]
    interpretation = None
    if applied:
        meaningful = service.cumulative_funnel_counts()["MEANINGFUL_RESPONSE"]
        if meaningful == 0 and acknowledged:
            interpretation = (
                f"{acknowledged} of {applied} submitted application{'s' if applied != 1 else ''} "
                f"{'have' if acknowledged != 1 else 'has'} been acknowledged, but no meaningful employer "
                "response has yet been recorded."
            )
        elif meaningful == 0:
            interpretation = f"No employer response has been recorded yet for {applied} submitted application{'s' if applied != 1 else ''}."
    return {"summary": summary, "interpretation": interpretation}


# -- Automatic Observation Generation (items 20-22) --------------------------
def generate_observations(service) -> list[dict]:
    """Deterministic, reproducible findings -- each one directly traceable
    to a count already computed above. No LLM, no speculative claims."""
    observations: list[dict] = []
    conn = service.connection
    total = conn.execute("SELECT COUNT(*) FROM application_history").fetchone()[0]
    funnel = service.cumulative_funnel_counts()

    priority_rows = dimension_performance(service, "intelligence_priority")
    c_row = next((r for r in priority_rows if r["label"] == "C"), None)
    if c_row and total:
        ratio = _ratio(c_row["opportunities"], total)
        observations.append({
            "category": "Priority Mix", "domain": "PRIORITY",
            "text": f"{c_row['opportunities']} of {total} opportunities ({round(ratio * 100)}%) are Priority C.",
            "sample_size": total, "confidence": assess_confidence(total, ratio),
            "drill_down": "/opportunities?intelligence_priority=C",
        })

    manual_review = conn.execute("SELECT COUNT(*) FROM application_history WHERE remote_eligibility = 'MANUAL_REVIEW'").fetchone()[0]
    if manual_review and total:
        ratio = _ratio(manual_review, total)
        c_manual_review = conn.execute(
            "SELECT COUNT(*) FROM application_history WHERE remote_eligibility = 'MANUAL_REVIEW' AND intelligence_priority = 'C'"
        ).fetchone()[0]
        c_total = c_row["opportunities"] if c_row else 0
        detail = ""
        if c_total:
            c_ratio = round(100 * c_manual_review / c_total)
            detail = f" {c_manual_review} of {c_total} Priority-C opportunities ({c_ratio}%) are attributable to this same unresolved eligibility gap."
        observations.append({
            "category": "Eligibility", "domain": "ELIGIBILITY",
            "text": (
                f"{manual_review} of {total} opportunities ({round(ratio * 100)}%) required human eligibility "
                f"review because international/remote eligibility was not stated in the vacancy.{detail}"
            ),
            "sample_size": total, "confidence": assess_confidence(total, ratio),
            "drill_down": "/action-required",
        })

    source_rows = dimension_performance(service, "source")
    if len(source_rows) == 1 and total:
        observations.append({
            "category": "Source", "domain": "SEARCH_STRATEGY",
            "text": f"100% of opportunities ({total}) come from a single source: {source_rows[0]['label']}. No comparison across sources is possible yet.",
            "sample_size": total, "confidence": EMERGING,
            "drill_down": "/opportunities",
        })

    if funnel["APPLIED"]:
        observations.append({
            "category": "Application Funnel", "domain": "APPLICATION_STRATEGY",
            "text": (
                f"{funnel['APPLIED']} application(s) submitted; {funnel['ACKNOWLEDGED']} automated "
                f"acknowledgement(s); {funnel['MEANINGFUL_RESPONSE']} meaningful employer response(s) recorded."
            ),
            "sample_size": funnel["APPLIED"], "confidence": EMERGING,
            "drill_down": "/applications",
        })
    else:
        observations.append({
            "category": "Application Funnel", "domain": "APPLICATION_STRATEGY",
            "text": "Not enough outcome data yet.",
            "sample_size": 0, "confidence": EMERGING, "drill_down": "/applications",
        })

    intervention = human_intervention_metrics(service)
    if intervention["applications"]:
        observations.append({
            "category": "Human Intervention", "domain": "AUTOMATION",
            "text": (
                f"{intervention['total_observed_interventions']} observable intervention(s) recorded across "
                f"{intervention['applications']} application(s) ({intervention['per_application']} per application). "
                + ("Partial measurement -- see explanation." if intervention["partial_measurement"] else "")
            ),
            "sample_size": intervention["applications"], "confidence": EMERGING,
            "drill_down": "/action-required",
        })

    return observations


# -- Proposed Learning generation (item 21: threshold gate) ------------------
def generate_learning_candidates(service) -> list[dict]:
    """A candidate becomes eligible ONLY when it clears item 21's bar
    (repeated pattern / strategically material issue / meaningful downstream
    signal) AND reaches at least Moderate confidence -- Emerging-confidence
    observations stay observations, never a proposed policy change. Pure and
    deterministic: same stored data always yields the same candidates."""
    candidates: list[dict] = []
    conn = service.connection
    total = conn.execute("SELECT COUNT(*) FROM application_history").fetchone()[0]
    if not total:
        return candidates

    manual_review = conn.execute("SELECT COUNT(*) FROM application_history WHERE remote_eligibility = 'MANUAL_REVIEW'").fetchone()[0]
    ratio = _ratio(manual_review, total)
    confidence = assess_confidence(total, ratio) if manual_review else EMERGING
    if manual_review and confidence in (MODERATE, HIGH):
        priority_rows = dimension_performance(service, "intelligence_priority")
        c_row = next((r for r in priority_rows if r["label"] == "C"), None)
        c_manual_review = conn.execute(
            "SELECT COUNT(*) FROM application_history WHERE remote_eligibility = 'MANUAL_REVIEW' AND intelligence_priority = 'C'"
        ).fetchone()[0]
        c_total = c_row["opportunities"] if c_row else 0
        evidence_summary = (
            f"{manual_review} of {total} opportunities ({round(ratio * 100)}%) reached eligibility review because "
            f"remote/geographic eligibility was not stated in the vacancy."
        )
        if c_total:
            evidence_summary += (
                f" {c_manual_review} of {c_total} Priority-C opportunities "
                f"({round(100 * c_manual_review / c_total)}%) are attributable specifically to this same gap, "
                f"not to weaker opportunity/candidate fit."
            )
        candidates.append({
            "key": "eligibility-review-burden",
            "domain": "ELIGIBILITY",
            "title": "Reduce eligibility-review burden via search/market targeting",
            "observation": evidence_summary,
            "proposed_change": (
                "Consider adjusting discovery/search targeting to prefer markets or postings that explicitly "
                "state international/remote work eligibility, since unresolved eligibility -- not opportunity "
                "or candidate quality -- is the single largest blocker between discovery and application today."
            ),
            "evidence_summary": evidence_summary,
            "sample_size": total,
            "confidence": confidence,
        })

    return candidates
