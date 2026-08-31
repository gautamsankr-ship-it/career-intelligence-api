from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.application_package import ApplicationPackage
from app.services.application_browser_service import ApplicationBrowserService
from app.services.application_execution_orchestrator import ApplicationExecutionOrchestrator
from helpers.synthetic_answer_engine import SyntheticAnswerEngine


SAFE_FORM = '''<form class="greenhouse"><label for="email">Email address</label><input id="email" type="email" required><label for="notice">What is your notice period?</label><input id="notice" required><label for="cv">Resume/CV</label><input id="cv" type="file" required><label for="cover">Cover letter</label><input id="cover" type="file"><button>Next</button><button>Submit Application</button></form>'''
LEGAL_FORM = SAFE_FORM.replace('<button>Next', '<label for="legal">I certify this is accurate</label><input id="legal" type="checkbox" required><button>Next')


class History:
    def __init__(self, record): self.record=dict(record)
    def get_record_by_id(self, identifier): return self.record if identifier == self.record["id"] else None


class Packages:
    def __init__(self, record, package): self.history=History(record); self.package=package; self.saved=0
    def load(self, identifier): return self.package if identifier == self.package.tracker_id else None
    def _identity(self, record): return "identity"
    def _save(self, package): self.package=package; self.saved += 1; return package
    def ready(self): return [self.package]


class Browser:
    def __init__(self, html): self.html=html; self.preview_calls=0; self.prepare_calls=0; self.route_calls=0
    def _plan(self, url, vacancy, tracker_id): return ApplicationBrowserService(preview_folder=Path("."), answer_engine=SyntheticAnswerEngine()).preview_html(self.html, url, vacancy, tracker_id, persist=False)
    def preview_url(self, url, vacancy, tracker_id, headed, application_date):
        self.preview_calls += 1; return self._plan(url, vacancy, tracker_id)
    def fill_preview_url(self, url, vacancy, tracker_id, headed, application_date):
        self.prepare_calls += 1; plan=self._plan(url, vacancy, tracker_id)
        plan.fields_filled=sum(field.action == "FILL" for field in plan.fields)
        for document in plan.document_requirements:
            if document["action"] == "READY_FOR_UPLOAD" and document["kind"] in {"RESUME", "COVER_LETTER"}: document["action"]="UPLOADED_IN_FILL_PREVIEW"; plan.documents_uploaded += 1
        return plan
    def resolve_route_url(self, url, record, headed=False): self.route_calls += 1; return SimpleNamespace(resolution_status="EXTERNAL_ROUTE_UNRESOLVED", application_url="", application_url_type="", portal="UNKNOWN", route_confidence="LOW")


class ProgressBrowser(Browser):
    def __init__(self, pages, status="PREPARED_FOR_FINAL_REVIEW", actions=2): super().__init__(pages[0]); self.pages=pages; self.status=status; self.actions=actions
    def progress_url(self, url, vacancy, tracker_id, headed, application_date):
        plans=[]
        for page in self.pages:
            plan=self._plan(url, vacancy, tracker_id) if page == self.pages[0] else ApplicationBrowserService(preview_folder=Path("."), answer_engine=SyntheticAnswerEngine()).preview_html(page, url, vacancy, tracker_id, persist=False)
            plan.fields_filled=sum(field.action == "FILL" for field in plan.fields)
            for doc in plan.document_requirements:
                if doc["action"] == "READY_FOR_UPLOAD" and doc["kind"] in {"RESUME","COVER_LETTER"}: doc["action"]="UPLOADED_IN_FILL_PREVIEW"
            plans.append(plan)
        return {"plans":plans,"status":self.status,"navigation_actions":self.actions}


