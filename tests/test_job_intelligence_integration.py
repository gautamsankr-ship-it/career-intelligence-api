"""Task 21.14E: JobIntelligenceService.priority is now the single
authoritative gate for CareerAgent's application-preparation pipeline --
proven here end-to-end through the real CareerAgent.process_jobs() control
flow for all five priorities, and that no positive signal (ATS A+, high
career score) can bypass a REJECT/HUMAN_REVIEW/WATCH outcome.

Fully hermetic: discovery/queue/email-classifier are faked; application_service
is a thin fake returning a hand-built evaluation shaped exactly like the real
ApplicationService.evaluate_job()'s JobEvaluation, so JobIntelligenceService
computes a REAL priority from REAL inputs (not hardcoded results). Only the
tracker is real, backed by an isolated sqlite file under tmp_path -- never
the production app/data/application_history.db. No Gmail, no OpenAI call,
no browser/submission code reachable at all.
"""

from types import SimpleNamespace

from app.models.career_opportunity import CareerOpportunity
from app.services.application_email_classifier import EmailClassification
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity
from app.services.career_agent import CareerAgent
from app.services.job_intelligence_service import JobIntelligenceService
from app.services.remote_work_eligibility import ELIGIBLE, INELIGIBLE, RemoteEligibilityResult

USABLE_DESCRIPTION = (
    "Please send your CV to jobs@acme.test. We are looking for an experienced "
    "accountant to join our finance team covering reporting and compliance work."
)


def _opportunity(job_id: str, **overrides) -> CareerOpportunity:
    base = dict(
        id=job_id, source="test", company="Acme Partners", job_title="Accountant",
        job_description=USABLE_DESCRIPTION, work_arrangement="REMOTE", remote_status=True,
        job_url=f"https://example.test/jobs/{job_id}",
    )
    base.update(overrides)
    return CareerOpportunity(**base)


class FakeDiscovery:
    def __init__(self, opportunities):
        self._opportunities = opportunities

    def discover_jobs(self):
        return self._opportunities


class FakeQueue:
    def add_application(self, opportunity):
        return SimpleNamespace(status="QUEUED")


class FakeEmailClassifier:
    def classify_opportunity(self, opportunity, job_analysis):
        return SimpleNamespace(classification=EmailClassification.EXPLICIT_APPLICATION_EMAIL, selected_email="jobs@acme.test")


class FakeApplicationService:
    """evaluate_job returns a fully hand-built evaluation shaped exactly
    like the real ApplicationService.evaluate_job()'s JobEvaluation, so
    JobIntelligenceService computes a REAL priority from REAL inputs.
    generate_application_documents raises if `raise_on_generate` -- the
    established FakeApp pattern for proving a priority never reaches
    document preparation."""

    def __init__(self, evaluation_overrides: dict | None = None, raise_on_generate: bool = False):
        self.evaluation_overrides = evaluation_overrides or {}
        self.raise_on_generate = raise_on_generate
        self.docs = 0

    def evaluate_job(self, job_description, opportunity=None):
        defaults = dict(
            profile={"candidate": {"full_name": "Candidate"}, "experience": {"years": 15}},
            job_analysis={"company": "Acme Partners", "job_title": "Accountant"},
            job_description=job_description,
            employer=SimpleNamespace(overall_score=70.0),
            career_decision=SimpleNamespace(overall_score=85.0, confidence=0.9, priority="HIGH", automation_level="FULL"),
            ats_result={"ats_score": {"overall_score": 80.0, "recommendation": "Worth Interview", "grade": "B"},
                        "keyword_summary": {"coverage": 0.8}},
            screening_decision="AUTO_APPLY",
            recruiter=SimpleNamespace(final_score=82.0, interview_probability=60.0, recommendation="Apply",
                                        risk_level="LOW", strengths=[], critical_gaps=[]),
            hard_eligibility=RemoteEligibilityResult(ELIGIBLE, "REMOTE_GLOBAL", "Explicit worldwide remote eligibility", "work from anywhere"),
        )
        defaults.update(self.evaluation_overrides)
        return SimpleNamespace(**defaults)

    def generate_application_documents(self, evaluation):
        self.docs += 1
        if self.raise_on_generate:
            raise AssertionError("documents must not be prepared for this priority")
        return SimpleNamespace(
            resume_strategy={"summary_focus": [], "keywords_to_strengthen": [], "keywords_missing": []},
            docx_path="fake/Resume.docx", cover_letter_docx_path=None, markdown_path="fake/Resume.md",
        )


def _agent(application_service, opportunity, tmp_path):
    agent = object.__new__(CareerAgent)
    history = ApplicationHistoryService(tmp_path / "history.db")
    agent.discovery = FakeDiscovery([opportunity])
    agent.queue = FakeQueue()
    agent.application_service = application_service
    agent.history = history
    agent.email_classifier = FakeEmailClassifier()
    agent.intelligence_service = JobIntelligenceService()
    return agent, history


def _run(job_id, evaluation_overrides=None, raise_on_generate=False, tmp_path=None, opportunity_overrides=None):
    opportunity = _opportunity(job_id, **(opportunity_overrides or {}))
    application_service = FakeApplicationService(evaluation_overrides, raise_on_generate=raise_on_generate)
    agent, history = _agent(application_service, opportunity, tmp_path)
    agent.process_jobs()
    record = history.get_record(fingerprint_for_opportunity(opportunity))
    return record, application_service


# --- Section 6: A/B prepare; C/D/E do not -----------------------------------

