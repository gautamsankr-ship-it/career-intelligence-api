from types import SimpleNamespace

import pytest

from app.models.career_opportunity import CareerOpportunity
from app.services.application_history_service import ApplicationHistoryService, fingerprint_for_opportunity
from app.services.auto_application_orchestrator import AutoApplicationOrchestrator
from app.services.remote_work_eligibility import ELIGIBLE, INELIGIBLE, MANUAL_REVIEW, NOT_APPLICABLE, RemoteWorkEligibilityClassifier


def remote(description, market="united_kingdom"):
    return CareerOpportunity(job_title="Finance Manager", job_description=description, work_arrangement="REMOTE", remote_status=True, market=market)


@pytest.mark.parametrize("text,expected", [
    ("Work from anywhere globally.", ELIGIBLE), ("Worldwide remote role.", ELIGIBLE),
    ("Remote from Nepal is accepted.", ELIGIBLE), ("UK residents only.", INELIGIBLE),
    ("Must be based in the UK.", INELIGIBLE), ("Australia-based applicants only.", INELIGIBLE),
    ("Remote anywhere in the US.", INELIGIBLE), ("Must have the right to work in the UK.", INELIGIBLE),
    ("US work authorization required.", INELIGIBLE), ("Full Australian working rights required.", INELIGIBLE),
    ("US citizen required.", INELIGIBLE), ("UK security clearance required.", INELIGIBLE),
    ("Must work UK business hours.", MANUAL_REVIEW), ("Remote contractor engagement.", MANUAL_REVIEW),
    ("International contractors accepted; contractors may work from any country.", ELIGIBLE),
    ("This is a fully remote role.", MANUAL_REVIEW),
    ("Global company with a global remote company culture. UK residents only.", INELIGIBLE),
])
def test_vacancy_level_eligibility_evidence(text, expected):
    assert RemoteWorkEligibilityClassifier().classify(remote(text)).decision == expected


def test_search_market_and_employer_location_do_not_create_false_restrictions():
    assert RemoteWorkEligibilityClassifier().classify(remote("Worldwide remote", "united_states")).decision == ELIGIBLE
    assert RemoteWorkEligibilityClassifier().classify(remote("Fully remote role", "united_kingdom")).decision == MANUAL_REVIEW
    nonremote = CareerOpportunity(job_title="Analyst", job_description="Work from anywhere", work_arrangement="ON_SITE")
    assert RemoteWorkEligibilityClassifier().classify(nonremote).decision == NOT_APPLICABLE


@pytest.mark.parametrize("text,expected", [
    ("Worldwide remote. International contractors accepted from any country. No visa sponsorship available.", ELIGIBLE),
    ("Remote role. Must already have UK work authorization. No sponsorship available.", INELIGIBLE),
    ("Fully remote. No sponsorship available.", MANUAL_REVIEW),
])
def test_no_sponsorship_is_contextual_not_a_standalone_remote_location_restriction(text, expected):
    assert RemoteWorkEligibilityClassifier().classify(remote(text)).decision == expected


class FakeApp:
    def __init__(self): self.docs = 0
    def evaluate_job(self, description):
        return SimpleNamespace(profile={"candidate": {"full_name": "Candidate"}}, job_analysis={}, career_decision=SimpleNamespace(overall_score=85), ats_result={"ats_score": {"overall_score": 80}}, screening_decision="AUTO_APPLY")
    def generate_application_documents(self, evaluation): self.docs += 1; raise AssertionError("documents must not be generated")


class FakeGmail:
    def __init__(self): self.calls = 0
    def create_draft_for_application(self, *args, **kwargs): self.calls += 1; raise AssertionError("gmail must not be called")


@pytest.mark.parametrize("text,status,eligibility", [
    ("Remote in the UK only. Send your CV to jobs@example.com", "REMOTE_INELIGIBLE", INELIGIBLE),
    ("Fully remote role. Send your CV to jobs@example.com", "REMOTE_ELIGIBILITY_REVIEW", MANUAL_REVIEW),
])
def test_auto_apply_is_blocked_before_documents_or_gmail_for_ineligible_or_unclear_remote(tmp_path, text, status, eligibility):
    job = remote(text)
    job.id = status
    app, gmail = FakeApp(), FakeGmail()
    with ApplicationHistoryService(tmp_path / "history.db") as history:
        runner = AutoApplicationOrchestrator(application_service=app, gmail_service=gmail, history_service=history)
        runner.run([job])
        record = history.get_record(fingerprint_for_opportunity(job))
        assert record["status"] == status
        assert record["remote_eligibility"] == eligibility
        assert app.docs == 0 and gmail.calls == 0