def record(**changes):
    return {"id":42, "job_fingerprint":"f", "company":"Example", "job_title":"Finance Manager", "job_description":"finance",
            "decision":"AUTO_APPLY", "remote_eligibility":"ELIGIBLE", "intelligence_priority":"B", "status":"MANUAL_WEB_REQUIRED", "application_status":"MANUAL_WEB_REQUIRED",
            "application_url":"https://boards.greenhouse.io/example/jobs/1", "source_listing_url":"https://boards.greenhouse.io/example/jobs/1", **changes}


def package(tmp_path, **changes):
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    resume=tmp_path / "resume.docx"; cover=tmp_path / "cover.docx"; resume.write_text("r"); cover.write_text("c")
    values={"company":"Example", "job_title":"Finance Manager", "market":"united_kingdom", "application_url":"https://boards.greenhouse.io/example/jobs/1", "application_portal":"GREENHOUSE", "route_confidence":"HIGH", "resume_path":str(resume), "resume_status":"READY", "resume_vacancy_identity":"identity", "cover_letter_path":str(cover), "cover_letter_status":"READY", "answer_vault_status":"ANSWER_VAULT_READY", "portal_capability":"FULL_PREPARATION_SUPPORTED", "readiness":"READY_FOR_BROWSER_PREPARATION", "vacancy_identity":"identity"}
    values.update(changes)
    return ApplicationPackage("pkg", 42, **values)


def orchestrator(tmp_path, html=SAFE_FORM, **changes):
    pkg=package(tmp_path, **changes); packages=Packages(record(), pkg); browser=Browser(html)
    return ApplicationExecutionOrchestrator(packages, browser, tmp_path / "executions"), packages, browser


def test_ready_package_inspect_and_preview_perform_no_page_writes(tmp_path):
    service, _, browser=orchestrator(tmp_path)
    assert service.execute(42, "INSPECT_ONLY").status == "READY_FOR_PREPARATION"
    preview=service.execute(42, "AUTOFILL_PREVIEW")
    assert preview.fields_filled == 0 and not preview.resume_uploaded and browser.prepare_calls == 0
    assert preview.fields_resolved >= 2 and preview.final_submit_detected


def test_prepare_greenhouse_and_lever_reach_final_review_without_submission(tmp_path):
    for portal, url in (("GREENHOUSE", "https://boards.greenhouse.io/example/jobs/1"), ("LEVER", "https://jobs.lever.co/example/1/apply")):
        service, packages, browser=orchestrator(tmp_path / portal)
        packages.package.application_portal=portal; packages.package.application_url=url
        result=service.execute(42, "PREPARE")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW"
        assert result.fields_filled >= 2 and result.resume_uploaded and result.cover_letter_uploaded
        assert result.final_submit_detected and browser.prepare_calls == 1
        assert packages.history.record["status"] == "MANUAL_WEB_REQUIRED"


def test_manual_legal_and_unknown_required_fields_are_never_filled(tmp_path):
    service, _, browser=orchestrator(tmp_path, LEGAL_FORM)
    result=service.execute(42, "PREPARE")
    assert result.status == "MANUAL_INPUT_REQUIRED" and result.fields_filled >= 2
    assert result.manual_review_fields >= 1
    unknown=SAFE_FORM.replace("What is your notice period?", "Why should we hire you?")
    result=orchestrator(tmp_path / "unknown", unknown)[0].execute(42, "PREPARE")
    assert result.status == "MANUAL_INPUT_REQUIRED" and result.unknown_required_fields == 1


@pytest.mark.parametrize(("html", "expected"), [("<form>CAPTCHA</form>", "CAPTCHA_REQUIRED"), ("<form>Sign in</form>", "AUTH_REQUIRED"), ("<form>Verification code</form>", "MFA_REQUIRED"), ("<form>Create an account</form>", "ACCOUNT_CREATION_REQUIRED")])
def test_access_barriers_stop_before_filling(tmp_path, html, expected):
    result=orchestrator(tmp_path / expected, html)[0].execute(42, "PREPARE")
    assert result.status == expected and result.fields_filled == 0 and not result.resume_uploaded


