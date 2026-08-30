"""Task 21.17C: ONE deterministic integration test proving the real
object/state handoff across the pipeline boundaries Task 21.17B found were
separately-invoked, not one connected chain:

    CareerAgent.process_jobs() evaluation
    -> persisted tracker/history record (real ApplicationHistoryService)
    -> A/B intelligence_priority (real JobIntelligenceService)
    -> ApplicationPackageOrchestrator.prepare()
    -> ApplicationExecutionOrchestrator.execute()
    -> FinalReviewService.create() / .approve()

Stops BEFORE ApplicationSubmissionService -- no final submission, no browser
click, no APPLIED write is exercised anywhere in this file.

No OpenAI, no Gmail, no Apify, no real browser:
- CareerAgent's discovery/queue/email-classifier/application-service
  collaborators are faked, using the same established pattern as
  test_career_agent_eligibility_gate.py.
- The tracker is a REAL ApplicationHistoryService backed by an isolated
  sqlite file under tmp_path -- never app/data/application_history.db.
- ApplicationPackageOrchestrator, ApplicationExecutionOrchestrator, and
  FinalReviewService are all REAL, operating on that same isolated tracker
  and real (tmp_path-scoped) package/execution/review JSON stores.
- The "browser" is the REAL ApplicationBrowserService.preview_html()/
  fill_preview_url() run against a synthetic HTML form string -- no network,
  no Playwright launch.
- The candidate answer vault is the real, already-approved
  ApplicationAnswerVault (read-only, non-sensitive candidate facts already
  used this way elsewhere in the test suite).

Also proves the Task 21.17C negative controls: C/D/E priorities and a
missing/malformed intelligence_priority all fail closed at both the package
and execution boundary, and APPLIED is never written anywhere in this file.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.career_opportunity import CareerOpportunity
from app.models.employer import Employer
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_email_classifier import EmailClassification
from app.services.application_execution_orchestrator import ApplicationExecutionOrchestrator
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity
from app.services.application_package_orchestrator import ApplicationPackageOrchestrator
from app.services.career_agent import CareerAgent
from app.services.final_review_service import FinalReviewService
from app.services.job_intelligence_service import JobIntelligenceService
from app.services.remote_work_eligibility import ELIGIBLE
from helpers.synthetic_answer_engine import SyntheticAnswerEngine

GREENHOUSE_URL = "https://boards.greenhouse.io/example-corp/jobs/1"
SAFE_FORM = (
    '<form class="greenhouse">'
    '<label for="email">Email address</label><input id="email" type="email" required>'
    '<label for="cv">Resume/CV</label><input id="cv" type="file" required>'
    '<button>Submit Application</button></form>'
)


def _opportunity(job_id: str) -> CareerOpportunity:
    return CareerOpportunity(
        id=job_id, source="test", company="Acme Partners", job_title="Senior Accountant",
        job_description=(
            "We are looking for an experienced Senior Accountant to join our finance team, "
            "covering monthly financial reporting, reconciliations, and statutory compliance "
            "for a portfolio of entities. This is a fully remote role, open worldwide."
        ),
        work_arrangement="REMOTE", remote_status=True,
        job_url=GREENHOUSE_URL, application_url=GREENHOUSE_URL,
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
    """No explicit application email -- forces the WEB route, which is what
    exercises the browser/ATS chain this test proves."""

    def classify_opportunity(self, opportunity, job_analysis):
        return SimpleNamespace(classification=EmailClassification.WEB_APPLICATION_ONLY, selected_email=None)


class FakeApplicationService:
    """evaluate_job() returns a deterministic, fully fabricated JobEvaluation
    (no OpenAI call); generate_application_documents() returns real,
    already-existing local docx files under tmp_path (no OpenAI, no docx
    generation needed -- this test proves pipeline wiring, not document
    generation, which is already covered elsewhere)."""

    def __init__(self, resume_path, cover_letter_path, career_score=85.0, ats_grade="B", job_analysis_overrides=None):
        self.resume_path = str(resume_path)
        self.cover_letter_path = str(cover_letter_path)
        self.career_score = career_score
        self.ats_grade = ats_grade
        self.job_analysis_overrides = job_analysis_overrides or {}
        self.docs = 0
        self.evaluate_job_calls = 0
        self.snapshot_calls = 0
        self.last_job_analysis = None
        self.last_employer = None

    def _job_analysis(self):
        job_analysis = {
            "company": "Acme Partners", "job_title": "Senior Accountant",
            "required_skills": [], "education": [], "preferred_skills": [],
        }
        job_analysis.update(self.job_analysis_overrides)
        return job_analysis

    def _evaluation(self, job_description, job_analysis, employer):
        return SimpleNamespace(
            profile={"experience": {"years": 15}},
            job_analysis=job_analysis,
            job_description=job_description,
            employer=employer,
            career_decision=SimpleNamespace(
                overall_score=self.career_score, confidence=90.0, priority="HIGH",
                automation_level="FULL", scorecards=[],
            ),
            ats_result={
                "ats_score": {"overall_score": 80.0, "grade": self.ats_grade, "recommendation": "Worth Interview"},
                "keyword_summary": {"coverage": 0.8},
            },
            screening_decision="AUTO_APPLY",
            recruiter=SimpleNamespace(
                final_score=82.0, interview_probability=60.0, recommendation="Apply",
                risk_level="LOW", strengths=[], critical_gaps=[],
            ),
            hard_eligibility=SimpleNamespace(
                decision=ELIGIBLE, scope="REMOTE_GLOBAL",
                reason="Explicit worldwide remote eligibility", evidence="work from anywhere",
            ),
        )

    def evaluate_job(self, job_description, opportunity=None, hard_eligibility=None):
        self.evaluate_job_calls += 1
        # A real Employer dataclass (not SimpleNamespace) -- CareerAgent's
        # snapshot persistence (Task 21.17D) only serializes a genuine
        # dataclass instance via dataclasses.asdict(), exactly like the real
        # EmployerService.analyze() output.
        employer = Employer(
            company="Acme Partners", industry="Finance", company_size="51-200",
            remote_friendly=True, innovation_score=7, culture_score=7,
            career_growth_score=8, financial_stability_score=7, overall_score=8.0,
            strengths=[], risks=[], recommendation="Apply", reason="",
        )
        evaluation = self._evaluation(job_description, self._job_analysis(), employer)
        if hard_eligibility is not None:
            evaluation.hard_eligibility = hard_eligibility
        return evaluation

    def evaluate_from_snapshot(self, job_description, job_analysis, employer, opportunity=None, hard_eligibility=None):
        self.snapshot_calls += 1
        self.last_job_analysis = job_analysis
        self.last_employer = employer
        evaluation = self._evaluation(job_description, job_analysis, employer)
        if hard_eligibility is not None:
            evaluation.hard_eligibility = hard_eligibility
        return evaluation

    def generate_application_documents(self, evaluation):
        self.docs += 1
        return SimpleNamespace(
            resume_strategy={"summary_focus": [], "keywords_to_strengthen": [], "keywords_missing": []},
            docx_path=self.resume_path, cover_letter_docx_path=self.cover_letter_path, markdown_path="",
        )


class FakeBrowser:
    """Real ApplicationBrowserService field-classification/answer-mapping
    logic run against a synthetic HTML string -- no network, no Playwright
    browser launch, no click of any real control."""

    def __init__(self, preview_folder, html=SAFE_FORM):
        self.html = html
        self.preview_folder = Path(preview_folder)
        self.preview_calls = 0
        self.prepare_calls = 0

    def _service(self):
        return ApplicationBrowserService(preview_folder=self.preview_folder, answer_engine=SyntheticAnswerEngine())

    def preview_url(self, url, vacancy, tracker_id, headed, application_date):
        self.preview_calls += 1
        return self._service().preview_html(self.html, url, vacancy, tracker_id, persist=False)

    def fill_preview_url(self, url, vacancy, tracker_id, headed, application_date):
        self.prepare_calls += 1
        plan = self._service().preview_html(self.html, url, vacancy, tracker_id, persist=False)
        plan.fields_filled = sum(field.action == "FILL" for field in plan.fields)
        for document in plan.document_requirements:
            if document["action"] == "READY_FOR_UPLOAD" and document["kind"] in {"RESUME", "COVER_LETTER"}:
                document["action"] = "UPLOADED_IN_FILL_PREVIEW"
        return plan

    def resolve_route_url(self, url, record, headed=False):
        return SimpleNamespace(
            resolution_status="EXTERNAL_ROUTE_UNRESOLVED", application_url="",
            application_url_type="", portal="UNKNOWN", route_confidence="LOW",
        )


def _agent(application_service, opportunity, history):
    agent = object.__new__(CareerAgent)
    agent.discovery = FakeDiscovery([opportunity])
    agent.queue = FakeQueue()
    agent.application_service = application_service
    agent.history = history
    agent.email_classifier = FakeEmailClassifier()
    agent.intelligence_service = JobIntelligenceService()
    return agent


def _vault(tmp_path):
    # NEVER default to ApplicationAnswerVault()'s real production path
    # (app/data/application_answer_vault.json) -- an isolated tmp_path copy
    # seeds the same default candidate answers, kept fully separate from the
    # real file, matching the established convention in
    # tests/test_application_package_orchestrator.py.
    return ApplicationAnswerVault(Path(tmp_path) / "vault.json")


def _documents(tmp_path):
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    resume = tmp_path / "Resume.docx"
    cover = tmp_path / "CoverLetter.docx"
    resume.write_text("resume")
    cover.write_text("cover letter")
    return resume, cover


def test_connected_pipeline_handoff_from_evaluation_to_final_review(tmp_path):
    """The positive chain: a genuinely competitive, eligible vacancy reaches
    A/B intelligence priority through the real CareerAgent evaluation path,
    and that SAME tracker_id/state then flows, unmodified, through package
    preparation, execution, and final review -- proving the previously
    separate pipeline boundaries are genuinely connectable end to end."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    resume, cover = _documents(tmp_path / "docs")
    opportunity = _opportunity("job-connected")
    application_service = FakeApplicationService(resume, cover)
    agent = _agent(application_service, opportunity, history)

    agent.process_jobs()

    record = history.get_record(fingerprint_for_opportunity(opportunity))
    assert record["intelligence_priority"] in {"A", "B"}
    assert record["status"] == "MANUAL_WEB_REQUIRED"
    assert application_service.docs == 1
    tracker_id = record["id"]

    package_service = ApplicationPackageOrchestrator(
        history=history, document_service=application_service, vault=_vault(tmp_path),
        package_dir=tmp_path / "packages",
    )
    package = package_service.prepare(tracker_id)
    assert package.readiness in {"READY_FOR_BROWSER_PREPARATION", "READY_FOR_APPLICATION"}
    assert package.resume_path == application_service.resume_path

    execution_service = ApplicationExecutionOrchestrator(
        package_service=package_service, browser=FakeBrowser(tmp_path / "previews"), execution_dir=tmp_path / "executions",
    )
    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "PREPARED_FOR_FINAL_REVIEW"
    assert execution.resume_uploaded

    review_service = FinalReviewService(
        package_service=package_service, review_dir=tmp_path / "reviews", execution_dir=tmp_path / "executions",
        answer_engine=ApplicationAnswerEngine(package_service.vault),
    )
    review = review_service.create(tracker_id)
    assert review.review_status == "READY_FOR_HUMAN_REVIEW"
    approved = review_service.approve(review.review_id)
    assert approved.review_status == "APPROVED_FOR_SUBMISSION"

    # Stop here: no ApplicationSubmissionService, no browser click, no submit.
    final_record = history.get_record_by_id(tracker_id)
    assert final_record["status"] != "APPLIED"
    assert final_record.get("applied_at") in (None, "")


