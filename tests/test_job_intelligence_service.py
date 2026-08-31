"""Task 21.14A/C: JobIntelligenceService computes each funnel dimension from
an already-existing engine's own output (CareerDecisionEngine/ATSEngine/
EmployerService/RemoteWorkEligibilityClassifier) -- nothing here re-scores
anything. Vacancy validity and opportunity value (Task 21.14C) are real,
rule-based dimensions -- transparent thresholds/majority-of-available-
signals, never a weighted composite -- and both now influence priority
alongside the existing hard-eligibility gate.

Fully hermetic: JobEvaluation-like objects are hand-built (SimpleNamespace),
no OpenAI call, no file/network/tracker/Gmail access at all. No live web/
network validation is ever performed for vacancy validity.
"""

from types import SimpleNamespace

from app.models.job_intelligence import Priority
from app.services.job_intelligence_service import (
    JobIntelligenceService,
    _is_behavioural,
    _normalize_employer_score,
    _requirement_criticality,
)
from app.services.remote_work_eligibility import ELIGIBLE, INELIGIBLE, MANUAL_REVIEW, NOT_APPLICABLE

# >=60 chars (MIN_DESCRIPTION_CHARS) but <200 (STRONG_DESCRIPTION_CHARS).
USABLE_DESCRIPTION = "Please submit your resume for this accounting role covering financial reporting and tax."
# >=200 chars, for VERIFIED tests.
STRONG_DESCRIPTION = USABLE_DESCRIPTION + (
    " Responsibilities include monthly close, statutory reporting, tax compliance, budgeting, "
    "forecasting and liaison with the finance leadership team across multiple entities and markets."
)
SHORT_DESCRIPTION = "N/A"

INELIGIBLE_RESULT = SimpleNamespace(decision=INELIGIBLE, scope="REMOTE_COUNTRY_RESTRICTED", reason="UK residence required", evidence="uk-based")
MANUAL_REVIEW_RESULT = SimpleNamespace(decision=MANUAL_REVIEW, scope="REMOTE_ELIGIBILITY_UNCLEAR", reason="Remote vacancy but geographic eligibility not stated", evidence="")
ELIGIBLE_RESULT = SimpleNamespace(decision=ELIGIBLE, scope="REMOTE_GLOBAL", reason="Explicit worldwide remote eligibility", evidence="work from anywhere")
NOT_APPLICABLE_RESULT = SimpleNamespace(decision=NOT_APPLICABLE, scope="REMOTE_NOT_APPLICABLE", reason="Vacancy is not confirmed remote", evidence="")


def _scorecard(category, score, weight=10):
    return SimpleNamespace(category=category, score=score, weight=weight)


def _evaluation(
    hard_eligibility,
    screening_decision="AUTO_APPLY",
    ats_grade="C",
    career_score=79.0,
    employer_score=6.5,
    career_growth_score=None,
    scorecards=None,
    job_analysis=None,
    job_description=USABLE_DESCRIPTION,
):
    employer = SimpleNamespace(overall_score=employer_score)
    if career_growth_score is not None:
        employer.career_growth_score = career_growth_score
    return SimpleNamespace(
        job_analysis=job_analysis if job_analysis is not None else {"job_title": "Accountant", "company": "Acme Partners"},
        job_description=job_description,
        employer=employer,
        career_decision=SimpleNamespace(overall_score=career_score, scorecards=scorecards or []),
        ats_result={"ats_score": {"overall_score": 71.2, "grade": ats_grade}},
        screening_decision=screening_decision,
        hard_eligibility=hard_eligibility,
    )


def _opportunity(**overrides):
    base = dict(
        metadata={}, posted_date="", application_route_status="", application_route_confidence="",
        job_url="", application_url="", source_listing_url="", work_arrangement="UNKNOWN", salary="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- Priority: hard eligibility (unchanged from 21.14A) ---------------------

def test_ineligible_vacancy_is_rejected():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(INELIGIBLE_RESULT))
    assert intelligence.priority == Priority.REJECT
    assert intelligence.hard_eligibility.value == INELIGIBLE
    assert intelligence.priority_reasons  # never an unexplained decision


def test_uncertain_eligibility_routes_to_human_review_not_priority_apply_or_apply():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        MANUAL_REVIEW_RESULT, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.5,
    ))
    # Even with a strong screening decision + ATS grade + high opportunity
    # value, uncertain hard eligibility must win.
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert intelligence.priority not in (Priority.PRIORITY_APPLY, Priority.APPLY)


def test_hard_eligibility_overrides_all_positive_scores():
    """INELIGIBLE + strong ATS + HIGH opportunity value is still REJECT."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        INELIGIBLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.5,
        job_description=STRONG_DESCRIPTION,
    ))
    assert intelligence.priority == Priority.REJECT


# --- Priority: eligibility resolved, screening/ATS/opportunity-value decide -

def test_eligible_auto_apply_with_strong_ats_and_high_opportunity_value_is_priority_apply():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        ELIGIBLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="A", employer_score=9.0,
    ))
    assert intelligence.opportunity_value.value == "HIGH"
    assert intelligence.priority == Priority.PRIORITY_APPLY


def test_eligible_auto_apply_strong_ats_but_only_medium_opportunity_value_is_apply_not_priority_apply():
    """Task 21.14C: PRIORITY_APPLY now requires HIGH opportunity value too,
    not ATS grade alone -- reflects "maximize career value", not just fit."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        ELIGIBLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="A", employer_score=6.5,
    ))
    assert intelligence.opportunity_value.value == "MEDIUM"
    assert intelligence.priority == Priority.APPLY


def test_eligible_auto_apply_with_ordinary_ats_grade_is_apply_not_priority_apply():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="C", employer_score=6.5))
    assert intelligence.priority == Priority.APPLY


