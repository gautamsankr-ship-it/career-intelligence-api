"""Determine what application materials an employer actually requires from
the vacancy/application instructions, before any document is generated.

Deterministic and evidence-preserving, mirroring the existing
RemoteWorkEligibilityClassifier design: explicit phrase evidence only, never
inferred from silence, and a conservative MANUAL_REVIEW fallback when the
text gives no usable signal. This exists specifically so a generic
application-preparation pipeline never manufactures a document (e.g. a
cover letter) the employer never asked for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


HIGH = "HIGH"
MANUAL_REVIEW = "MANUAL_REVIEW"

_RESUME_PATTERN = re.compile(
    r"\b(resume|r[ée]sum[ée]|cv|curriculum vitae)\b", re.IGNORECASE
)
_COVER_LETTER_PATTERN = re.compile(r"\bcover letter\b", re.IGNORECASE)
_SUPPORTING_STATEMENT_PATTERN = re.compile(
    r"\bsupporting statement\b|\baddress(?:ing)? the selection criteria\b",
    re.IGNORECASE,
)
_EMAIL_VERB_PATTERN = re.compile(
    r"\bemail (?:your|us|it|the|this)\b|\bsend .{0,40}\bemail\b|\bapply by email\b|\bemail your application\b",
    re.IGNORECASE,
)
_EMAIL_ADDRESS_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SUBJECT_LINE_PATTERN = re.compile(
    r"subject\s*(?:line)?\s*:?\s*\n*\s*(.+)", re.IGNORECASE
)
_CONTACT_NAME_PATTERN = re.compile(r"\b(?:to|email|contact)\s+([A-Z][a-zA-Z]+)\s+at\b")
_WORD_LIMIT_PATTERN = re.compile(
    r"(?:a\s+)?(?:maximum|max\.?|no more than|up to)\s+(\d+)[\s-]*words?"
    r"|(\d+)[\s-]*words?\s+or\s+(?:less|fewer)"
    r"|(\d+)[\s-]*words?\b",
    re.IGNORECASE,
)


def _find_word_limit(text: str) -> int | None:
    match = _WORD_LIMIT_PATTERN.search(text)
    if not match:
        return None
    for group in match.groups():
        if group:
            return int(group)
    return None


def _extract_prompt_sentence(text: str) -> str:
    """Best-effort extraction of the actual custom-response question/prompt.
    Prefers an explicit quoted/colon-introduced question; falls back to the
    sentence containing the word-limit mention."""
    colon_match = re.search(
        r"(?:answer the following question|tell us|question)\s*[:\-]?\s*\n*\s*(.+)",
        text,
        re.IGNORECASE,
    )
    if colon_match:
        candidate = colon_match.group(1).strip()
        end = candidate.find("\n\n")
        if end != -1:
            candidate = candidate[:end].strip()
        if candidate:
            return candidate.rstrip(".") + ("" if candidate.endswith(("?", ".")) else ".")

    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        if _WORD_LIMIT_PATTERN.search(sentence):
            return sentence.strip()
    return text.strip()


@dataclass(frozen=True)
class CustomResponsePrompt:
    prompt: str
    max_words: int | None
    evidence: str


@dataclass(frozen=True)
class ApplicationRequirements:
    resume_required: bool = False
    cover_letter_required: bool = False
    supporting_statement_required: bool = False
    custom_responses: tuple[CustomResponsePrompt, ...] = field(default_factory=tuple)
    email_required: bool = False
    recipient_email: str | None = None
    recipient_contact_name: str | None = None
    subject_instruction: str | None = None
    confidence: str = MANUAL_REVIEW
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_human_review(self) -> bool:
        return self.confidence == MANUAL_REVIEW


class ApplicationRequirementService:
    """Analyzes authoritative vacancy/application-instruction text to decide
    which employer-facing materials are actually required. Prefers, in
    order: explicit employer instructions in the text > structured
    route/application fields already resolved for the vacancy (application
    method, recipient email) > nothing else -- it never infers a document
    requirement it can't point to evidence for."""

    def analyze(
        self,
        job_description: str,
        application_method: str | None = None,
        recipient_email: str | None = None,
    ) -> ApplicationRequirements:
        text = job_description or ""
        evidence: list[str] = []

        resume_match = _RESUME_PATTERN.search(text)
        resume_required = bool(resume_match)
        if resume_match:
            evidence.append(f"resume: matched {resume_match.group(0)!r}")

        cover_letter_match = _COVER_LETTER_PATTERN.search(text)
        cover_letter_required = bool(cover_letter_match)
        if cover_letter_match:
            evidence.append("cover_letter: matched 'cover letter'")

        supporting_match = _SUPPORTING_STATEMENT_PATTERN.search(text)
        supporting_statement_required = bool(supporting_match)
        if supporting_match:
            evidence.append(f"supporting_statement: matched {supporting_match.group(0)!r}")

        custom_responses: list[CustomResponsePrompt] = []
        max_words = _find_word_limit(text)
        if max_words is not None:
            prompt = _extract_prompt_sentence(text)
            custom_responses.append(
                CustomResponsePrompt(prompt=prompt, max_words=max_words, evidence=text.strip())
            )
            evidence.append(f"custom_response: word-limit instruction found ({max_words} words)")

        # Structured route data (application_method) is stronger evidence than
        # a text-pattern guess, per the documented evidence hierarchy.
        email_from_method = (application_method or "").strip().upper() == "EMAIL"
        email_verb_match = _EMAIL_VERB_PATTERN.search(text)
        email_required = email_from_method or bool(email_verb_match)
        if email_from_method:
            evidence.append("email: application_method already resolved to EMAIL")
        elif email_verb_match:
            evidence.append(f"email: matched {email_verb_match.group(0)!r}")

        resolved_recipient = recipient_email
        if not resolved_recipient:
            address_match = _EMAIL_ADDRESS_PATTERN.search(text)
            if address_match:
                resolved_recipient = address_match.group(0)
                evidence.append(f"recipient_email: found {resolved_recipient!r} in text")
        elif recipient_email:
            evidence.append(f"recipient_email: from resolved application route ({recipient_email})")

        subject_instruction = None
        subject_match = _SUBJECT_LINE_PATTERN.search(text)
        if subject_match:
            subject_instruction = subject_match.group(1).strip().splitlines()[0].strip()
            evidence.append(f"subject_instruction: found {subject_instruction!r}")

        contact_name = None
        contact_match = _CONTACT_NAME_PATTERN.search(text)
        if contact_match:
            contact_name = contact_match.group(1)
            evidence.append(f"recipient_contact_name: found {contact_name!r}")

        any_signal = (
            resume_required
            or cover_letter_required
            or supporting_statement_required
            or bool(custom_responses)
            or email_required
        )

        if not any_signal:
            return ApplicationRequirements(
                confidence=MANUAL_REVIEW,
                evidence=("No explicit application-material instructions found in the vacancy text.",),
            )

        return ApplicationRequirements(
            resume_required=resume_required,
            cover_letter_required=cover_letter_required,
            supporting_statement_required=supporting_statement_required,
            custom_responses=tuple(custom_responses),
            email_required=email_required,
            recipient_email=resolved_recipient,
            recipient_contact_name=contact_name,
            subject_instruction=subject_instruction,
            confidence=HIGH,
            evidence=tuple(evidence),
        )