def test_package_and_production_guards_and_document_identity(tmp_path):
    service, packages, browser=orchestrator(tmp_path)
    packages.package.vacancy_identity="stale"
    assert service.execute(42).status == "PACKAGE_REFRESH_REQUIRED" and browser.preview_calls == 0
    service, packages, browser=orchestrator(tmp_path / "validation")
    packages.history.record["validation_only"]=True
    assert service.execute(42).status == "VALIDATION_ONLY_REJECTED" and browser.preview_calls == 0
    service, packages, browser=orchestrator(tmp_path / "applied")
    packages.history.record["status"]="APPLIED"
    assert service.execute(42).status == "NOT_APPLICATION_ELIGIBLE"
    service, packages, browser=orchestrator(tmp_path / "wrong")
    packages.package.resume_vacancy_identity="other"
    assert service.execute(42).status == "PACKAGE_REFRESH_REQUIRED"


@pytest.mark.parametrize("priority", ["C", "D", "E"])
def test_non_ab_intelligence_priority_blocks_execution(tmp_path, priority):
    """Task 21.17C: intelligence_priority is authoritative -- C/D/E must
    never reach execution regardless of legacy decision/remote_eligibility
    fields (both still say AUTO_APPLY/ELIGIBLE in the shared `record()`
    fixture)."""
    service, packages, browser = orchestrator(tmp_path / priority)
    packages.history.record["intelligence_priority"] = priority
    assert service.execute(42).status == "NOT_APPLICATION_ELIGIBLE"
    assert browser.preview_calls == 0


def test_priority_a_is_authorized_same_as_b(tmp_path):
    service, packages, _ = orchestrator(tmp_path)
    packages.history.record["intelligence_priority"] = "A"
    assert service.execute(42, "INSPECT_ONLY").status == "READY_FOR_PREPARATION"


def test_missing_intelligence_priority_fails_closed_even_with_legacy_auto_apply(tmp_path):
    """Task 21.17C: execution must never treat the legacy decision/
    remote_eligibility fields as authorization on their own -- a record with
    no intelligence_priority at all (e.g. a malformed or never-evaluated
    record) is blocked, even though the legacy fields say AUTO_APPLY/ELIGIBLE."""
    service, packages, browser = orchestrator(tmp_path)
    del packages.history.record["intelligence_priority"]
    assert service.execute(42).status == "NOT_APPLICATION_ELIGIBLE"
    assert browser.preview_calls == 0


def test_prepare_for_human_review_package_still_blocks_execution(tmp_path):
    """Task 21.24C: a package built via the new PREPARE_FOR_HUMAN_REVIEW
    package-preparation bypass (readiness=HUMAN_REVIEW_REQUIRED, fully
    document/route/answer-ready otherwise) must still be fully blocked at
    execution -- ApplicationExecutionOrchestrator gates purely on
    intelligence_priority (still "C" here) via the unchanged, shared
    application_eligibility_policy.intelligence_priority_gate(), and never
    consults package_gate/readiness at all."""
    service, packages, browser = orchestrator(
        tmp_path, readiness="HUMAN_REVIEW_REQUIRED",
    )
    packages.history.record["intelligence_priority"] = "C"
    packages.history.record["package_gate"] = "PREPARE_FOR_HUMAN_REVIEW"
    assert service.execute(42).status == "NOT_APPLICATION_ELIGIBLE"
    assert browser.preview_calls == 0


def test_unrecognized_intelligence_priority_fails_closed(tmp_path):
    service, packages, browser = orchestrator(tmp_path)
    packages.history.record["intelligence_priority"] = "NOT_A_REAL_PRIORITY"
    assert service.execute(42).status == "NOT_APPLICATION_ELIGIBLE"
    assert browser.preview_calls == 0


