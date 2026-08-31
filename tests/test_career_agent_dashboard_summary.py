"""Task 21.23A: `career_agent_dashboard.py` crashed with `KeyError: 'ready'`
because it read `summary['ready']` / `summary['rejected']` while
`CareerAgent.dashboard_summary()` has only ever returned `apply` / `review` /
`skip`. `apply_jobs.py` -- the other real caller of `dashboard_summary()` --
already consumes `apply`/`review`/`skip` correctly, so that is the
authoritative contract; the fix is confined to the two stale keys in
`career_agent_dashboard.py`.

Fully hermetic, mirroring test_career_agent_eligibility_gate.py's pattern:
discovery/queue/email-classifier/application-service are faked
(object.__new__ bypass on CareerAgent); only the tracker is real, backed by
an isolated sqlite file under tmp_path -- never the production
app/data/application_history.db. No Gmail, no OpenAI, no Apify call.
"""

import importlib
import io
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.config import SCREENING_AUTO_APPLY, SCREENING_REVIEW, SCREENING_SKIP
from app.models.career_opportunity import CareerOpportunity
from app.services.application_email_classifier import EmailClassification
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity
from app.services.career_agent import CareerAgent
from app.services.job_intelligence_service import JobIntelligenceService
from app.services.remote_work_eligibility import ELIGIBLE


def _opportunity(job_id: str, title: str) -> CareerOpportunity:
    return CareerOpportunity(
        id=job_id, source="test", company="Acme Partners", job_title=title,
        job_description=(
            "Please send your CV to jobs@acme.test. We are looking for an experienced "
            "accountant to join our finance team covering reporting and compliance work."
        ),
        work_arrangement="REMOTE", remote_status=True,
        job_url="https://example.test/jobs/1",
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


ELIGIBLE_RESULT = SimpleNamespace(decision=ELIGIBLE, scope="REMOTE_GLOBAL", reason="Explicit worldwide remote eligibility", evidence="work from anywhere")


class FakeApplicationService:
    """evaluate_job returns a pre-built evaluation whose screening_decision
    (and matching career_decision.decision) is fixed per opportunity id, so
    dashboard_summary()'s apply/review/skip buckets are deterministic
    regardless of what the real JobIntelligenceService computes for A-E."""

    def __init__(self, decision_by_id: dict[str, str]):
        self.decision_by_id = decision_by_id
        self.docs = 0

    def evaluate_job(self, job_description, opportunity=None):
        decision = self.decision_by_id[opportunity.id]
        return SimpleNamespace(
            profile={"candidate": {"full_name": "Candidate"}, "experience": {"years": 15}},
            job_analysis={"company": "Acme Partners", "job_title": opportunity.job_title},
            job_description=job_description,
            employer=SimpleNamespace(overall_score=70.0),
            career_decision=SimpleNamespace(overall_score=85.0, confidence=0.9, priority="HIGH", automation_level="FULL", decision=decision),
            ats_result={"ats_score": {"overall_score": 80.0, "recommendation": "Worth Interview", "grade": "B"},
                        "keyword_summary": {"coverage": 0.8}},
            screening_decision=decision,
            recruiter=SimpleNamespace(final_score=82.0, interview_probability=60.0, recommendation="Apply",
                                        risk_level="LOW", strengths=[], critical_gaps=[]),
            hard_eligibility=ELIGIBLE_RESULT,
        )

    def generate_application_documents(self, evaluation):
        self.docs += 1
        return SimpleNamespace(
            resume_strategy={"summary_focus": [], "keywords_to_strengthen": [], "keywords_missing": []},
            docx_path="fake/Resume.docx", cover_letter_docx_path=None, markdown_path="fake/Resume.md",
        )


def _agent(decision_by_id: dict[str, str], opportunities, tmp_path) -> tuple[CareerAgent, ApplicationHistoryService]:
    agent = object.__new__(CareerAgent)
    history = ApplicationHistoryService(tmp_path / "history.db")
    agent.discovery = FakeDiscovery(opportunities)
    agent.queue = FakeQueue()
    agent.application_service = FakeApplicationService(decision_by_id)
    agent.history = history
    agent.email_classifier = FakeEmailClassifier()
    agent.intelligence_service = JobIntelligenceService()
    return agent, history


def test_dashboard_summary_apply_review_skip_counts_are_correct(tmp_path):
    opportunities = [
        _opportunity("job-apply", "Auto Apply Role"),
        _opportunity("job-review", "Review Role"),
        _opportunity("job-skip", "Skip Role"),
    ]
    decision_by_id = {
        "job-apply": SCREENING_AUTO_APPLY,
        "job-review": SCREENING_REVIEW,
        "job-skip": SCREENING_SKIP,
    }
    agent, history = _agent(decision_by_id, opportunities, tmp_path)

    summary = agent.dashboard_summary()

    # Contract proof: exactly the keys career_agent_dashboard.py and
    # apply_jobs.py actually read -- no 'ready'/'rejected'.
    for key in ("total_jobs", "apply", "review", "skip", "career_average", "recruiter_average", "highest", "jobs"):
        assert key in summary
    assert "ready" not in summary
    assert "rejected" not in summary

    assert summary["total_jobs"] == 3
    assert summary["apply"] == 1
    assert summary["review"] == 1
    assert summary["skip"] == 1


def test_dashboard_script_consumes_real_summary_contract_without_keyerror(tmp_path):
    """Patch CareerAgent so `career_agent_dashboard.main()` runs against a
    real dashboard_summary() shape, never the production DB, and prove it
    completes and prints the fixed labels without KeyError."""
    opportunities = [
        _opportunity("job-apply", "Auto Apply Role"),
        _opportunity("job-review", "Review Role"),
        _opportunity("job-skip", "Skip Role"),
    ]
    decision_by_id = {
        "job-apply": SCREENING_AUTO_APPLY,
        "job-review": SCREENING_REVIEW,
        "job-skip": SCREENING_SKIP,
    }
    real_agent, history = _agent(decision_by_id, opportunities, tmp_path)

    sys.modules.pop("career_agent_dashboard", None)
    module = importlib.import_module("career_agent_dashboard")
    try:
        with patch.object(module, "CareerAgent", return_value=real_agent) as mock_career_agent:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                module.main()
            mock_career_agent.assert_called_once()
        output = buffer.getvalue()
        assert "Approve & Send  : 1" in output
        assert "Generate Package: 1" in output
        assert "Rejected        : 1" in output
    finally:
        sys.modules.pop("career_agent_dashboard", None)


def test_dashboard_rendering_never_mutates_production_tracker():
    """Importing/running the dashboard script against a fully mocked
    CareerAgent must never touch the real production database -- the same
    hazard test_test_isolation.py guards for import, extended to main()."""
    sys.modules.pop("career_agent_dashboard", None)
    with patch("app.services.career_agent.CareerAgent") as mock_career_agent:
        module = importlib.import_module("career_agent_dashboard")
        mock_career_agent.assert_not_called()

        fake_summary = {
            "total_jobs": 0, "apply": 0, "review": 0, "skip": 0,
            "career_average": 0, "recruiter_average": 0, "highest": None, "jobs": [],
        }
        mock_career_agent.return_value.dashboard_summary.return_value = fake_summary

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            module.main()
        mock_career_agent.assert_called_once()
        mock_career_agent.return_value.dashboard_summary.assert_called_once()
    sys.modules.pop("career_agent_dashboard", None)
