"""Deterministic application-email detection for vacancy content.

This module intentionally does not send email or call external services.  An
address is eligible only when nearby vacancy text explicitly authorizes an
application, CV, or resume submission to that address.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Iterable


class EmailClassification(str, Enum):
    EXPLICIT_APPLICATION_EMAIL = "EXPLICIT_APPLICATION_EMAIL"
    CONTACT_ONLY_EMAIL = "CONTACT_ONLY_EMAIL"
    NO_EMAIL = "NO_EMAIL"
    WEB_APPLICATION_ONLY = "WEB_APPLICATION_ONLY"


EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+(?![\w-])"
)

APPLICATION_PATTERNS = (
    r"send (?:your )?(?:cv|resume|application)(?:\s+\w+){0,5}\s+to",
    r"email (?:your )?(?:cv|resume|application)(?:\s+\w+){0,5}\s+to",
    r"apply (?:by|via) email(?:\s+\w+){0,5}(?:at|to)",
    r"applications?\s+to",
    r"applications?\s+(?:should be )?(?:sent|submitted|emailed)\s+to",
    r"submit (?:your )?(?:cv|resume|application)(?:\s+\w+){0,5}\s+to",
    r"interested candidates? should email (?:their )?(?:cv|resume|application)",
)
CONTACT_PATTERNS = (
    r"for (?:general )?(?:enquiries|inquiries|questions|more information)",
    r"confidential discussion",
    r"\bcontact\b",
    r"\bfraud\b",
    r"\bsecurity\b",
    r"\bprivacy\b",
    r"\bsupport\b",
    r"general (?:enquiries|inquiries)",
)
WEB_APPLICATION_PATTERNS = (
    r"apply (?:via|through) (?:our )?(?:careers? portal|website|linkedin)",
    r"apply online(?:\s+at)?",
    r"submit your application (?:using|through|via) (?:the )?(?:link|portal|website)",
    r"(?:careers?|application) portal",
)
GENERAL_MAILBOXES = {"info", "contact", "hello", "enquiries", "inquiries", "support", "security", "privacy", "fraud"}


@dataclass(frozen=True)
class DetectedEmail:
    address: str
    classification: EmailClassification
    context: str
    strength: int = 0


@dataclass(frozen=True)
class ApplicationEmailResult:
    classification: EmailClassification
    emails: tuple[DetectedEmail, ...]
    selected_email: str | None = None


def _contains_any(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _string_values(value: Any) -> Iterable[str]:
    """Yield textual values from raw descriptions, analysis, and metadata."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _string_values(nested)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            yield from _string_values(nested)


class ApplicationEmailClassifier:
    """Classify vacancy addresses using only deterministic nearby context."""

    def classify(self, *sources: Any) -> ApplicationEmailResult:
        texts = tuple(text for source in sources for text in _string_values(source))
        candidates: dict[str, DetectedEmail] = {}

        for text in texts:
            for match in EMAIL_PATTERN.finditer(text):
                address = match.group(0)
                key = address.lower()
                context = self._nearby_context(text, match.start(), match.end())
                detected = self._classify_address(address, context)
                current = candidates.get(key)
                if current is None or detected.strength > current.strength:
                    candidates[key] = detected

        emails = tuple(candidates.values())
        explicit = [
            email for email in emails
            if email.classification == EmailClassification.EXPLICIT_APPLICATION_EMAIL
        ]
        if explicit:
            selected = max(explicit, key=lambda email: email.strength)
            return ApplicationEmailResult(
                EmailClassification.EXPLICIT_APPLICATION_EMAIL,
                emails,
                selected.address,
            )
        if emails:
            return ApplicationEmailResult(EmailClassification.CONTACT_ONLY_EMAIL, emails)
        combined_text = "\n".join(texts)
        if _contains_any(WEB_APPLICATION_PATTERNS, combined_text):
            return ApplicationEmailResult(EmailClassification.WEB_APPLICATION_ONLY, emails)
        return ApplicationEmailResult(EmailClassification.NO_EMAIL, emails)

    @staticmethod
    def _nearby_context(text: str, start: int, end: int) -> str:
        """Use the containing sentence/list item to avoid intent bleed-over."""
        before_start = max(0, start - 180)
        before = text[before_start:start]
        after = text[end:min(len(text), end + 120)]
        left_boundary = max(before.rfind("."), before.rfind("!"), before.rfind("?"), before.rfind("\n"))
        right_candidates = [index for index in (after.find("."), after.find("!"), after.find("?"), after.find("\n")) if index >= 0]
        left = before_start + left_boundary + 1
        right = end + (min(right_candidates) if right_candidates else len(after))
        return text[left:right]

    def classify_opportunity(self, opportunity, job_analysis: Any = None) -> ApplicationEmailResult:
        """Inspect all in-scope vacancy sources without deriving any address."""
        return self.classify(
            opportunity.job_description,
            job_analysis,
            opportunity.metadata or {},
        )

    @staticmethod
    def _classify_address(address: str, context: str) -> DetectedEmail:
        normalized_context = context.lower()
        local_part = address.partition("@")[0].lower()
        # General/support mailboxes are never automated recipients, even if a
        # surrounding page happens to contain an application phrase.
        if local_part in GENERAL_MAILBOXES or _contains_any(CONTACT_PATTERNS, normalized_context):
            return DetectedEmail(address, EmailClassification.CONTACT_ONLY_EMAIL, context)
        for index, pattern in enumerate(APPLICATION_PATTERNS, start=1):
            if re.search(pattern, normalized_context, flags=re.IGNORECASE):
                return DetectedEmail(
                    address,
                    EmailClassification.EXPLICIT_APPLICATION_EMAIL,
                    context,
                    strength=len(APPLICATION_PATTERNS) - index + 1,
                )
        return DetectedEmail(address, EmailClassification.CONTACT_ONLY_EMAIL, context)
