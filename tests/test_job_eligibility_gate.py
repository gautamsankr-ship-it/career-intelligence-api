"""Task 21.14A: hard eligibility must gate application-document preparation
BEFORE resume/cover-letter/custom-response/email/DOCX generation, in both
ApplicationService entry points (generate_application_documents -- the
strict AUTO_APPLY path used by CareerAgent -- and prepare_application --
the vacancy-driven path). This was the known architectural gap: neither
method previously consulted hard eligibility at all.

Fully hermetic: JobEvaluation is constructed directly (no OpenAI call); all
file output is redirected via monkeypatch/tmp_path; no Gmail, no tracker/
history, no Answer Vault access, no production directories touched.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import docx_service
from app.services.application_email_composer import ApplicationEmailComposer
from app.services.application_requirements import ApplicationRequirementService
from app.services.application_service import ApplicationService, JobEvaluation
from app.services.cover_letter_generator import CoverLetterGenerator
from app.services.custom_response_generator import CustomResponseGenerator
from app.services.remote_work_eligibility import ELIGIBLE, INELIGIBLE, MANUAL_REVIEW, NOT_APPLICABLE
from app.services.resume_composer import ResumeComposer
from app.services.resume_generator import ResumeGenerator
from app.services.resume_optimizer import ResumeOptimizer


def _service() -> ApplicationService:
    """Same collaborator-only bypass as test_application_service_prepare_application.py:
    none of these seven collaborators perform file I/O on construction, so
    this stays hermetic and fast without weakening what's under test."""
    service = object.__new__(ApplicationService)
    service.requirement_service = ApplicationRequirementService()
    service.resume_strategy_engine = ResumeOptimizer()
    service.resume_composer = ResumeComposer()
    service.resume_generator = ResumeGenerator()
    service.cover_letter_generator = CoverLetterGenerator()
    service.custom_response_generator = CustomResponseGenerator()
    service.email_composer = ApplicationEmailComposer()
    return service


PROFILE = {
    "candidate": {"full_name": "Jane Candidate", "email": "jane@example.test"},
    "experience": {"years": 15},
    "professional_summary": {"headline": "Chartered Accountant with 15+ years of experience."},
    "employment_history": [
        {"company": "Some Accounting Firm", "position": "Accountant",
         "responsibilities": ["Financial Reporting"], "technologies": ["Xero"]},
    ],
}


def _evaluation(hard_eligibility, screening_decision="AUTO_APPLY", job_description="Please submit your resume.") -> JobEvaluation:
    return JobEvaluation(
        profile=PROFILE,
        job_analysis={"company": "Acme Partners", "job_title": "Accountant",
                      "required_skills": ["Xero"], "finance_domains": ["Financial Reporting"]},
        employer=SimpleNamespace(overall_score=65.0),
        career_decision=SimpleNamespace(decision=screening_decision, overall_score=79.0),
        ats_result={
            "ats_score": {"overall_score": 71.2, "grade": "C"},
            "keyword_summary": {"matched": [], "partial": [], "missing": []},
        },
        screening_decision=screening_decision,
        job_description=job_description,
        hard_eligibility=hard_eligibility,
    )


# Plain SimpleNamespace, not RemoteEligibilityResult(...), so this stays a
# harmless module-level constant-building idiom (attribute access only --
# nothing under test ever isinstance()-checks against the real dataclass).
INELIGIBLE_RESULT = SimpleNamespace(decision=INELIGIBLE, scope="REMOTE_COUNTRY_RESTRICTED", reason="UK residence required", evidence="uk-based")
MANUAL_REVIEW_RESULT = SimpleNamespace(decision=MANUAL_REVIEW, scope="REMOTE_ELIGIBILITY_UNCLEAR", reason="Remote vacancy but geographic eligibility not stated", evidence="")
ELIGIBLE_RESULT = SimpleNamespace(decision=ELIGIBLE, scope="REMOTE_GLOBAL", reason="Explicit worldwide remote eligibility", evidence="work from anywhere")
NOT_APPLICABLE_RESULT = SimpleNamespace(decision=NOT_APPLICABLE, scope="REMOTE_NOT_APPLICABLE", reason="Vacancy is not confirmed remote", evidence="")


