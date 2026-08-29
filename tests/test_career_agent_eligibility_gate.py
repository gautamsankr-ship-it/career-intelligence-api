"""Task 21.14A: CareerAgent.process_jobs() is the primary orchestrator (used
directly, unlike AutoApplicationOrchestrator which already gated on
RemoteWorkEligibilityClassifier). Before this task it evaluated jobs and
called ApplicationService.generate_application_documents() without ever
consulting hard eligibility -- the known architectural gap. These tests
prove CareerAgent now closes it: an ineligible/uncertain opportunity's
documents are never generated, and the tracker records the same
REMOTE_INELIGIBLE/REMOTE_ELIGIBILITY_REVIEW statuses AutoApplicationOrchestrator
already used, rather than a new status.

Fully hermetic: discovery/queue/email-classifier/application-service are
faked (object.__new__ bypass on CareerAgent, mirroring the FakeApp pattern
already established in test_remote_work_eligibility.py); only the tracker
is real, backed by an isolated sqlite file under tmp_path -- never the
production app/data/application_history.db. No Gmail, no OpenAI call.
"""

from types import SimpleNamespace

import pytest

from app.models.career_opportunity import CareerOpportunity
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity
from app.services.application_email_classifier import EmailClassification
from app.services.career_agent import CareerAgent
from app.services.job_intelligence_service import JobIntelligenceService
from app.services.remote_work_eligibility import ELIGIBLE, INELIGIBLE, MANUAL_REVIEW


def _opportunity(job_id: str) -> CareerOpportunity:
    return CareerOpportunity(
        id=job_id, source="test", company="Acme Partners", job_title="Accountant",
        job_description=(
            "Please send your CV to jobs@acme.test. We are looking for an experienced "
            "accountant to join our finance team covering reporting and compliance work."
        ),
        work_arrangement="REMOTE", remote_status=True,
        job_url="https://example.test/jobs/1",  # avoids an "unresolved route" vacancy-validity signal
    )


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
    """evaluate_job returns a pre-built evaluation carrying the hard
    eligibility result under test; generate_application_documents raises if
    ever called for an ineligible/uncertain evaluation, exactly like the
    established FakeApp pattern in test_remote_work_eligibility.py."""

    def __init__(self, hard_eligibility):
        self.hard_eligibility = hard_eligibility
        self.docs = 0

    def evaluate_job(self, job_description, opportunity=None):
        return SimpleNamespace(
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
            hard_eligibility=self.hard_eligibility,
        )

    def generate_application_documents(self, evaluation):
        self.docs += 1
        raise AssertionError("documents must not be generated for an ineligible/uncertain vacancy")


def _agent(application_service, opportunity, tmp_path) -> tuple[CareerAgent, ApplicationHistoryService]:
    agent = object.__new__(CareerAgent)
    history = ApplicationHistoryService(tmp_path / "history.db")
    agent.discovery = FakeDiscovery([opportunity])
    agent.queue = FakeQueue()
    agent.application_service = application_service
    agent.history = history
    agent.email_classifier = FakeEmailClassifier()
    # Real JobIntelligenceService (Task 21.14E): no file/network I/O beyond
    # a read-only real-evidence-library lookup already established as safe
    # elsewhere (test_candidate_evidence_service.py).
    agent.intelligence_service = JobIntelligenceService()
    return agent, history


INELIGIBLE_RESULT = SimpleNamespace(decision=INELIGIBLE, scope="REMOTE_COUNTRY_RESTRICTED", reason="UK residence required", evidence="uk-based")
MANUAL_REVIEW_RESULT = SimpleNamespace(decision=MANUAL_REVIEW, scope="REMOTE_ELIGIBILITY_UNCLEAR", reason="Remote vacancy but geographic eligibility not stated", evidence="")
ELIGIBLE_RESULT = SimpleNamespace(decision=ELIGIBLE, scope="REMOTE_GLOBAL", reason="Explicit worldwide remote eligibility", evidence="work from anywhere")


@pytest.mark.parametrize("hard_eligibility,expected_status", [
    (INELIGIBLE_RESULT, "REMOTE_INELIGIBLE"),
    (MANUAL_REVIEW_RESULT, "REMOTE_ELIGIBILITY_REVIEW"),
])
def test_ineligible_or_uncertain_opportunity_never_reaches_document_generation(tmp_path, hard_eligibility, expected_status):
    opportunity = _opportunity("job-1")
    application_service = FakeApplicationService(hard_eligibility)
    agent, history = _agent(application_service, opportunity, tmp_path)

    agent.process_jobs()

    record = history.get_record(fingerprint_for_opportunity(opportunity))
    assert record["status"] == expected_status
    assert record["remote_eligibility"] == hard_eligibility.decision
    assert application_service.docs == 0


def test_eligible_opportunity_continues_through_the_real_pipeline(tmp_path):
    opportunity = _opportunity("job-2")

    class ElgibleApplicationService(FakeApplicationService):
        def generate_application_documents(self, evaluation):
            self.docs += 1
            return SimpleNamespace(
                resume_strategy={"summary_focus": [], "keywords_to_strengthen": [], "keywords_missing": []},
                docx_path="fake/Resume.docx", cover_letter_docx_path=None, markdown_path="fake/Resume.md",
            )

    application_service = ElgibleApplicationService(ELIGIBLE_RESULT)
    agent, history = _agent(application_service, opportunity, tmp_path)

    agent.process_jobs()

    record = history.get_record(fingerprint_for_opportunity(opportunity))
    assert record["remote_eligibility"] == ELIGIBLE
    assert record["status"] != "REMOTE_INELIGIBLE"
    assert record["status"] != "REMOTE_ELIGIBILITY_REVIEW"
    assert application_service.docs == 1
