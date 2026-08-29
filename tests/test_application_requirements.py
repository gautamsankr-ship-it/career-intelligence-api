"""Task 21.11: deterministic, evidence-based extraction of what an employer
actually requires (resume / cover letter / supporting statement / custom
written response / application email), never inferred from silence. Pure
text-analysis, fully hermetic -- no network, no production data."""

import pytest

from app.services.application_requirements import ApplicationRequirementService, MANUAL_REVIEW


@pytest.fixture
def service() -> ApplicationRequirementService:
    return ApplicationRequirementService()


def test_case1_cv_and_cover_letter(service):
    result = service.analyze("Please submit your CV and cover letter.")
    assert result.resume_required is True
    assert result.cover_letter_required is True
    assert result.confidence == "HIGH"


def test_case2_resume_plus_custom_response_via_email(service):
    result = service.analyze(
        "Email your resume and a maximum 200-word response explaining why you are suitable."
    )
    assert result.resume_required is True
    assert len(result.custom_responses) == 1
    assert result.custom_responses[0].max_words == 200
    assert result.email_required is True
    assert result.cover_letter_required is False


def test_case3_resume_only(service):
    result = service.analyze("Upload your resume.")
    assert result.resume_required is True
    assert result.cover_letter_required is False
    assert not result.custom_responses


def test_case4_supporting_statement(service):
    result = service.analyze(
        "Please provide a supporting statement addressing the selection criteria."
    )
    assert result.supporting_statement_required is True


def test_case5_ambiguous_instructions_require_manual_review(service):
    result = service.analyze("Please apply through our careers portal.")
    assert result.confidence == MANUAL_REVIEW
    assert result.needs_human_review is True
    assert result.resume_required is False
    assert result.cover_letter_required is False
    assert not result.custom_responses


def test_no_document_requirement_is_invented_for_manual_review_case(service):
    """Nothing gets asserted True just because a document is usually implied."""
    result = service.analyze("")
    assert result.confidence == MANUAL_REVIEW
    assert result.resume_required is False


def test_structured_application_method_is_stronger_evidence_than_text_guessing(service):
    result = service.analyze("General role description with no apply instructions at all.",
                              application_method="EMAIL", recipient_email="jobs@example.com")
    assert result.email_required is True
    assert result.recipient_email == "jobs@example.com"
    assert any("application_method already resolved" in e for e in result.evidence)


def test_word_limit_hyphenated_variant_is_recognised(service):
    result = service.analyze("Submit a maximum 150-word statement of interest.")
    assert result.custom_responses[0].max_words == 150


def test_envision_vacancy_real_wording_resume_and_custom_response_no_cover_letter(service):
    """Mirrors the real EnVision Partners "How to Apply" wording -- synthetic
    reproduction of the captured text, not a read of production tracker data."""
    text = (
        "How to Apply\n"
        "To apply, please send an email to Sarah at hr@envision.com.au\n\n"
        "Subject line:\nEnVision - Your Name\n\n"
        "Attach your resume and answer the following question in 200 words or less:\n"
        "Tell us what you can bring to EnVision Partners and why you believe you'd be a great fit for our team.\n"
        "Applications that do not follow these instructions will not be considered."
    )
    result = service.analyze(text, application_method="EMAIL", recipient_email="hr@envision.com.au")
    assert result.resume_required is True
    assert result.cover_letter_required is False
    assert len(result.custom_responses) == 1
    assert result.custom_responses[0].max_words == 200
    assert result.email_required is True
    assert result.recipient_email == "hr@envision.com.au"
    assert result.subject_instruction is not None and "EnVision" in result.subject_instruction
    assert result.recipient_contact_name == "Sarah"


def test_contact_name_absent_when_not_evidenced(service):
    result = service.analyze("Please email your resume to jobs@example.com.")
    assert result.recipient_contact_name is None