def test_source_only_job33_style_route_resolves_once_then_requires_direct_route(tmp_path):
    service, packages, browser=orchestrator(tmp_path, application_url="https://linkedin.com/jobs/view/33", application_portal="UNKNOWN", route_confidence="LOW", portal_capability="MANUAL_WEB", readiness="MANUAL_WEB_REQUIRED")
    packages.history.record.update(source_listing_url="https://linkedin.com/jobs/view/33", application_url="")
    result=service.execute(42, "INSPECT_ONLY")
    assert result.status == "DIRECT_ROUTE_REQUIRED" and browser.route_calls == 1 and browser.preview_calls == 0


def test_additional_documents_are_not_mapped_and_missing_ready_documents_refresh_package(tmp_path):
    supporting=SAFE_FORM.replace("<button>Next", '<label for="portfolio">Portfolio</label><input id="portfolio" type="file" required><button>Next')
    result=orchestrator(tmp_path / "supporting", supporting)[0].execute(42, "PREPARE")
    assert result.status == "MANUAL_INPUT_REQUIRED" and result.resume_uploaded
    service, packages, _=orchestrator(tmp_path / "stale-cover")
    Path(packages.package.cover_letter_path).unlink()
    assert service.execute(42).status == "PACKAGE_REFRESH_REQUIRED"


def test_multistep_greenhouse_and_lever_progress_to_final_review_without_submit(tmp_path):
    page1='<form class="greenhouse"><label for="email">Email address</label><input id="email" required><button>Next</button></form>'
    page2='<form class="greenhouse"><label for="notice">What is your notice period?</label><input id="notice" required><label for="cv">Resume/CV</label><input id="cv" type="file" required><label for="cover">Cover letter</label><input id="cover" type="file"><label for="extra">Optional unknown</label><input id="extra"><button>Continue</button></form>'
    review='<html>Review your application<form><button>Submit Application</button></form></html>'
    for portal in ("GREENHOUSE","LEVER"):
        pkg=package(tmp_path / portal); pkg.application_portal=portal; pkg.application_url=f"https://jobs.{portal.lower()}.io/example/1"
        packages=Packages(record(), pkg); browser=ProgressBrowser([page1,page2,review])
        result=ApplicationExecutionOrchestrator(packages,browser,tmp_path / portal / "runs").execute(42,"PROGRESS")
        assert result.status == "PREPARED_FOR_FINAL_REVIEW" and result.navigation_actions == 2
        assert result.resume_uploaded and result.cover_letter_uploaded and result.final_submit_detected
        assert packages.history.record["status"] == "MANUAL_WEB_REQUIRED"


@pytest.mark.parametrize("status", ["CAPTCHA_REQUIRED","AUTH_REQUIRED","MFA_REQUIRED","ACCOUNT_CREATION_REQUIRED","LOOP_DETECTED","NAVIGATION_UNCERTAIN","MAX_PAGES_EXCEEDED","MAX_NAVIGATION_ACTIONS_EXCEEDED"])
def test_progression_blockers_and_bounds_are_preserved(tmp_path, status):
    pkg=package(tmp_path / status); packages=Packages(record(),pkg); browser=ProgressBrowser([SAFE_FORM],status,0)
    result=ApplicationExecutionOrchestrator(packages,browser,tmp_path / status / "runs").execute(42,"PROGRESS")
    assert result.status == status and not result.final_submit_detected is False


def test_final_legal_attestation_requires_manual_confirmation_and_resume_is_safe(tmp_path):
    pkg=package(tmp_path); packages=Packages(record(),pkg); browser=ProgressBrowser([LEGAL_FORM],"PREPARED_FOR_FINAL_REVIEW",0)
    service=ApplicationExecutionOrchestrator(packages,browser,tmp_path / "runs")
    first=service.execute(42,"PROGRESS")
    assert first.status == "FINAL_REVIEW_MANUAL_CONFIRMATION_REQUIRED" and first.manual_review_fields
    resumed=service.resume(first.execution_id)
    assert resumed.status == "FINAL_REVIEW_MANUAL_CONFIRMATION_REQUIRED"