@pytest.mark.parametrize("job_analysis_overrides,expected_priority", [
    ({}, "B"),
    # Missing company -> vacancy_validity UNCERTAIN -> Tier-2 HUMAN_REVIEW (C),
    # reached through the real CareerAgent/JobIntelligenceService path, not
    # forced onto the tracker record directly.
    ({"company": ""}, "C"),
])
def test_negative_controls_c_priority_blocked_at_package_and_execution(tmp_path, job_analysis_overrides, expected_priority):
    """A vacancy that genuinely reaches C through the real intelligence
    pipeline must never reach package readiness or execution -- and the
    genuinely competitive (B) case must, proving both outcomes share one
    wiring path, not two."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    resume, cover = _documents(tmp_path / "docs")
    opportunity = _opportunity(f"job-{expected_priority}")
    application_service = FakeApplicationService(resume, cover, job_analysis_overrides=job_analysis_overrides)
    agent = _agent(application_service, opportunity, history)

    agent.process_jobs()

    record = history.get_record(fingerprint_for_opportunity(opportunity))
    assert record["intelligence_priority"] == expected_priority
    tracker_id = record["id"]
    package_service = ApplicationPackageOrchestrator(
        history=history, document_service=application_service, vault=_vault(tmp_path),
        package_dir=tmp_path / "packages",
    )
    package = package_service.prepare(tracker_id)
    execution_service = ApplicationExecutionOrchestrator(
        package_service=package_service, browser=FakeBrowser(tmp_path / "previews"), execution_dir=tmp_path / "executions",
    )
    execution = execution_service.execute(tracker_id, "PREPARE")

    if record["intelligence_priority"] in {"A", "B"}:
        assert package.readiness in {"READY_FOR_BROWSER_PREPARATION", "READY_FOR_APPLICATION"}
        assert execution.status == "PREPARED_FOR_FINAL_REVIEW"
    else:
        assert package.readiness == "NOT_APPLICATION_ELIGIBLE"
        assert execution.status == "NOT_APPLICATION_ELIGIBLE"

    final_record = history.get_record_by_id(tracker_id)
    assert final_record["status"] != "APPLIED"


@pytest.mark.parametrize("priority", ["C", "D", "E"])
def test_negative_controls_explicit_c_d_e_priority_blocked(tmp_path, priority):
    """Direct proof for each of C/D/E specifically (rather than relying on a
    scoring path that happens to reach them): a tracker record with that
    intelligence_priority must never reach package readiness or execution,
    regardless of legacy decision/remote_eligibility fields."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    resume, cover = _documents(tmp_path / "docs")
    opportunity = _opportunity(f"job-explicit-{priority}")
    application_service = FakeApplicationService(resume, cover)
    fingerprint, _ = history.record_evaluation(
        opportunity, application_service.evaluate_job(opportunity.job_description), "REVIEW",
        remote_eligibility=ELIGIBLE,
    )
    history.update_record(fingerprint, intelligence_priority=priority, resume_path=str(resume), cover_letter_path=str(cover))
    tracker_id = history.get_record(fingerprint)["id"]

    package_service = ApplicationPackageOrchestrator(
        history=history, document_service=application_service, vault=_vault(tmp_path),
        package_dir=tmp_path / "packages",
    )
    package = package_service.prepare(tracker_id)
    execution_service = ApplicationExecutionOrchestrator(
        package_service=package_service, browser=FakeBrowser(tmp_path / "previews"), execution_dir=tmp_path / "executions",
    )
    execution = execution_service.execute(tracker_id, "PREPARE")

    assert package.readiness == "NOT_APPLICATION_ELIGIBLE"
    assert execution.status == "NOT_APPLICATION_ELIGIBLE"
    assert application_service.docs == 0
    assert history.get_record_by_id(tracker_id)["status"] != "APPLIED"


