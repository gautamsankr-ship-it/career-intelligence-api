"""Task 21.11 Addendum section 17: automated pre-human-review quality gate.
Hermetic -- writes only to tmp_path, no production data."""

import docx

from app.services.application_quality_gate import (
    check_cover_letter,
    check_custom_response,
    check_email,
    check_resume,
)


def _write_resume_docx(path, paragraphs):
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(path)
    return str(path)


def test_check_resume_passes_clean_document(tmp_path):
    path = _write_resume_docx(tmp_path / "Resume.docx", ["Jane Candidate", "Senior Accountant", "Managed reporting."])
    result = check_resume(path)
    assert result.passed is True
    assert result.issues == ()


def test_check_resume_flags_internal_markers(tmp_path):
    path = _write_resume_docx(tmp_path / "Resume.docx", ["**Optimized Resume**", "ATS Optimization Summary"])
    result = check_resume(path)
    assert result.passed is False
    assert any("Optimized Resume" in issue for issue in result.issues)


def test_check_cover_letter_flags_missing_employer_and_role():
    text = "Dear Hiring Manager, I am writing to apply. " * 20
    result = check_cover_letter(text, employer_name="Acme Partners", role_title="Finance Manager")
    assert result.passed is False
    assert any("Acme Partners" in issue for issue in result.issues)
    assert any("Finance Manager" in issue for issue in result.issues)


def test_check_cover_letter_passes_when_grounded_and_reasonable_length():
    body = " ".join(["Grounded relevant sentence about Acme Partners and the Finance Manager role."] * 20)
    result = check_cover_letter(body, employer_name="Acme Partners", role_title="Finance Manager")
    assert result.passed is True


def test_check_custom_response_flags_over_limit():
    result = check_custom_response("word " * 250, max_words=200)
    assert result.passed is False
    assert any("exceeds" in issue for issue in result.issues)


def test_check_custom_response_passes_within_limit():
    result = check_custom_response("word " * 100, max_words=200)
    assert result.passed is True


def test_check_email_flags_wrong_sender_and_missing_recipient():
    result = check_email(
        body="Hi there, applying.", recipient=None, subject="Application",
        sender="wrong@example.com", expected_sender="gautamsankr@gmail.com",
    )
    assert result.passed is False
    assert any("recipient" in issue for issue in result.issues)
    assert any("sender" in issue for issue in result.issues)


def test_check_email_passes_when_correct():
    result = check_email(
        body="Hi Sarah, I am writing to apply.", recipient="hr@example.com", subject="Application",
        sender="gautamsankr@gmail.com", expected_sender="gautamsankr@gmail.com",
    )
    assert result.passed is True
