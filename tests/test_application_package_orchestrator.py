from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_package_orchestrator import ApplicationPackageOrchestrator


class History:
    def __init__(self, records): self.records = {record["id"]: dict(record) for record in records}
    def get_record_by_id(self, tracker_id): return self.records.get(tracker_id)
    def list_ready_records(self): return list(self.records.values())


class Documents:
    def __init__(self, directory): self.directory=Path(directory); self.calls=0; self.last_hard_eligibility=None
    def evaluate_job(self, text, opportunity=None, hard_eligibility=None):
        self.calls += 1
        self.last_hard_eligibility = hard_eligibility
        return SimpleNamespace(hard_eligibility=hard_eligibility)
    def generate_application_documents(self, evaluation):
        resume=self.directory / "generated-resume.docx"; cover=self.directory / "generated-cover.docx"
        resume.write_text("resume", encoding="utf-8"); cover.write_text("cover", encoding="utf-8")
        return SimpleNamespace(docx_path=str(resume), cover_letter_docx_path=str(cover))


def record(identifier=1, **extra):
    return {"id":identifier, "job_fingerprint":f"fingerprint-{identifier}", "company":"Example", "job_title":"Finance Manager",
            "job_description":"Finance leadership and reporting", "market":"united_kingdom", "career_track":"FINANCE",
            "career_score":85.0, "ats_score":80.0, "decision":"AUTO_APPLY", "remote_eligibility":"ELIGIBLE",
            "status":"MANUAL_WEB_REQUIRED", "application_status":"MANUAL_WEB_REQUIRED", "application_method":"WEB",
            "application_url":"https://boards.greenhouse.io/example/jobs/1", "application_portal":"GREENHOUSE",
            "application_route_confidence":"HIGH", "application_url_type":"ATS_URL", "source_listing_url":"https://boards.greenhouse.io/example/jobs/1", **extra}


def service(tmp_path, records):
    return ApplicationPackageOrchestrator(History(records), Documents(tmp_path), ApplicationAnswerVault(tmp_path / "vault.json"), tmp_path / "packages")


def test_eligible_auto_apply_generates_idempotent_browser_package(tmp_path):
    orchestrator=service(tmp_path, [record()])
    package=orchestrator.prepare(1)
    again=orchestrator.prepare(1)
    assert package.readiness == "READY_FOR_BROWSER_PREPARATION"
    assert package.portal_capability == "FULL_PREPARATION_SUPPORTED"
    assert package.resume_status == package.cover_letter_status == "READY"
    assert package.package_id == again.package_id
    assert orchestrator.document_service.calls == 1
    assert orchestrator.history.records[1]["status"] == "MANUAL_WEB_REQUIRED"


@pytest.mark.parametrize("change,expected", [
    ({"decision":"SKIP"}, "NOT_AUTO_APPLY"), ({"decision":"REVIEW"}, "NOT_AUTO_APPLY"),
    ({"remote_eligibility":"INELIGIBLE"}, "REMOTE_ELIGIBILITY_NOT_CONFIRMED"),
    ({"remote_eligibility":"MANUAL_REVIEW"}, "REMOTE_ELIGIBILITY_NOT_CONFIRMED"),
    ({"remote_eligibility":None}, "REMOTE_ELIGIBILITY_NOT_CONFIRMED"),
    ({"validation_only":True}, "VALIDATION_ONLY_REJECTED"),
])
def test_production_gate_rejects_nonproduction_and_validation_only_records(tmp_path, change, expected):
    orchestrator=service(tmp_path, [record(**change)])
    package=orchestrator.prepare(1)
    assert package.readiness == "NOT_APPLICATION_ELIGIBLE" and package.blocking_reasons == [expected]
    assert orchestrator.document_service.calls == 0


def test_not_applicable_remote_eligibility_is_not_blocked(tmp_path):
    """Task 21.14B regression: a non-remote vacancy correctly resolves to
    NOT_APPLICABLE (no known blocker), not "ELIGIBLE" -- the previous strict
    `!= "ELIGIBLE"` check silently rejected every such vacancy."""
    orchestrator = service(tmp_path, [record(remote_eligibility="NOT_APPLICABLE")])
    package = orchestrator.prepare(1)
    assert package.readiness != "NOT_APPLICATION_ELIGIBLE"
    assert "REMOTE_ELIGIBILITY_NOT_CONFIRMED" not in package.blocking_reasons


@pytest.mark.parametrize("priority,expected", [
    ("E", "INTELLIGENCE_REJECTED"),
    ("C", "INTELLIGENCE_HUMAN_REVIEW_REQUIRED"),
    ("D", "INTELLIGENCE_WATCH"),
])
def test_intelligence_priority_is_authoritative_over_legacy_fields_when_present(tmp_path, priority, expected):
    """Task 21.14E: intelligence_priority, when persisted, is the primary
    gate -- even when the legacy decision/remote_eligibility fields would
    otherwise have allowed the record through, a C/D/E priority still blocks."""
    orchestrator = service(tmp_path, [record(
        intelligence_priority=priority, decision="AUTO_APPLY", remote_eligibility="ELIGIBLE",
    )])
    package = orchestrator.prepare(1)
    assert package.readiness == "NOT_APPLICATION_ELIGIBLE"
    assert package.blocking_reasons == [expected]
    assert orchestrator.document_service.calls == 0