def test_low_opportunity_value_downgrades_auto_apply_to_watch_not_reject():
    """Task 21.14C section 2: LOW alone downgrades/watches, never hard-rejects."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        ELIGIBLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=2.0,
    ))
    assert intelligence.opportunity_value.value == "LOW"
    assert intelligence.priority == Priority.WATCH
    assert intelligence.priority != Priority.REJECT


def test_not_applicable_eligibility_behaves_like_eligible_for_priority():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(NOT_APPLICABLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="C", employer_score=6.5))
    assert intelligence.priority == Priority.APPLY


def test_eligible_review_decision_is_human_review():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, screening_decision="REVIEW"))
    assert intelligence.priority == Priority.HUMAN_REVIEW


def test_eligible_skip_decision_is_watch_not_reject():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, screening_decision="SKIP"))
    assert intelligence.priority == Priority.WATCH


def test_priority_always_carries_reasons():
    service = JobIntelligenceService()
    for hard_eligibility, screening_decision in (
        (INELIGIBLE_RESULT, "AUTO_APPLY"), (MANUAL_REVIEW_RESULT, "AUTO_APPLY"),
        (ELIGIBLE_RESULT, "AUTO_APPLY"), (ELIGIBLE_RESULT, "REVIEW"), (ELIGIBLE_RESULT, "SKIP"),
    ):
        intelligence = service.evaluate(_evaluation(hard_eligibility, screening_decision=screening_decision))
        assert intelligence.priority_reasons
        assert all(isinstance(reason, str) and reason for reason in intelligence.priority_reasons)


# --- Vacancy validity (Task 21.14C) -----------------------------------------

def test_verified_strong_vacancy():
    service = JobIntelligenceService()
    opportunity = _opportunity(application_route_status="RESOLVED", application_route_confidence="HIGH")
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, job_description=STRONG_DESCRIPTION), opportunity=opportunity)
    assert intelligence.vacancy_validity.value == "VERIFIED"
    assert intelligence.vacancy_validity.reasons


def test_likely_valid_without_opportunity_metadata():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, job_description=USABLE_DESCRIPTION))
    assert intelligence.vacancy_validity.value == "LIKELY_VALID"


def test_stale_vacancy_from_old_posted_date():
    service = JobIntelligenceService()
    opportunity = _opportunity(posted_date="2020-01-01T00:00:00+00:00")
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT), opportunity=opportunity)
    assert intelligence.vacancy_validity.value == "STALE"
    assert intelligence.priority == Priority.REJECT


def test_invalid_vacancy_from_unusable_description():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, job_description=SHORT_DESCRIPTION))
    assert intelligence.vacancy_validity.value == "INVALID"
    assert intelligence.priority == Priority.REJECT


def test_invalid_vacancy_from_duplicate_signal():
    service = JobIntelligenceService()
    opportunity = _opportunity(metadata={"history_status": "DUPLICATE"})
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT), opportunity=opportunity)
    assert intelligence.vacancy_validity.value == "INVALID"


def test_invalid_vacancy_missing_title_and_company():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, job_analysis={}))
    assert intelligence.vacancy_validity.value == "INVALID"


def test_uncertain_validity_from_partial_identification_routes_to_human_review():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, job_analysis={"job_title": "Accountant"}))
    assert intelligence.vacancy_validity.value == "UNCERTAIN"
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert intelligence.priority_reasons


def test_uncertain_validity_from_unresolved_route_with_no_url():
    service = JobIntelligenceService()
    opportunity = _opportunity(application_route_status="APPLICATION_ROUTE_UNRESOLVED")
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT), opportunity=opportunity)
    assert intelligence.vacancy_validity.value == "UNCERTAIN"
    assert intelligence.priority == Priority.HUMAN_REVIEW


def test_no_live_network_validation_is_ever_performed():
    """The validity computation never reaches outside the supplied
    job_analysis/job_description/opportunity -- confirmed by source
    inspection, not just behavior."""
    import inspect

    import app.services.job_intelligence_service as module
    source = inspect.getsource(module)
    for forbidden in ("requests.", "httpx.", "urlopen", "socket."):
        assert forbidden not in source


def test_strong_ats_cannot_rescue_invalid_vacancy():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        ELIGIBLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.5,
        job_description=SHORT_DESCRIPTION,
    ))
    assert intelligence.priority == Priority.REJECT


# --- Opportunity value (Task 21.14C) ----------------------------------------

def test_high_value_opportunity_from_employer_and_industry_signals():
    service = JobIntelligenceService()
    scorecards = [_scorecard("Industry", 9, 10)]
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=9.0, scorecards=scorecards))
    assert intelligence.opportunity_value.value == "HIGH"
    assert intelligence.opportunity_value.reasons


def test_low_value_opportunity_from_weak_employer_score():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=1.5))
    assert intelligence.opportunity_value.value == "LOW"


def test_opportunity_value_insufficient_data_when_no_signal_available():
    service = JobIntelligenceService()
    evaluation = _evaluation(ELIGIBLE_RESULT)
    evaluation.employer = SimpleNamespace()  # no overall_score, no career_growth_score
    intelligence = service.evaluate(evaluation)
    assert intelligence.opportunity_value.value == "INSUFFICIENT_DATA"


def test_opportunity_value_uses_career_growth_scorecard_fallback_when_employer_lacks_it():
    service = JobIntelligenceService()
    scorecards = [_scorecard("Career Growth", 9, 10)]
    evaluation = _evaluation(INELIGIBLE_RESULT, employer_score=None)
    evaluation.employer = SimpleNamespace()  # no overall_score, no career_growth_score attr
    evaluation.career_decision.scorecards = scorecards
    intelligence = JobIntelligenceService().evaluate(evaluation)
    assert intelligence.opportunity_value.value == "HIGH"
    assert any("Career Growth scorecard" in reason for reason in intelligence.opportunity_value.reasons)


def test_opportunity_value_geography_and_compensation_are_evidence_only_not_scored():
    """Two evaluations with identical employer/industry/career-growth
    signals but different work_arrangement/salary must produce the SAME
    band -- those fields are supplementary evidence, never inputs to the
    tier decision."""
    service = JobIntelligenceService()
    remote_high_salary = _opportunity(work_arrangement="REMOTE", salary="$150,000 - $180,000")
    onsite_no_salary = _opportunity(work_arrangement="ON_SITE", salary="")

    a = service.evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=9.0), opportunity=remote_high_salary)
    b = service.evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=9.0), opportunity=onsite_no_salary)

    assert a.opportunity_value.value == b.opportunity_value.value == "HIGH"
    assert any("REMOTE" in reason for reason in a.opportunity_value.reasons)
    assert any("150,000" in reason for reason in a.opportunity_value.reasons)


# --- Task 21.15A: EmployerService score normalization ------------------------
# EmployerService.overall_score/career_growth_score are produced on a native
# 0-10 scale (Task 21.15 observed 3-9.75 across 40 real vacancies; confirmed
# by CareerDecisionEngine's weight=10 clamp and RecruiterReasoningService's
# `career_growth_score * 10` conversion of the same two fields -- see
# _normalize_employer_score's docstring for the full evidence trail). Before
# this fix, _opportunity_value() fed these raw 0-10 values straight into
# _tier() (which expects 0-100), so opportunity_value was LOW for 100% of
# real vacancies and PRIORITY_APPLY/APPLY were structurally unreachable.

def test_normalize_employer_score_converts_0_to_10_scale_to_0_to_100():
    assert _normalize_employer_score(7.5) == 75.0
    assert _normalize_employer_score(3.0) == 30.0
    assert _normalize_employer_score(9.75) == 97.5


def test_normalize_employer_score_boundary_values():
    assert _normalize_employer_score(0.0) == 0.0
    assert _normalize_employer_score(10.0) == 100.0
    # Defensive clamp: a value above the documented 0-10 range never
    # produces a normalized score above 100.
    assert _normalize_employer_score(12.0) == 100.0


def test_normalize_employer_score_passes_through_none():
    assert _normalize_employer_score(None) is None


def test_realistic_employer_score_reaches_high_tier_opportunity_value_not_low():
    """The exact defect Task 21.15 found: a real EmployerService-shaped
    score of 7.5 (within the observed 3-9.75 live range) must normalize to
    HIGH, not LOW."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=7.5))
    assert intelligence.opportunity_value.value == "HIGH"


