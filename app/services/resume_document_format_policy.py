"""Task 21.30 Section 1: which already-generated sibling file (PDF or DOCX)
a given application channel should actually receive.

Every tailored package now retains BOTH an ATS-safe DOCX and a text-based
ATS-safe PDF, generated from the SAME approved resume/cover-letter content
(app/services/docx_service.py). This module never generates or rewrites
that content -- it only picks which existing file to submit, per the
candidate-approved priority order:

  1. Employer/portal explicitly requires PDF -> PDF
  2. Employer/portal explicitly requires DOC/DOCX -> DOCX
  3. Portal accepts only one supported format -> that format
  4. LinkedIn Easy Apply accepts both -> prefer PDF
  5. Direct recruiter/email application -> prefer PDF
  6. PDF cannot be generated/is unavailable -> DOCX fallback
"""
from __future__ import annotations

PDF = "PDF"
DOCX = "DOCX"


def select_document_format(
    *,
    required_format: str | None = None,
    accepted_formats: tuple[str, ...] | None = None,
    pdf_available: bool = True,
) -> str:
    required = (required_format or "").strip().upper()
    if required == "PDF":
        return PDF
    if required in {"DOC", "DOCX"}:
        return DOCX

    if accepted_formats:
        supported = {f.strip().upper() for f in accepted_formats if f.strip().upper() in {"PDF", "DOC", "DOCX"}}
        if len(supported) == 1:
            return PDF if next(iter(supported)) == "PDF" else DOCX

    # Tiers 4/5: LinkedIn Easy Apply and direct recruiter/email both default
    # to PDF when nothing more specific constrains the choice.
    if not pdf_available:
        return DOCX
    return PDF
