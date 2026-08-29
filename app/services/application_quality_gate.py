"""Automated pre-human-review quality checks (Task 21.11 Addendum section
17). These never block generation -- a package is always still "prepared,
not submitted" -- they simply attach a structured pass/fail report to the
manifest so a human reviewer sees issues before deciding to send anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import docx

_FORBIDDEN_RESUME_STRINGS = (
    "ATS Optimization Summary",
    "Optimized Resume",
    "Target Position",
    "**",
    "{",
    "'company':",
    '"company":',
)

_PLACEHOLDER_PATTERN = re.compile(
    r"\[(company|role|job title|your name|insert|placeholder)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class QualityGateResult:
    passed: bool
    issues: tuple[str, ...] = field(default_factory=tuple)


def check_resume(docx_path: str) -> QualityGateResult:
    document = docx.Document(docx_path)
    texts = [p.text for p in document.paragraphs if p.text.strip()]
    full_text = "\n".join(texts)

    issues: list[str] = []
    for forbidden in _FORBIDDEN_RESUME_STRINGS:
        if any(forbidden in t for t in texts):
            issues.append(f"contains internal/malformed marker: {forbidden!r}")

    if not texts:
        issues.append("resume document is empty")

    if len(full_text) > 12000:
        issues.append("resume content is unusually long -- review length before sending")

    return QualityGateResult(passed=not issues, issues=tuple(issues))


def check_cover_letter(text: str, employer_name: str, role_title: str) -> QualityGateResult:
    issues: list[str] = []
    if not text or not text.strip():
        issues.append("cover letter is empty")
        return QualityGateResult(passed=False, issues=tuple(issues))

    word_count = len(text.split())
    if word_count < 150 or word_count > 550:
        issues.append(f"cover letter word count ({word_count}) is outside the expected ~300-450 word range")

    if employer_name and employer_name != "the employer" and employer_name not in text:
        issues.append(f"employer name {employer_name!r} does not appear in the cover letter")

    if role_title and role_title not in text:
        issues.append(f"role title {role_title!r} does not appear in the cover letter")

    if _PLACEHOLDER_PATTERN.search(text):
        issues.append("cover letter appears to contain unresolved placeholder text")

    return QualityGateResult(passed=not issues, issues=tuple(issues))


def check_custom_response(text: str, max_words: int) -> QualityGateResult:
    issues: list[str] = []
    if not text or not text.strip():
        issues.append("custom response is empty")
        return QualityGateResult(passed=False, issues=tuple(issues))

    word_count = len(text.split())
    if word_count > max_words:
        issues.append(f"custom response ({word_count} words) exceeds the {max_words}-word limit")

    return QualityGateResult(passed=not issues, issues=tuple(issues))


def check_email(
    body: str,
    recipient: str | None,
    subject: str | None,
    sender: str,
    expected_sender: str,
) -> QualityGateResult:
    issues: list[str] = []
    if not recipient:
        issues.append("email has no recipient")
    if not subject or not subject.strip():
        issues.append("email has no subject")
    if not body or not body.strip():
        issues.append("email body is empty")
    if sender != expected_sender:
        issues.append(f"email sender {sender!r} does not match the expected sender identity {expected_sender!r}")
    if _PLACEHOLDER_PATTERN.search(body or ""):
        issues.append("email body appears to contain unresolved placeholder text")

    return QualityGateResult(passed=not issues, issues=tuple(issues))