def test_realistic_employer_score_at_medium_tier():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=6.0))
    assert intelligence.opportunity_value.value == "MEDIUM"


def test_realistic_employer_score_at_low_tier():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=3.0))
    assert intelligence.opportunity_value.value == "LOW"


def test_career_growth_direct_attribute_is_normalized_from_0_to_10_scale():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        ELIGIBLE_RESULT, employer_score=None, career_growth_score=8.0,
    ))
    assert any("normalized to 80.0/100" in reason for reason in intelligence.opportunity_value.reasons)


def test_career_growth_scorecard_fallback_is_not_double_normalized():
    """When employer.career_growth_score is absent, the Career Growth
    CareerDecisionEngine scorecard percentage is used instead -- it is
    ALREADY on a 0-100 scale (score/weight*100) and must not be run through
    the EmployerService *10 conversion a second time (which would inflate
    an 80% scorecard reading to a clamped 100%)."""
    service = JobIntelligenceService()
    scorecards = [_scorecard("Career Growth", 8, 10)]  # 80% on the scorecard's own 0-100 scale
    evaluation = _evaluation(ELIGIBLE_RESULT, employer_score=None, scorecards=scorecards)
    evaluation.employer = SimpleNamespace()  # no overall_score, no career_growth_score attr
    intelligence = JobIntelligenceService().evaluate(evaluation)
    assert intelligence.opportunity_value.value == "HIGH"
    reason = next(r for r in intelligence.opportunity_value.reasons if "Career Growth scorecard" in r)
    assert "normalized" not in reason  # proves this reason came from the scorecard path, not EmployerService


def test_industry_scorecard_math_is_unaffected_by_the_employer_score_fix():
    """Industry relevance is already a CareerDecisionEngine score-as-percent-
    of-weight value -- this fix must not touch it at all."""
    service = JobIntelligenceService()
    scorecards = [_scorecard("Industry", 3, 10)]  # 30% -> LOW, unaffected by employer normalization
    evaluation = _evaluation(ELIGIBLE_RESULT, employer_score=None, scorecards=scorecards)
    evaluation.employer = SimpleNamespace()
    intelligence = JobIntelligenceService().evaluate(evaluation)
    assert intelligence.opportunity_value.value == "LOW"
    assert any("3/10 on the Industry scorecard" in r for r in intelligence.opportunity_value.reasons)


def test_realistic_employer_score_makes_priority_apply_reachable():
    """Before this fix, no real (0-10-scale) employer score could ever
    reach HIGH opportunity_value, so PRIORITY_APPLY was structurally
    unreachable for any real vacancy regardless of ATS grade or screening
    decision. A realistic strong employer score (7.5, within the observed
    3-9.75 live range) combined with an A-grade AUTO_APPLY screen must now
    reach PRIORITY_APPLY."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        ELIGIBLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="A", employer_score=7.5,
    ))
    assert intelligence.opportunity_value.value == "HIGH"
    assert intelligence.priority == Priority.PRIORITY_APPLY


def test_realistic_employer_score_still_overridden_by_hard_ineligibility():
    """The fix only changes opportunity_value's scale -- it must not weaken
    the precedence rules. A strong, realistic (now-HIGH) opportunity value
    still cannot rescue an INELIGIBLE vacancy."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        INELIGIBLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.5,
    ))
    assert intelligence.opportunity_value.value == "HIGH"
    assert intelligence.priority == Priority.REJECT


def test_no_production_mutation_from_normalized_opportunity_value_evaluation():
    """This fix is pure in-memory scoring -- confirmed the same way Task
    21.14E's equivalent regression is: the real tracker db file is
    byte-identical before and after a JobIntelligenceService.evaluate() call
    that exercises the new normalization path."""
    import hashlib

    real_history_db = "app/data/application_history.db"
    before = hashlib.sha256(open(real_history_db, "rb").read()).hexdigest()

    JobIntelligenceService().evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=7.5))

    after = hashlib.sha256(open(real_history_db, "rb").read()).hexdigest()
    assert before == after


# --- Dimensions stay distinct ------------------------------------------------

def test_dimensions_stay_distinct_not_merged_into_a_composite():
    """Each dimension reflects its own source signal -- changing one (e.g.
    the employer score) must never silently move a different, unrelated
    dimension (e.g. candidate competitiveness)."""
    service = JobIntelligenceService()
    low_employer = service.evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=1.0, career_score=79.0))
    high_employer = service.evaluate(_evaluation(ELIGIBLE_RESULT, employer_score=9.9, career_score=79.0))

    assert low_employer.opportunity_value.value == "LOW"
    assert high_employer.opportunity_value.value == "HIGH"
    # candidate_competitiveness is driven by career_score/requirement
    # evidence, not employer score -- unaffected by the opportunity_value swing.
    assert low_employer.candidate_competitiveness.value == high_employer.candidate_competitiveness.value == "VERY_STRONG"
    # Distinct sources recorded per dimension -- proves no dimension is
    # silently derived from another's engine output.
    assert "employer" in low_employer.opportunity_value.source.lower()
    assert "career" in low_employer.candidate_competitiveness.source.lower()
    assert "ats" in low_employer.application_alignment.source.lower()


def test_application_alignment_reuses_ats_score_not_a_new_number():
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(ELIGIBLE_RESULT))
    assert intelligence.application_alignment.value == 71.2


def test_evaluate_reuses_precomputed_hard_eligibility_without_reclassifying():
    """When evaluation.hard_eligibility is already set (as ApplicationService
    always sets it), JobIntelligenceService must reuse it rather than
    running the classifier again from scratch."""
    service = JobIntelligenceService()
    evaluation = _evaluation(INELIGIBLE_RESULT)
    intelligence = service.evaluate(evaluation)
    assert intelligence.hard_eligibility.reasons == (INELIGIBLE_RESULT.reason,)
    assert intelligence.evidence == (INELIGIBLE_RESULT.evidence,)


# --- Task 21.14D: requirement-level evidence assessment ---------------------

def _profile(**overrides):
    base = {
        "experience": {"years": 15},
        "skills": {
            "accounting": ["Financial Reporting", "Management Accounting"],
            "taxation": ["Australian Tax", "BAS", "IAS"],
        },
        "employment_history": [
            {
                "company": "Test Employer Pty Ltd", "position": "Senior Accountant",
                "responsibilities": ["Management Accounting", "Financial Reporting"],
                "technologies": ["Xero", "MYOB"],
            },
        ],
    }
    base.update(overrides)
    return base