@pytest.fixture(autouse=True)
def _isolate_output_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(docx_service, "OUTPUT_DIR", tmp_path / "applications")
    real_evidence_library = Path.cwd() / "app" / "data" / "candidate_evidence_library.json"
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output" / "resumes").mkdir(parents=True, exist_ok=True)
    profile_dir = tmp_path / "app" / "data"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "master_candidate_profile.json").write_text("{}", encoding="utf-8")
    (profile_dir / "candidate_evidence_library.json").write_text(
        real_evidence_library.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _applications_dir_is_empty(tmp_path) -> bool:
    applications_dir = tmp_path / "applications"
    if not applications_dir.exists():
        return True
    return not any(applications_dir.rglob("*"))


# --- generate_application_documents(): the strict AUTO_APPLY path used by CareerAgent ---

def test_ineligible_vacancy_blocks_generate_application_documents(tmp_path):
    service = _service()
    evaluation = _evaluation(INELIGIBLE_RESULT)

    with pytest.raises(ValueError, match="eligibility"):
        service.generate_application_documents(evaluation)

    assert _applications_dir_is_empty(tmp_path)


def test_uncertain_eligibility_blocks_generate_application_documents_even_with_manual_flag(tmp_path):
    """manual=True overrides the SCREENING_AUTO_APPLY precondition (an
    existing, unrelated escape hatch) but must never bypass hard eligibility."""
    service = _service()
    evaluation = _evaluation(MANUAL_REVIEW_RESULT)

    with pytest.raises(ValueError, match="eligibility"):
        service.generate_application_documents(evaluation, manual=True)

    assert _applications_dir_is_empty(tmp_path)


def test_eligible_vacancy_generates_documents_normally(tmp_path):
    service = _service()
    evaluation = _evaluation(ELIGIBLE_RESULT)

    result = service.generate_application_documents(evaluation)

    assert result.docx_path is not None
    assert Path(result.docx_path).exists()


# --- human_review_package=True: the ONE narrow Task 21.24C exception -------

def test_ineligible_vacancy_still_blocks_generation_even_with_human_review_package_flag(tmp_path):
    """INELIGIBLE remains an absolute, unconditional block -- human_review_package
    (like manual) may never rescue it."""
    service = _service()
    evaluation = _evaluation(INELIGIBLE_RESULT)

    with pytest.raises(ValueError, match="eligibility"):
        service.generate_application_documents(evaluation, manual=True, human_review_package=True)

    assert _applications_dir_is_empty(tmp_path)


def test_uncertain_eligibility_still_blocks_generation_without_the_human_review_flag(tmp_path):
    """Confirms the pre-existing test above still holds unchanged: manual=True
    alone (human_review_package defaulting to False) never bypasses MANUAL_REVIEW."""
    service = _service()
    evaluation = _evaluation(MANUAL_REVIEW_RESULT)

    with pytest.raises(ValueError, match="eligibility"):
        service.generate_application_documents(evaluation, manual=True)

    assert _applications_dir_is_empty(tmp_path)


def test_uncertain_eligibility_generates_documents_when_human_review_package_is_explicit(tmp_path):
    """Task 21.24C: human_review_package=True is the one narrow, named
    exception that lets a MANUAL_REVIEW (unresolved hard eligibility)
    evaluation through to document generation -- used only by
    ApplicationPackageOrchestrator for a record whose own persisted
    package_gate already says PREPARE_FOR_HUMAN_REVIEW."""
    service = _service()
    evaluation = _evaluation(MANUAL_REVIEW_RESULT)

    result = service.generate_application_documents(evaluation, manual=True, human_review_package=True)

    assert result.docx_path is not None
    assert Path(result.docx_path).exists()


def test_not_applicable_eligibility_generates_documents_normally(tmp_path):
    """NOT_APPLICABLE means "not a remote-eligibility concern for this
    vacancy" -- a proceed state, same as ELIGIBLE, not a block."""
    service = _service()
    evaluation = _evaluation(NOT_APPLICABLE_RESULT)

    result = service.generate_application_documents(evaluation)

    assert result.docx_path is not None


def test_unassessed_eligibility_does_not_change_existing_behavior(tmp_path):
    """hard_eligibility=None (never assessed) must behave exactly as before
    Task 21.14A -- proceeds normally. This is what every pre-21.14A caller
    that doesn't know about eligibility still gets."""
    service = _service()
    evaluation = _evaluation(None)

    result = service.generate_application_documents(evaluation)

    assert result.docx_path is not None


# --- prepare_application(): the vacancy-driven path -----------------------

def test_ineligible_vacancy_prepare_application_generates_no_documents():
    service = _service()
    evaluation = _evaluation(INELIGIBLE_RESULT, job_description="Please submit your CV and cover letter.")

    plan = service.prepare_application(evaluation)

    assert plan.resume_docx_path is None
    assert plan.cover_letter_docx_path is None
    assert not plan.custom_responses
    assert plan.email_body is None
    assert plan.manifest["blocked"] is True
    assert plan.manifest["blocked_reason"] == "HARD_INELIGIBLE"
    assert plan.manifest["hard_eligibility"]["decision"] == INELIGIBLE


def test_uncertain_eligibility_prepare_application_routes_to_human_review_not_auto_preparation():
    service = _service()
    evaluation = _evaluation(MANUAL_REVIEW_RESULT, job_description="Please submit your CV and cover letter.")

    plan = service.prepare_application(evaluation)

    assert plan.resume_docx_path is None
    assert plan.cover_letter_docx_path is None
    assert not plan.custom_responses
    assert plan.email_body is None
    assert plan.manifest["human_review_required"] is True
    assert plan.manifest["human_review_reason"] == "HARD_ELIGIBILITY_UNCERTAIN"


def test_eligible_vacancy_prepare_application_continues_normally():
    service = _service()
    evaluation = _evaluation(ELIGIBLE_RESULT, job_description="Please submit your CV and cover letter.")

    plan = service.prepare_application(evaluation)

    assert plan.resume_docx_path is not None
    assert plan.cover_letter_docx_path is not None
    assert plan.manifest.get("blocked") is not True
    assert plan.manifest["hard_eligibility"]["decision"] == ELIGIBLE


def test_unassessed_eligibility_prepare_application_unchanged_from_before_21_14a():
    service = _service()
    evaluation = _evaluation(None, job_description="Please submit your CV and cover letter.")

    plan = service.prepare_application(evaluation)

    assert plan.resume_docx_path is not None
    assert "hard_eligibility" not in plan.manifest


def test_eligibility_gate_is_checked_before_requirements_ambiguity():
    """An ineligible vacancy with also-ambiguous document requirements
    reports the eligibility block, not a generic human-review-for-
    ambiguous-requirements outcome -- the more fundamental gate wins."""
    service = _service()
    evaluation = _evaluation(INELIGIBLE_RESULT, job_description="Please apply through our careers portal.")

    plan = service.prepare_application(evaluation)

    assert plan.manifest["blocked"] is True
    assert plan.manifest["blocked_reason"] == "HARD_INELIGIBLE"


def test_preparation_never_touches_tracker_gmail_or_answer_vault_when_gating():
    import app.services.application_service as module
    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("GmailService", "ApplicationHistoryService", "ApplicationAnswerVault"):
        assert forbidden not in source
