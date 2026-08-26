from types import SimpleNamespace

import pytest

from app.services.application_email_classifier import (
    ApplicationEmailClassifier,
    EmailClassification,
)
from app.services.application_history_service import ApplicationHistoryService


@pytest.fixture
def classifier():
    return ApplicationEmailClassifier()


@pytest.mark.parametrize(
    "text, expected_email",
    [
        ("Send your CV to [jobs@example.com](mailto:jobs@example.com)", "jobs@example.com"),
        ("Email your resume to [careers@example.com](mailto:careers@example.com)", "careers@example.com"),
    ],
)
def test_explicit_application_addresses_are_selected(classifier, text, expected_email):
    result = classifier.classify(text)

    assert result.classification == EmailClassification.EXPLICIT_APPLICATION_EMAIL
    assert result.selected_email == expected_email


@pytest.mark.parametrize(
    "text",
    [
        "For a confidential discussion email [recruiter@example.com](mailto:recruiter@example.com)",
        "For questions contact [hr@example.com](mailto:hr@example.com)",
        "Fraud concerns: [security@example.com](mailto:security@example.com)",
    ],
)
def test_contact_addresses_are_never_selected(classifier, text):
    result = classifier.classify(text)

    assert result.classification == EmailClassification.CONTACT_ONLY_EMAIL
    assert result.selected_email is None


def test_web_instruction_without_email_is_web_application_only(classifier):
    assert classifier.classify("Apply through our careers portal").classification == (
        EmailClassification.WEB_APPLICATION_ONLY
    )


def test_no_email_or_web_instruction_is_no_email(classifier):
    assert classifier.classify("Build reporting tools with the finance team.").classification == (
        EmailClassification.NO_EMAIL
    )


def test_multiple_addresses_select_only_the_explicit_application_address(classifier):
    result = classifier.classify(
        "For questions contact recruiter@example.com. "
        "Interested candidates should email their CV to talent@example.com."
    )

    assert result.classification == EmailClassification.EXPLICIT_APPLICATION_EMAIL
    assert result.selected_email == "talent@example.com"
    assert len(result.emails) == 2


def test_explicit_email_wins_over_web_instruction_when_email_submission_is_authorized(classifier):
    result = classifier.classify(
        "Apply through our careers portal, or send your application to hr@example.com."
    )

    assert result.classification == EmailClassification.EXPLICIT_APPLICATION_EMAIL
    assert result.selected_email == "hr@example.com"


def test_malformed_addresses_are_ignored(classifier):
    result = classifier.classify(
        "Send your CV to jobs@example or careers@@example.com or applicant@.com."
    )

    assert result.classification == EmailClassification.NO_EMAIL
    assert result.emails == ()


def test_classifier_reads_raw_description_analysis_and_metadata_without_mutating_score(classifier):
    opportunity = SimpleNamespace(
        job_description="No email in this raw description.",
        metadata={"application_notes": "Send your CV to talent@example.com"},
        raw_score=82.5,
    )

    result = classifier.classify_opportunity(
        opportunity,
        {"submission": "Email your resume to careers@example.com"},
    )

    assert result.classification == EmailClassification.EXPLICIT_APPLICATION_EMAIL
    assert opportunity.raw_score == 82.5


def test_history_persists_explicit_email_route(tmp_path):
    opportunity = SimpleNamespace(
        id="job-1",
        source="LinkedIn",
        job_url="https://example.com/jobs/1",
        company="Example",
        job_title="Analyst",
        location="Remote",
        job_description="Send your CV to jobs@example.com",
        metadata={},
    )
    evaluation = SimpleNamespace(
        ats_result={"ats_score": {"overall_score": 88.0}},
        career_decision=SimpleNamespace(overall_score=82.5),
        screening_decision="AUTO_APPLY",
    )

    with ApplicationHistoryService(tmp_path / "history.db") as history:
        fingerprint, accepted = history.record_evaluation(
            opportunity,
            evaluation,
            "ELIGIBLE",
            application_method="EMAIL",
            recipient_email="jobs@example.com",
        )
        record = history.get_record(fingerprint)

    assert accepted is True
    assert record["career_score"] == 82.5
    assert record["application_method"] == "EMAIL"
    assert record["recipient_email"] == "jobs@example.com"
    assert record["status"] == "ELIGIBLE"