def _evaluation_with_profile(hard_eligibility, job_analysis, profile=None, **kwargs):
    evaluation = _evaluation(hard_eligibility, job_analysis=job_analysis, **kwargs)
    evaluation.profile = profile if profile is not None else _profile()
    return evaluation


def test_strong_verified_evidence():
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "required_skills": ["Xero"]}
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    xero = next(r for r in intelligence.requirement_evidence if r.requirement == "Xero")
    assert xero.classification == "STRONG_EVIDENCE"
    assert xero.supporting_evidence
    assert xero.reason


def test_partial_evidence():
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "required_skills": ["Xero Practice Manager"]}
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    item = intelligence.requirement_evidence[0]
    assert item.classification == "PARTIAL_EVIDENCE"
    assert "xero" in item.supporting_evidence


def test_no_evidence():
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "required_skills": ["SAP FICO Consultant"]}
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    item = intelligence.requirement_evidence[0]
    assert item.classification == "NO_EVIDENCE"
    assert item.supporting_evidence == ()


def test_proven_hard_requirement_gap_from_years_shortfall():
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "experience_required": 25}
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    gap = next(r for r in intelligence.requirement_evidence if r.classification == "HARD_REQUIREMENT_GAP")
    assert "25" in gap.requirement
    assert "15" in gap.reason  # candidate's verified years
    assert intelligence.priority == Priority.REJECT
    assert intelligence.candidate_competitiveness.value == "LOW"


def test_years_requirement_met_is_strong_evidence_not_a_gap():
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "experience_required": 10}
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    item = next(r for r in intelligence.requirement_evidence if "years" in r.requirement)
    assert item.classification == "STRONG_EVIDENCE"


def test_uncertain_mandatory_requirement_routes_to_human_review():
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["SAP FICO Consultant"],  # mandatory, factual, unmatched
    }
    intelligence = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.5,
    ))
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert any("SAP" in reason for reason in intelligence.priority_reasons)


def test_behavioural_requirement_is_never_fabricated_into_a_hard_gap_or_forced_review():
    """A behavioural/soft-skill requirement with no matching evidence must
    be reported honestly (NO_EVIDENCE, is_behavioural=True) but never
    escalated to HARD_REQUIREMENT_GAP, and must not by itself force
    HUMAN_REVIEW the way an uncertain factual requirement would."""
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["Excellent attention to detail"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis, screening_decision="AUTO_APPLY"))
    item = intelligence.requirement_evidence[0]
    assert item.is_behavioural is True
    assert item.classification != "HARD_REQUIREMENT_GAP"
    assert "behavioural" in item.reason.lower()
    # No hard gap and no uncertain-mandatory-factual signal -- this
    # behavioural item alone must not force HUMAN_REVIEW.
    assert intelligence.priority != Priority.HUMAN_REVIEW


def test_employer_scoped_evidence_isolation_for_requirement_assessment():
    """Requirement evidence must come only from employers actually present
    in the profile -- never contaminated by a different employer's facts
    (mirrors the Task 21.12 employer-isolation guarantee, applied here)."""
    service = JobIntelligenceService()
    profile = _profile(employment_history=[
        {"company": "Australian Accounting Firm", "position": "Offshore Accounting Manager",
         "responsibilities": ["Management Accounting"], "technologies": ["Xero"]},
    ])
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["SMSF", "G.S.N. Associates"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis, profile=profile))
    by_requirement = {r.requirement: r for r in intelligence.requirement_evidence}
    # SMSF is genuinely part of the real evidence library's Trident-aliased
    # entry, which this profile's "Australian Accounting Firm" entry matches.
    assert by_requirement["SMSF"].classification in ("STRONG_EVIDENCE", "PARTIAL_EVIDENCE")
    # "G.S.N. Associates" belongs only to the GSN employer record, which
    # this profile does not include at all -- must not leak in as evidence.
    assert by_requirement["G.S.N. Associates"].classification == "NO_EVIDENCE"


# --- Task 21.14D: candidate competitiveness bands ---------------------------

def test_strong_candidate_from_high_career_fit_and_medium_evidence_coverage():
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["Xero", "SAP FICO Consultant"],  # 1/2 covered -> MEDIUM coverage
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis, career_score=85.0))
    assert intelligence.candidate_competitiveness.value == "STRONG"


def test_stretch_candidate_from_medium_career_fit_and_low_evidence_coverage():
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["SAP FICO Consultant", "Workday", "Oracle Fusion"],  # 0/3 covered -> LOW coverage
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis, career_score=60.0))
    assert intelligence.candidate_competitiveness.value == "STRETCH"
    assert intelligence.priority == Priority.HUMAN_REVIEW


def test_low_candidate_from_low_career_fit_routes_to_watch():
    """No mandatory factual requirement is included here so this isolates
    tier 3 (LOW competitiveness -> WATCH) from the separate tier 2 uncertain-
    mandatory-requirement rule exercised elsewhere."""
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners"}
    intelligence = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, career_score=30.0, screening_decision="SKIP",
    ))
    assert intelligence.candidate_competitiveness.value == "LOW"
    assert intelligence.priority == Priority.WATCH


def test_hard_gap_cannot_be_rescued_by_strong_ats_grade():
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "experience_required": 30}
    intelligence = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.9,
    ))
    assert intelligence.priority == Priority.REJECT


def test_requirement_evidence_always_has_reasons_and_provenance_when_matched():
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["Xero"], "preferred_skills": ["Excellent attention to detail"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    for item in intelligence.requirement_evidence:
        assert item.reason
        if item.classification in ("STRONG_EVIDENCE", "PARTIAL_EVIDENCE"):
            assert item.supporting_evidence


def test_no_production_mutation_from_requirement_evidence_assessment(tmp_path, monkeypatch):
    """The evidence-library lookup this dimension performs is read-only --
    confirmed by hashing the real library file before and after."""
    import hashlib

    library_path = "app/data/candidate_evidence_library.json"
    before = hashlib.sha256(open(library_path, "rb").read()).hexdigest()

    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "required_skills": ["Xero"]}
    service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))

    after = hashlib.sha256(open(library_path, "rb").read()).hexdigest()
    assert before == after


# --- Task 21.15B: requirement-classifier calibration -------------------------
# Real-market validation (Task 21.15) found generic soft/interpersonal
# wording ("analytical skills", "cross-functional collaboration",
# "prioritization") was being treated as a factual mandatory requirement,
# inflating HUMAN_REVIEW volume for reasons unrelated to actual candidate
# fit. These prove the recalibrated _is_behavioural() (literal markers +
# word-root stems + sentence-opening prefixes) without weakening genuine
# factual-requirement/HARD_REQUIREMENT_GAP handling.