@pytest.mark.parametrize("priority", ["A", "B"])
def test_intelligence_priority_a_or_b_proceeds_even_if_legacy_fields_would_have_blocked(tmp_path, priority):
    """The inverse: A/B priority proceeds even when the legacy decision
    field alone would have blocked it (e.g. a stale SKIP left over from an
    earlier evaluation) -- intelligence_priority overrides, never the reverse."""
    orchestrator = service(tmp_path, [record(intelligence_priority=priority, decision="SKIP", remote_eligibility="INELIGIBLE")])
    package = orchestrator.prepare(1)
    assert package.readiness != "NOT_APPLICATION_ELIGIBLE"
    # Not blocked -> proceeds far enough to actually generate documents.
    assert orchestrator.document_service.calls == 1


def test_intelligence_priority_a_still_blocked_by_terminal_application_status(tmp_path):
    """A terminal post-application status still blocks, even for a
    ready (A/B) intelligence priority -- the terminal check is not bypassed."""
    orchestrator = service(tmp_path, [record(intelligence_priority="A", status="APPLIED", application_status="APPLIED")])
    package = orchestrator.prepare(1)
    assert package.readiness == "NOT_APPLICATION_ELIGIBLE"
    assert package.blocking_reasons == ["TERMINAL_APPLICATION_STATUS"]


def test_generate_documents_reuses_tracker_recorded_eligibility_not_a_fresh_recomputation(tmp_path):
    """Task 21.14B: the tracker's already-recorded remote_eligibility (the
    same value _eligibility_reason() just gated on) must reach
    ApplicationService.evaluate_job() directly, rather than being dropped in
    favour of a fresh, weaker job_analysis-derived reclassification."""
    orchestrator = service(tmp_path, [record(
        remote_eligibility="ELIGIBLE", remote_eligibility_reason="Explicit worldwide remote eligibility",
        remote_eligibility_evidence="work from anywhere",
    )])
    orchestrator.prepare(1)
    passed = orchestrator.document_service.last_hard_eligibility
    assert passed is not None
    assert passed.decision == "ELIGIBLE"
    assert passed.reason == "Explicit worldwide remote eligibility"
    assert passed.evidence == "work from anywhere"


def test_existing_same_tracker_documents_are_reused_and_wrong_prior_identity_is_not(tmp_path):
    resume=tmp_path / "resume.docx"; cover=tmp_path / "cover.docx"; resume.write_text("r"); cover.write_text("c")
    orchestrator=service(tmp_path, [record(resume_path=str(resume), cover_letter_path=str(cover))])
    package=orchestrator.prepare(1)
    assert package.resume_path == str(resume) and orchestrator.document_service.calls == 0
    saved=orchestrator.load(1); saved.vacancy_identity="wrong"; orchestrator._save(saved)
    # With no tracker-bound paths, a package from another vacancy cannot be reused.
    orchestrator.history.records[1].update(resume_path="", cover_letter_path="")
    package=orchestrator.prepare(1)
    assert package.resume_path.endswith("generated-resume.docx") and orchestrator.document_service.calls == 1


def test_required_cover_and_route_capabilities(tmp_path):
    direct=service(tmp_path, [record(cover_letter_requirement="REQUIRED")])
    assert direct.prepare(1).cover_letter_status == "READY"
    lever=service(tmp_path, [record(application_url="https://jobs.lever.co/example/1/apply", application_portal="LEVER")])
    assert lever.prepare(1).portal_capability == "FULL_PREPARATION_SUPPORTED"
    workday=service(tmp_path, [record(application_url="https://example.workdayjobs.com/job/1/apply", application_portal="WORKDAY")])
    assert workday.prepare(1).portal_capability == "PARTIAL_AUTOMATION"


def test_linkedin_source_only_is_manual_web_and_answer_vault_is_reported(tmp_path):
    orchestrator=service(tmp_path, [record(application_url="", application_url_type="JOB_LISTING_URL", application_portal="UNKNOWN", source_listing_url="https://linkedin.com/jobs/view/1", job_url="https://linkedin.com/jobs/view/1")])
    package=orchestrator.prepare(1)
    assert package.readiness == "MANUAL_WEB_REQUIRED"
    assert package.portal_capability == "MANUAL_WEB" and package.answer_vault_status == "ANSWER_VAULT_READY"
    assert package.answer_counts["AUTO_FILL"] > 0 and package.manual_answer_count > 0


def test_prepare_ready_excludes_validation_only_and_never_marks_applied(tmp_path):
    orchestrator=service(tmp_path, [record(1), record(2, validation_only=True)])
    packages=orchestrator.prepare_ready(5)
    assert [package.tracker_id for package in packages] == [1]
    assert all(value["status"] != "APPLIED" for value in orchestrator.history.records.values())