def test_priority_a_prepares_documents(tmp_path):
    record, service = _run("a", {
        "employer": SimpleNamespace(overall_score=90.0),
        "ats_result": {"ats_score": {"overall_score": 95.0, "grade": "A"}, "keyword_summary": {"coverage": 0.9}},
    }, tmp_path=tmp_path)
    assert record["intelligence_priority"] == "A"
    assert service.docs == 1
    assert record["resume_path"] == "fake/Resume.docx"


def test_priority_b_prepares_documents(tmp_path):
    record, service = _run("b", {}, tmp_path=tmp_path)  # defaults: grade B, employer 70 -> MEDIUM
    assert record["intelligence_priority"] == "B"
    assert service.docs == 1
    assert record["resume_path"] == "fake/Resume.docx"


def test_priority_c_human_review_blocks_preparation(tmp_path):
    record, service = _run("c", {"screening_decision": "REVIEW"}, raise_on_generate=True, tmp_path=tmp_path)
    assert record["intelligence_priority"] == "C"
    assert service.docs == 0
    assert not record.get("resume_path")


def test_priority_d_watch_blocks_preparation(tmp_path):
    record, service = _run("d", {"screening_decision": "SKIP"}, raise_on_generate=True, tmp_path=tmp_path)
    assert record["intelligence_priority"] == "D"
    assert service.docs == 0
    assert not record.get("resume_path")


def test_priority_e_reject_blocks_preparation(tmp_path):
    record, service = _run("e", {
        "hard_eligibility": RemoteEligibilityResult(INELIGIBLE, "REMOTE_COUNTRY_RESTRICTED", "UK residence required", "uk-based"),
    }, raise_on_generate=True, tmp_path=tmp_path)
    assert record["intelligence_priority"] == "E"
    assert service.docs == 0
    assert record["status"] == "REMOTE_INELIGIBLE"
    assert not record.get("resume_path")


# --- Section 6: no positive signal bypasses C/D/E ---------------------------

def test_ats_a_plus_cannot_bypass_human_review(tmp_path):
    record, service = _run("f", {
        "screening_decision": "REVIEW",
        "ats_result": {"ats_score": {"overall_score": 99.0, "grade": "A+"}, "keyword_summary": {"coverage": 0.99}},
        "employer": SimpleNamespace(overall_score=99.0),
    }, raise_on_generate=True, tmp_path=tmp_path)
    assert record["intelligence_priority"] == "C"
    assert service.docs == 0


def test_hard_requirement_gap_cannot_reach_preparation(tmp_path):
    record, service = _run("g", {
        "job_analysis": {"company": "Acme Partners", "job_title": "Accountant", "experience_required": 30},
        "ats_result": {"ats_score": {"overall_score": 99.0, "grade": "A+"}, "keyword_summary": {"coverage": 0.99}},
    }, raise_on_generate=True, tmp_path=tmp_path)
    assert record["intelligence_priority"] == "E"
    assert service.docs == 0


def test_invalid_vacancy_cannot_reach_preparation(tmp_path):
    record, service = _run(
        "h", {"job_description": "N/A"}, raise_on_generate=True, tmp_path=tmp_path,
        opportunity_overrides={"job_description": "N/A"},
    )
    assert record["intelligence_priority"] == "E"
    assert service.docs == 0


def test_uncertain_mandatory_requirement_cannot_auto_progress(tmp_path):
    record, service = _run("i", {
        "job_analysis": {"company": "Acme Partners", "job_title": "Accountant", "required_skills": ["SAP FICO Consultant"]},
    }, raise_on_generate=True, tmp_path=tmp_path)
    assert record["intelligence_priority"] == "C"
    assert service.docs == 0


def test_eligible_strong_candidate_prepares_normally(tmp_path):
    record, service = _run("j", {
        "employer": SimpleNamespace(overall_score=90.0),
        "career_decision": SimpleNamespace(overall_score=90.0, confidence=0.9, priority="HIGH", automation_level="FULL"),
        "ats_result": {"ats_score": {"overall_score": 92.0, "grade": "A"}, "keyword_summary": {"coverage": 0.9}},
    }, tmp_path=tmp_path)
    assert record["intelligence_priority"] == "A"
    assert record["candidate_competitiveness"] == "VERY_STRONG"
    assert service.docs == 1


# --- Section 6: submission authorization / persisted metadata / safety -----

def test_submission_authorization_remains_mandatory_even_for_priority_a():
    """A/B priority prepares documents but never touches Gmail/browser
    submission -- CareerAgent's own source never reaches either."""
    import inspect

    import app.services.career_agent as module
    source = inspect.getsource(module)
    for forbidden in ("GmailService", "create_draft_for_application", "send_message", "Playwright", "browser_automation"):
        assert forbidden not in source


def test_intelligence_metadata_is_persisted_on_the_tracker_record(tmp_path):
    record, _ = _run("k", {
        "employer": SimpleNamespace(overall_score=90.0),
        "ats_result": {"ats_score": {"overall_score": 95.0, "grade": "A"}, "keyword_summary": {"coverage": 0.9}},
    }, tmp_path=tmp_path)
    assert record["intelligence_priority"] == "A"
    assert record["vacancy_validity"] in ("VERIFIED", "LIKELY_VALID")
    assert record["opportunity_value"] == "HIGH"
    assert record["candidate_competitiveness"] in ("VERY_STRONG", "STRONG", "COMPETITIVE")
    import json
    reasons = json.loads(record["intelligence_priority_reasons"])
    assert isinstance(reasons, list) and reasons


def test_no_production_mutation_from_career_agent_run(tmp_path):
    import hashlib

    real_history_db = "app/data/application_history.db"
    before = hashlib.sha256(open(real_history_db, "rb").read()).hexdigest()

    _run("l", {}, tmp_path=tmp_path)

    after = hashlib.sha256(open(real_history_db, "rb").read()).hexdigest()
    assert before == after