def test_explicit_mandatory_qualification_is_factual_not_behavioural():
    assert _is_behavioural("CA ANZ (or equivalent) qualification") is False
    assert _is_behavioural("CPA qualification") is False


def test_explicit_mandatory_license_is_factual_not_behavioural():
    assert _is_behavioural("Current Real Estate License") is False
    assert _is_behavioural("Valid driver's license") is False


def test_explicit_mandatory_experience_requirement_is_factual_not_behavioural():
    assert _is_behavioural("5+ years of relevant experience") is False
    assert _is_behavioural("Supervisory experience") is False


def test_explicit_mandatory_software_requirement_is_factual_not_behavioural():
    assert _is_behavioural("SAP FICO Consultant") is False
    assert _is_behavioural("NetSuite") is False
    assert _is_behavioural("Strong MS Office proficiency") is False


def test_communication_is_behavioural():
    assert _is_behavioural("Excellent communication skills") is True


def test_analytical_skills_is_behavioural():
    assert _is_behavioural("Strong analytical skills") is True
    assert _is_behavioural("Analytical skills") is True


def test_cross_functional_collaboration_is_behavioural():
    assert _is_behavioural("cross-functional collaboration") is True


def test_prioritization_is_behavioural():
    assert _is_behavioural("Prioritization") is True
    assert _is_behavioural("Prioritisation") is True


def test_attention_to_detail_is_behavioural():
    assert _is_behavioural("Excellent attention to detail") is True


def test_problem_solving_is_behavioural():
    assert _is_behavioural("Strong problem solving skills") is True


def test_behavioural_stem_generalizes_beyond_the_exact_listed_phrase():
    """The word-root stems generalize across grammatical variants that were
    never individually enumerated -- the fix Task 21.15B calls for instead
    of an ever-growing exact-phrase dictionary."""
    assert _is_behavioural("manage multiple priorities") is True  # "priorit" stem, not "prioritization"
    assert _is_behavioural("influencing stakeholders and regulators") is True  # "stakeholder" stem
    assert _is_behavioural("stakeholder management skills") is True


def test_ability_to_prefix_is_behavioural():
    assert _is_behavioural("Ability to work independently") is True
    assert _is_behavioural(
        "Ability to simultaneously handle diverse and pressing assignments and "
        "sensitive and adversarial situations"
    ) is True


def test_willingness_to_learn_style_disposition_is_still_behavioural():
    assert _is_behavioural("Willingness to learn") is True
    assert _is_behavioural("Eager to learn") is True
    assert _is_behavioural("Willingness to embrace change") is True
    assert _is_behavioural("Willing to take on new challenges") is True


def test_willing_to_relocate_or_travel_is_a_factual_constraint_not_behavioural():
    """A follow-up correction: 'willing(ness) to ...' is ambiguous -- the
    same framing covers a genuine disposition (behavioural, above) and an
    objectively verifiable candidate constraint (relocation, travel,
    licensing/certification, work location/schedule) that must still be
    able to trigger the uncertain-critical-requirement gate."""
    assert _is_behavioural("Willing to relocate") is False
    assert _is_behavioural("Willingness to travel") is False


def test_willing_to_obtain_license_or_certification_is_factual_not_behavioural():
    assert _is_behavioural("Willing to obtain a security clearance") is False
    assert _is_behavioural("Willing to complete ARITA course") is False  # real Task 21.15 example
    assert _is_behavioural("Willing to undergo a background check") is False


def test_willing_to_work_location_or_schedule_is_factual_not_behavioural():
    assert _is_behavioural("Willing to work weekends on-site") is False
    assert _is_behavioural("Willing to work on a rotating roster") is False


def test_behavioural_no_evidence_does_not_force_human_review_new_terms():
    """End-to-end: a mandatory requirement using the newly-recognized
    behavioural phrasing must not, by itself, force HUMAN_REVIEW -- the
    exact real-world failure Task 21.15 found."""
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["Strong analytical skills", "cross-functional collaboration", "Prioritization"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis, screening_decision="AUTO_APPLY"))
    for item in intelligence.requirement_evidence:
        assert item.is_behavioural is True
        assert item.classification != "HARD_REQUIREMENT_GAP"
    assert intelligence.priority != Priority.HUMAN_REVIEW
    assert not any("critical factual requirement" in reason for reason in intelligence.priority_reasons)


def test_unverified_relocation_constraint_still_forces_human_review_end_to_end():
    """End-to-end companion to the _is_behavioural unit tests above: an
    unverified 'willing to relocate' constraint must still route to
    HUMAN_REVIEW -- proving the willing/willingness correction didn't
    silently exempt real relocation/travel/licensing constraints from the
    gate it's meant to protect."""
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["Willing to relocate to Singapore"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    item = intelligence.requirement_evidence[0]
    assert item.is_behavioural is False
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert any("relocate" in reason.lower() for reason in intelligence.priority_reasons)


def test_uncertain_critical_factual_requirement_still_forces_human_review():
    """The calibration must not over-correct -- a genuinely factual,
    mandatory, unverified requirement (a specific named software/system)
    still routes to HUMAN_REVIEW."""
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["SAP FICO Consultant"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert any("SAP" in reason for reason in intelligence.priority_reasons)


def test_proven_hard_requirement_gap_still_rejects_after_calibration():
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "experience_required": 30}
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    assert intelligence.priority == Priority.REJECT
    gap = next(r for r in intelligence.requirement_evidence if r.classification == "HARD_REQUIREMENT_GAP")
    assert gap.is_behavioural is False


def test_ats_a_plus_cannot_rescue_a_real_hard_gap_after_calibration():
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "experience_required": 30}
    intelligence = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.9,
    ))
    assert intelligence.priority == Priority.REJECT


def test_no_fabricated_evidence_for_a_reclassified_behavioural_requirement():
    """Reclassifying a requirement as behavioural must not fabricate
    evidence for it -- it stays NO_EVIDENCE with an honest reason, just no
    longer forces HUMAN_REVIEW by itself."""
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["cross-functional collaboration"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    item = intelligence.requirement_evidence[0]
    assert item.classification == "NO_EVIDENCE"
    assert item.supporting_evidence == ()
    assert "behavioural" in item.reason.lower()


def test_deterministic_replay_produces_identical_results():
    """Task 21.15B section 4: the same frozen evaluation input must produce
    byte-identical JobIntelligence output across repeated evaluate() calls
    -- required for the frozen-benchmark replay to be trustworthy."""
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["Xero", "cross-functional collaboration", "CPA"],
    }
    first = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    second = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    assert first == second