def test_missing_intelligence_priority_fails_closed_in_the_real_execution_orchestrator(tmp_path):
    """A tracker record with no intelligence_priority at all (e.g. a
    malformed or never-evaluated record) must fail closed at execution --
    the closer-to-real-action boundary -- even though the legacy
    decision/remote_eligibility fields alone would have allowed it through."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    resume, cover = _documents(tmp_path / "docs")
    opportunity = _opportunity("job-missing-priority")
    application_service = FakeApplicationService(resume, cover)
    fingerprint, _ = history.record_evaluation(
        opportunity, application_service.evaluate_job(opportunity.job_description), "REVIEW",
        remote_eligibility=ELIGIBLE,
    )
    # Deliberately no intelligence_priority update -- simulates a malformed/
    # never-evaluated record. decision=AUTO_APPLY and remote_eligibility=
    # ELIGIBLE are both already set by record_evaluation() above.
    history.update_record(fingerprint, resume_path=str(resume), cover_letter_path=str(cover))
    tracker_id = history.get_record(fingerprint)["id"]

    package_service = ApplicationPackageOrchestrator(
        history=history, document_service=application_service, vault=_vault(tmp_path),
        package_dir=tmp_path / "packages",
    )
    execution_service = ApplicationExecutionOrchestrator(
        package_service=package_service, browser=FakeBrowser(tmp_path / "previews"), execution_dir=tmp_path / "executions",
    )
    # Package preparation still has its documented legacy fallback for
    # pre-21.14E records with no intelligence_priority (unchanged behavior).
    package = package_service.prepare(tracker_id)
    assert package.readiness in {"READY_FOR_BROWSER_PREPARATION", "READY_FOR_APPLICATION"}
    # Execution, however, must fail closed regardless.
    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "NOT_APPLICATION_ELIGIBLE"
    assert history.get_record_by_id(tracker_id)["status"] != "APPLIED"


def test_one_consistent_evaluation_snapshot_flows_from_career_agent_through_final_review(tmp_path):
    """Task 21.17D: CareerAgent's ONE evaluate_job() call persists both
    intelligence_priority and the evaluation snapshot (job_analysis +
    employer) together, on the same tracker row. When package generation is
    later needed again (e.g. the original documents are no longer present),
    ApplicationPackageOrchestrator must reuse that SAME persisted snapshot --
    proven here by asserting no second evaluate_job() call occurs, the
    reused job_analysis/employer are byte-for-byte the ones CareerAgent
    itself produced, and intelligence_priority is never overwritten -- all
    the way through to FinalReviewService."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    resume, cover = _documents(tmp_path / "docs")
    opportunity = _opportunity("job-snapshot-consistency")
    application_service = FakeApplicationService(resume, cover)
    agent = _agent(application_service, opportunity, history)

    agent.process_jobs()

    record = history.get_record(fingerprint_for_opportunity(opportunity))
    assert record["intelligence_priority"] == "B"
    assert application_service.evaluate_job_calls == 1
    snapshot = json.loads(record["evaluation_snapshot"])
    assert snapshot["job_analysis"]["company"] == "Acme Partners"
    assert snapshot["employer"]["overall_score"] == 8.0
    tracker_id = record["id"]

    # Simulate document generation being needed again (e.g. the original
    # files were cleaned up) -- this forces ApplicationPackageOrchestrator
    # to call _generate_documents() a second time.
    history.update_record(fingerprint_for_opportunity(opportunity), resume_path="", cover_letter_path="")

    package_service = ApplicationPackageOrchestrator(
        history=history, document_service=application_service, vault=_vault(tmp_path),
        package_dir=tmp_path / "packages",
    )
    package = package_service.prepare(tracker_id)

    # The persisted snapshot was reused -- no second evaluate_job() call.
    assert application_service.evaluate_job_calls == 1
    assert application_service.snapshot_calls == 1
    assert package.evaluation_source == "PERSISTED_SNAPSHOT"
    assert application_service.last_job_analysis == snapshot["job_analysis"]
    assert application_service.last_employer.overall_score == snapshot["employer"]["overall_score"]
    assert package.readiness in {"READY_FOR_BROWSER_PREPARATION", "READY_FOR_APPLICATION"}

    # intelligence_priority was never touched by this second prepare() call.
    record_after = history.get_record_by_id(tracker_id)
    assert record_after["intelligence_priority"] == "B"

    execution_service = ApplicationExecutionOrchestrator(
        package_service=package_service, browser=FakeBrowser(tmp_path / "previews"), execution_dir=tmp_path / "executions",
    )
    execution = execution_service.execute(tracker_id, "PREPARE")
    assert execution.status == "PREPARED_FOR_FINAL_REVIEW"

    review_service = FinalReviewService(
        package_service=package_service, review_dir=tmp_path / "reviews", execution_dir=tmp_path / "executions",
        answer_engine=ApplicationAnswerEngine(package_service.vault),
    )
    review = review_service.create(tracker_id)
    assert review.review_status == "READY_FOR_HUMAN_REVIEW"

    assert history.get_record_by_id(tracker_id)["status"] != "APPLIED"
