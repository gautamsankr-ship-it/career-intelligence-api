"""Task 21.30 Section 1: which sibling file (PDF/DOCX) a channel should
receive -- the priority-order policy, never document content itself."""

from app.services.resume_document_format_policy import DOCX, PDF, select_document_format


def test_explicit_portal_requirement_wins_over_everything():
    assert select_document_format(required_format="pdf", pdf_available=True) == PDF
    assert select_document_format(required_format="DOCX", pdf_available=True) == DOCX
    assert select_document_format(required_format="doc", pdf_available=True) == DOCX


def test_single_supported_format_from_portal_is_used():
    assert select_document_format(accepted_formats=("PDF",)) == PDF
    assert select_document_format(accepted_formats=("DOCX",)) == DOCX


def test_portal_accepting_both_falls_back_to_default_preference():
    assert select_document_format(accepted_formats=("PDF", "DOCX")) == PDF


def test_linkedin_easy_apply_and_direct_email_prefer_pdf_by_default():
    assert select_document_format() == PDF


def test_pdf_unavailable_falls_back_to_docx():
    assert select_document_format(pdf_available=False) == DOCX
    # Even an explicit PDF requirement cannot be honored if the sibling was
    # never generated -- the caller is expected to check `pdf_available`
    # before invoking a hard PDF requirement in practice, but the default,
    # unconstrained path must never claim PDF availability it doesn't have.
    assert select_document_format(pdf_available=False, accepted_formats=("PDF", "DOCX")) == DOCX