# --- Task 21.15C: factual-requirement criticality ---------------------------
# Real-market validation found EVERY factual item placed in required_skills
# was treated as equally critical -- a named tool got the same
# uncertain-mandatory HUMAN_REVIEW treatment as an explicitly required
# professional licence. These prove the criticality distinction: CRITICAL
# (uncertainty still forces HUMAN_REVIEW) vs NON_CRITICAL (missing evidence
# only affects Candidate Competitiveness) vs AMBIGUOUS_CRITICALITY (the
# requirement's own wording can't be located in job_description at all --
# handled conservatively, same as CRITICAL).

_CPA_REQUIRED_DESCRIPTION = "Finance role. Candidates must hold a CPA to be considered for this role."
_MUST_HOLD_CA_CPA_DESCRIPTION = "Finance role. You must hold CA/CPA qualification for this position."
_MIN_EXPERIENCE_TEXT_DESCRIPTION = "Finance role.\n- Minimum of 5+ years relevant experience required.\n- Other duties as assigned."
_WORK_AUTH_DESCRIPTION = "Finance role.\n- Must have valid work authorization for this role.\n- Other duties as assigned."
_LICENCE_REQUIRED_DESCRIPTION = "Finance role.\n- A valid driver's licence is required for this position.\n- Other duties as assigned."
_NETSUITE_PREFERRED_DESCRIPTION = "Finance role.\n- Experience with NetSuite is preferred but not required.\n- Other duties as assigned."
_ORDINARY_TOOL_DESCRIPTION = "Finance role.\n- Experience with NetSuite and other ERP systems.\n- Other duties as assigned."


def test_cpa_required_is_critical():
    assert _requirement_criticality("CPA", _CPA_REQUIRED_DESCRIPTION) == "CRITICAL"


def test_must_hold_ca_cpa_is_critical():
    assert _requirement_criticality("CA/CPA", _MUST_HOLD_CA_CPA_DESCRIPTION) == "CRITICAL"


def test_explicit_minimum_experience_text_is_critical():
    assert _requirement_criticality("5+ years relevant experience", _MIN_EXPERIENCE_TEXT_DESCRIPTION) == "CRITICAL"


def test_years_requirement_structured_field_is_always_critical():
    """The structured experience_required check (_years_requirement) is a
    direct numeric comparison, not a text-search guess -- always CRITICAL."""
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "experience_required": 30}
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    gap = next(r for r in intelligence.requirement_evidence if r.classification == "HARD_REQUIREMENT_GAP")
    assert gap.criticality == "CRITICAL"


def test_work_authorization_required_is_critical():
    assert _requirement_criticality("work authorization", _WORK_AUTH_DESCRIPTION) == "CRITICAL"


def test_licence_required_is_critical():
    assert _requirement_criticality("driver's licence", _LICENCE_REQUIRED_DESCRIPTION) == "CRITICAL"


def test_netsuite_preferred_is_non_critical():
    assert _requirement_criticality("NetSuite", _NETSUITE_PREFERRED_DESCRIPTION) == "NON_CRITICAL"


def test_ordinary_tool_experience_without_mandatory_language_is_non_critical():
    """A named tool mentioned with no explicit mandatory/critical language
    nearby does not automatically become critical merely because the
    upstream extractor placed it in required_skills."""
    assert _requirement_criticality("NetSuite", _ORDINARY_TOOL_DESCRIPTION) == "NON_CRITICAL"


def test_requirement_wording_not_locatable_is_ambiguous_and_handled_conservatively():
    """An extraction-fragment requirement that doesn't recognizably
    correspond to any part of the actual vacancy text (e.g. "related field"
    split out of a longer degree-subject list) must not be silently treated
    as evidenced or non-critical -- it stays AMBIGUOUS_CRITICALITY, which
    the priority gate treats the same as CRITICAL."""
    assert _requirement_criticality("related field", "Finance role with no matching wording at all.") == "AMBIGUOUS_CRITICALITY"


def test_critical_no_evidence_forces_human_review():
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["CPA"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, job_description=_CPA_REQUIRED_DESCRIPTION,
    ))
    item = next(r for r in intelligence.requirement_evidence if r.requirement == "CPA")
    assert item.criticality == "CRITICAL"
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert any("critical factual requirement" in reason for reason in intelligence.priority_reasons)


def test_non_critical_no_evidence_does_not_itself_force_human_review():
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["NetSuite"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, job_description=_ORDINARY_TOOL_DESCRIPTION, screening_decision="AUTO_APPLY",
    ))
    item = next(r for r in intelligence.requirement_evidence if r.requirement == "NetSuite")
    assert item.criticality == "NON_CRITICAL"
    assert item.classification == "NO_EVIDENCE"
    assert intelligence.priority != Priority.HUMAN_REVIEW
    assert not any("critical factual requirement" in reason for reason in intelligence.priority_reasons)


def test_non_critical_gap_still_reduces_evidence_coverage_and_competitiveness():
    """A non-critical uncertain requirement doesn't force HUMAN_REVIEW, but
    it must still count against Candidate Competitiveness's evidence-
    coverage ratio -- missing evidence is never simply erased."""
    service = JobIntelligenceService()
    description = (
        "Finance role.\n- Experience with NetSuite and other ERP systems.\n"
        "- Experience with Workday.\n- Experience with Oracle Fusion.\n- Other duties as assigned."
    )
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["NetSuite", "Workday", "Oracle Fusion"],  # 0/3 covered, all non-critical
    }
    intelligence = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, job_description=description, career_score=60.0,
    ))
    for item in intelligence.requirement_evidence:
        assert item.criticality == "NON_CRITICAL"
    assert intelligence.candidate_competitiveness.value == "STRETCH"


def test_hard_requirement_gap_still_rejects_with_criticality_present():
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Accountant", "company": "Acme Partners", "experience_required": 30}
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis))
    assert intelligence.priority == Priority.REJECT


def test_ats_a_plus_cannot_rescue_critical_uncertainty():
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["CPA"],
    }
    intelligence = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, job_description=_CPA_REQUIRED_DESCRIPTION,
        screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.9,
    ))
    assert intelligence.priority == Priority.HUMAN_REVIEW


def test_credential_requirement_defaults_critical_without_explicit_marker_language():
    """Real vacancies routinely list a professional qualification under a
    heading like "Skills and Attributes for Success" without repeating
    "required" next to it -- caught by Task 21.15C's own manual audit. A
    credential-type requirement defaults CRITICAL even with no explicit
    mandatory-language marker nearby."""
    description = "Finance role.\nSkills And Attributes For Success\n- CA ANZ (or equivalent) qualification.\n- Experience in forensic accounting."
    assert _requirement_criticality("CA ANZ (or equivalent) qualification", description) == "CRITICAL"


def test_credential_requirement_downgraded_by_explicit_softening_language():
    description = "Finance role.\n- MBA preferred but not required.\n- Other duties as assigned."
    assert _requirement_criticality("MBA", description) == "NON_CRITICAL"


def test_softening_language_does_not_leak_across_bullet_boundaries():
    """A regression the manual audit caught directly: a neighbouring
    bullet's softening word ("Languages preferred") must not leak into an
    adjacent bullet's own explicit "essential" framing."""
    description = "Finance role.\n- Languages preferred;\n- MS Office proficiency essential."
    assert _requirement_criticality("MS Office proficiency", description) == "CRITICAL"


def test_deterministic_replay_with_criticality_remains_identical_across_runs():
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["CPA", "NetSuite"],
    }
    first = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, job_description=_CPA_REQUIRED_DESCRIPTION,
    ))
    second = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, job_description=_CPA_REQUIRED_DESCRIPTION,
    ))
    assert first == second


# --- Legitimate intermediary / anonymous employer (Task 21.24B) -------------
# A vacancy must not be rejected/deferred merely because the underlying
# employer is anonymous when a legitimate, identifiable intermediary
# explicitly discloses it is recruiting on behalf of a client/partner
# (Jobgether-style partner-company vacancies). Employer anonymity alone must
# never override eligibility, requirement evidence, or application-route
# checks -- those remain fully independent gates.

_INTERMEDIARY_DESCRIPTION = (
    "This position is listed on behalf of a partner company, who manages all applications "
    "and next steps. Our partner is looking for a Head of Finance based in Australia to lead "
    "management reporting, budgeting, settlements and financial process optimization."
)
_INTERMEDIARY_STRONG_DESCRIPTION = _INTERMEDIARY_DESCRIPTION + (
    " Responsibilities include monthly close, statutory reporting, tax compliance, budgeting, "
    "forecasting and liaison with the finance leadership team across multiple entities and markets."
)


def test_legitimate_intermediary_with_anonymous_employer_is_not_uncertain_from_missing_company():
    """(1) A named, identifiable intermediary that affirmatively discloses it
    recruits on behalf of an anonymous client must not be UNCERTAIN merely
    because job_analysis has no company field."""
    service = JobIntelligenceService()
    opportunity = _opportunity(company="Jobgether", application_route_status="SOURCE_ONLY")
    intelligence = service.evaluate(
        _evaluation(ELIGIBLE_RESULT, job_analysis={"job_title": "Head of Finance"}, job_description=_INTERMEDIARY_DESCRIPTION),
        opportunity=opportunity,
    )
    assert intelligence.vacancy_validity.value in ("LIKELY_VALID", "VERIFIED")
    assert any("LEGITIMATE_INTERMEDIARY_EMPLOYER_ANONYMOUS" in reason for reason in intelligence.vacancy_validity.reasons)
    assert "vacancy analysis is missing: company" not in intelligence.vacancy_validity.reasons


def test_missing_employer_without_on_behalf_of_disclosure_remains_uncertain():
    """(2) A named poster alone -- with no affirmative "on behalf of a
    client/partner" disclosure in the JD -- must not be trusted as a
    legitimate intermediary. Employer anonymity here is genuinely unresolved,
    not a disclosed intermediary relationship."""
    service = JobIntelligenceService()
    opportunity = _opportunity(company="Some Job Board")
    intelligence = service.evaluate(
        _evaluation(ELIGIBLE_RESULT, job_analysis={"job_title": "Head of Finance"}, job_description=USABLE_DESCRIPTION),
        opportunity=opportunity,
    )
    assert intelligence.vacancy_validity.value == "UNCERTAIN"
    assert intelligence.priority == Priority.HUMAN_REVIEW


def test_suspicious_unidentifiable_poster_stays_fail_closed():
    """(3) No discovery opportunity at all (so no identifiable intermediary
    name whatsoever) must remain UNCERTAIN -- the pre-existing, unresolved-
    employer behavior is fully preserved for genuinely unknown/suspicious
    postings."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(
        _evaluation(ELIGIBLE_RESULT, job_analysis={"job_title": "Head of Finance"}, job_description=_INTERMEDIARY_DESCRIPTION),
    )
    assert intelligence.vacancy_validity.value == "UNCERTAIN"


def test_intermediary_anonymous_employer_does_not_bypass_hard_ineligibility():
    """(4) Intermediary/anonymous-employer status must never rescue a hard
    INELIGIBLE result -- eligibility stays fully independent."""
    service = JobIntelligenceService()
    opportunity = _opportunity(company="Jobgether", application_route_status="SOURCE_ONLY")
    intelligence = service.evaluate(
        _evaluation(INELIGIBLE_RESULT, job_analysis={"job_title": "Head of Finance"}, job_description=_INTERMEDIARY_DESCRIPTION),
        opportunity=opportunity,
    )
    assert intelligence.priority == Priority.REJECT
    assert intelligence.vacancy_validity.value != "UNCERTAIN"  # validity itself is fine
    assert intelligence.hard_eligibility.value == INELIGIBLE  # but eligibility still rejects


def test_intermediary_anonymous_employer_does_not_bypass_hard_requirement_gap():
    """(5) Intermediary/anonymous-employer status must never rescue a proven
    HARD_REQUIREMENT_GAP (e.g. a mandatory years-of-experience shortfall) --
    requirement evidence stays fully independent."""
    service = JobIntelligenceService()
    opportunity = _opportunity(company="Jobgether", application_route_status="SOURCE_ONLY")
    job_analysis = {"job_title": "Head of Finance", "experience_required": 25}
    intelligence = service.evaluate(
        _evaluation_with_profile(ELIGIBLE_RESULT, job_analysis, job_description=_INTERMEDIARY_DESCRIPTION),
        opportunity=opportunity,
    )
    gap = next(r for r in intelligence.requirement_evidence if r.classification == "HARD_REQUIREMENT_GAP")
    assert "25" in gap.requirement
    assert intelligence.priority == Priority.REJECT


def test_intermediary_application_route_is_accepted_as_a_valid_route():
    """(6) When the intermediary's own listing carries a resolved,
    high-confidence application route and a substantive description, the
    vacancy can reach VERIFIED -- the system does not require a direct
    employer ATS when the intermediary is the authorized channel."""
    service = JobIntelligenceService()
    opportunity = _opportunity(
        company="Jobgether", application_route_status="RESOLVED", application_route_confidence="HIGH",
    )
    intelligence = service.evaluate(
        _evaluation(ELIGIBLE_RESULT, job_analysis={"job_title": "Head of Finance"}, job_description=_INTERMEDIARY_STRONG_DESCRIPTION),
        opportunity=opportunity,
    )
    assert intelligence.vacancy_validity.value == "VERIFIED"
    assert any("Jobgether" in reason for reason in intelligence.vacancy_validity.reasons)


def test_jobgether_style_scenario_is_not_blocked_solely_by_employer_anonymity():
    """(7) Task 21.24B regression scenario, reproducing Tracker 61's actual
    persisted shape: Jobgether intermediary, "on behalf of a partner
    company" disclosure, missing job_analysis.company, SOURCE_ONLY/LOW route,
    and MANUAL_REVIEW hard eligibility (international eligibility unresolved
    -- unrelated to employer anonymity). Employer anonymity alone must not be
    a blocker; geographic eligibility remains the one real blocker."""
    service = JobIntelligenceService()
    opportunity = _opportunity(
        company="Jobgether", application_route_status="SOURCE_ONLY", application_route_confidence="LOW",
        job_url="https://au.linkedin.com/jobs/view/head-of-finance-at-jobgether-4457989411",
    )
    intelligence = service.evaluate(
        _evaluation(
            MANUAL_REVIEW_RESULT, job_analysis={"job_title": "Head of Finance"},
            job_description=_INTERMEDIARY_STRONG_DESCRIPTION, career_score=79.6,
        ),
        opportunity=opportunity,
    )
    assert intelligence.vacancy_validity.value != "UNCERTAIN"
    assert not any("missing: company" in reason for reason in intelligence.vacancy_validity.reasons)
    # The vacancy itself is no longer the blocker; unresolved geographic
    # eligibility is now the *only* thing routing this to HUMAN_REVIEW.
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert intelligence.hard_eligibility.value == MANUAL_REVIEW
    assert any("eligibility" in reason.lower() for reason in intelligence.priority_reasons)


# --- Prepare-for-human-review package gate (Task 21.24C) --------------------
# A narrow, additive distinction -- separate from `priority`, which always
# stays HUMAN_REVIEW (C) here -- for whether *internal* application-package
# preparation may proceed for a strong C opportunity whose only remaining
# blocker is human-resolvable uncertainty. Never converts C to B.

def test_qualifying_strong_c_gets_prepare_for_human_review():
    """(1)/(2) A strong C -- LIKELY_VALID vacancy, HIGH/MEDIUM opportunity
    value, VERY_STRONG/STRONG competitiveness, no requirement gap, only
    unresolved hard eligibility -- qualifies for PREPARE_FOR_HUMAN_REVIEW,
    and priority itself remains exactly "C"."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(
        _evaluation(MANUAL_REVIEW_RESULT, job_analysis={"job_title": "Head of Finance", "company": "Acme Partners"}),
    )
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert intelligence.priority.value == "C"
    assert intelligence.package_gate == "PREPARE_FOR_HUMAN_REVIEW"
    assert intelligence.package_gate_reasons
    assert any("eligibility" in reason.lower() for reason in intelligence.package_gate_reasons)


def test_hard_ineligible_c_cannot_prepare():
    """(3) Hard INELIGIBLE never even reaches C (it's REJECT, tier 1) --
    package_gate must be empty regardless."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(
        _evaluation(INELIGIBLE_RESULT, job_analysis={"job_title": "Head of Finance", "company": "Acme Partners"}),
    )
    assert intelligence.priority == Priority.REJECT
    assert intelligence.package_gate == ""


def test_hard_requirement_gap_cannot_prepare():
    """(4) A proven HARD_REQUIREMENT_GAP forces REJECT (tier 1) -- never
    HUMAN_REVIEW, so package_gate must be empty."""
    service = JobIntelligenceService()
    job_analysis = {"job_title": "Head of Finance", "company": "Acme Partners", "experience_required": 25}
    intelligence = service.evaluate(_evaluation_with_profile(MANUAL_REVIEW_RESULT, job_analysis))
    assert any(r.classification == "HARD_REQUIREMENT_GAP" for r in intelligence.requirement_evidence)
    assert intelligence.priority == Priority.REJECT
    assert intelligence.package_gate == ""


def test_weak_stretch_competitiveness_c_cannot_prepare():
    """(5) A C driven by STRETCH candidate competitiveness (weak/low-value
    fit), not by unresolved eligibility, must not qualify -- competitiveness
    must be STRONG or VERY_STRONG."""
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["SAP FICO Consultant", "Workday", "Oracle Fusion"],  # 0/3 covered -> LOW coverage
    }
    intelligence = service.evaluate(_evaluation_with_profile(ELIGIBLE_RESULT, job_analysis, career_score=60.0))
    assert intelligence.candidate_competitiveness.value == "STRETCH"
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert intelligence.package_gate == ""


def test_uncertain_critical_requirement_c_cannot_prepare():
    """(5)/credential variant: a C driven by an uncertain CRITICAL mandatory
    requirement (a genuine, unresolved skill/credential question) is NOT the
    "human-resolvable" uncertainty this rule is for -- must not qualify,
    read from structured requirement_evidence, never string-matched."""
    service = JobIntelligenceService()
    job_analysis = {
        "job_title": "Accountant", "company": "Acme Partners",
        "required_skills": ["SAP FICO Consultant"],  # mandatory, factual, unmatched, CRITICAL
    }
    intelligence = service.evaluate(_evaluation_with_profile(
        ELIGIBLE_RESULT, job_analysis, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.5,
    ))
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert intelligence.package_gate == ""


def test_uncertain_suspicious_vacancy_cannot_prepare():
    """(6) A C driven by UNCERTAIN vacancy validity (no legitimate-
    intermediary evidence at all -- a genuinely suspicious/unverifiable
    posting) must not qualify -- validity must be VERIFIED or LIKELY_VALID."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(
        _evaluation(ELIGIBLE_RESULT, job_analysis={"job_title": "Accountant"}),  # missing company, no intermediary
    )
    assert intelligence.vacancy_validity.value == "UNCERTAIN"
    assert intelligence.priority == Priority.HUMAN_REVIEW
    assert intelligence.package_gate == ""


def test_a_and_b_never_carry_package_gate():
    """(11) A/B opportunities never populate package_gate -- it is purely a
    C-priority concept and irrelevant to the existing A/B path."""
    service = JobIntelligenceService()
    intelligence = service.evaluate(_evaluation(
        ELIGIBLE_RESULT, screening_decision="AUTO_APPLY", ats_grade="A+", employer_score=9.5,
    ))
    assert intelligence.priority in (Priority.PRIORITY_APPLY, Priority.APPLY)
    assert intelligence.package_gate == ""


def test_d_and_e_never_qualify_for_package_gate():
    """(12) WATCH (D) and REJECT (E) never populate package_gate."""
    service = JobIntelligenceService()
    watch = service.evaluate(_evaluation(ELIGIBLE_RESULT, screening_decision="SKIP"))
    assert watch.priority == Priority.WATCH
    assert watch.package_gate == ""
    reject = service.evaluate(_evaluation(INELIGIBLE_RESULT))
    assert reject.priority == Priority.REJECT
    assert reject.package_gate == ""
