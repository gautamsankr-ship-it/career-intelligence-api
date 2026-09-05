"""Task 21.34: Gmail Outcome Monitoring.

Connects real Gmail employer/recruiter correspondence to the EXISTING
Application CRM (`OpportunityCRMService`) so application outcomes update
automatically. This module owns no database and no second tracking system --
every persisted fact goes through `OpportunityCRMService`, the same service
Task 21.32/21.33 already built.

Safety, by construction:
  * Gmail access is READ ONLY. `GmailService.list_message_ids` /
    `GmailService.get_message` are the only Gmail calls this module ever
    makes -- both read-only endpoints. Nothing here can send, reply,
    forward, draft, label, archive, trash, or delete anything, and the
    Gmail client is always constructed with the narrow
    `GMAIL_READONLY_SCOPES` / `GMAIL_READONLY_TOKEN_PATH` (never the
    compose-scope token used for drafting/sending applications).
  * A message updates an opportunity's CRM only when matching evidence is
    "sufficient" (see `match_opportunity`): a single weak signal (e.g. the
    company name alone) is never enough. Ambiguous matches (two or more
    opportunities tied on evidence) and UNKNOWN/uncertain classifications
    always route to human review instead of a best guess -- never fabricated.
  * Idempotent: every CRM write this module makes carries the Gmail message
    id as its `evidence_reference`/blocker `detail`. Before acting on a
    message, its id is checked against `opportunity_events.evidence_reference`
    (already the append-only audit trail `OpportunityCRMService` maintains --
    no separate "seen messages" table is introduced) and skipped if already
    recorded.
  * Generic job-alert/newsletter digests (LinkedIn/Indeed job-alert senders,
    "N new jobs" / "job alert" style subjects) are filtered out before any
    opportunity matching is attempted, so they are never mistaken for a real
    employer response.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import GMAIL_READONLY_SCOPES, GMAIL_READONLY_TOKEN_PATH
from app.services.gmail_service import GmailService
from app.services.opportunity_crm_service import ALLOWED_TRANSITIONS, OpportunityCRMService

# --- Classification categories (Task 21.34 scope, exactly) ------------------
# Each of these is already a valid `employer_responses.response_type` per
# `app.models.crm.EMPLOYER_RESPONSE_TYPES`, with one deliberate naming
# exception: the CRM's existing enum/stage-transition map (Task 21.32) calls
# this response "RECRUITER_CONTACT" (its crm_stage is "RECRUITER_RESPONSE").
# This module keeps the task's own vocabulary for classification and
# translates to the CRM's stored value at the single `_EMPLOYER_RESPONSE_TYPE`
# boundary below -- never invents a second, parallel enum in the database.
ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"
RECRUITER_RESPONSE = "RECRUITER_RESPONSE"
SCREENING_REQUEST = "SCREENING_REQUEST"
INTERVIEW_INVITATION = "INTERVIEW_INVITATION"
ASSESSMENT_REQUEST = "ASSESSMENT_REQUEST"
REJECTION = "REJECTION"
OFFER = "OFFER"
UNKNOWN = "UNKNOWN"

CLASSIFICATIONS = (
    ACKNOWLEDGEMENT, RECRUITER_RESPONSE, SCREENING_REQUEST, INTERVIEW_INVITATION,
    ASSESSMENT_REQUEST, REJECTION, OFFER, UNKNOWN,
)

_EMPLOYER_RESPONSE_TYPE = {
    ACKNOWLEDGEMENT: "ACKNOWLEDGEMENT",
    RECRUITER_RESPONSE: "RECRUITER_CONTACT",
    SCREENING_REQUEST: "SCREENING_REQUEST",
    INTERVIEW_INVITATION: "INTERVIEW_INVITATION",
    ASSESSMENT_REQUEST: "ASSESSMENT_REQUEST",
    REJECTION: "REJECTION",
    OFFER: "OFFER",
    UNKNOWN: "UNKNOWN",
}

# --- Match outcomes -----------------------------------------------------
MATCHED = "MATCHED"
AMBIGUOUS = "AMBIGUOUS"
NO_MATCH = "NO_MATCH"

# A single weak signal is never enough (see docstring). Company/domain
# evidence alone scores 3 -- below this -- so it can never match alone;
# company + title, a recruiter contact, a job reference, or thread evidence
# each clear it on their own.
MIN_MATCH_SCORE = 4
# Two or more candidates within this many points of the top score are
# genuinely ambiguous -- never guessed between.
AMBIGUITY_MARGIN = 2

_SCORE_COMPANY = 3
_SCORE_TITLE = 2
_SCORE_JOB_REFERENCE = 4
_SCORE_RECRUITER_CONTACT = 4
_SCORE_THREAD_EVIDENCE = 5

# Corporate-suffix / filler tokens stripped before comparing company names --
# matching on these alone (e.g. two different "... Group" employers) would be
# a false positive, not evidence.
_COMPANY_STOPWORDS = frozenset({
    "inc", "incorporated", "llc", "ltd", "limited", "co", "corp", "corporation",
    "group", "holdings", "international", "plc", "gmbh", "bcorp", "b", "the",
    "company", "companies",
})
_STOPWORDS_GENERIC_TITLE = frozenset({
    "the", "a", "an", "of", "and", "for", "at", "in", "on", "to", "with", "&",
})

# Known bulk job-alert / digest senders and subject phrasing -- filtered out
# before any opportunity matching is attempted, regardless of whether they
# happen to mention a tracked company/title among many listings.
_BULK_SENDER_SUBSTRINGS = (
    "jobalerts-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "jobs-listings@linkedin.com",
    "alerts@indeed.com",
    "jobalert@indeed.com",
    "jobalerts@indeed.com",
    "indeedalerts@indeed.com",
    "notifications@linkedin.com",
)
_BULK_SUBJECT_PATTERNS = (
    r"\bjob alert\b",
    r"\bnew jobs? (for|matching|based on)\b",
    r"\bjobs? for you\b",
    r"\b\d+\+? new jobs?\b",
    r"\brecommended jobs?\b",
    r"\bweekly job digest\b",
    r"\bjobs? you may be interested in\b",
    r"\bpeople are also viewing\b",
)

# --- Classification patterns, checked in this order (most decisive first) --
_REJECTION_PATTERNS = (
    r"\bunfortunately\b",
    r"regret to inform",
    r"will not (?:be )?(?:moving|proceeding) forward",
    r"decided to (?:move forward|proceed) with (?:other|another) candidate",
    r"not (?:be )?(?:selected|successful)\b",
    r"position has been filled",
    r"pursue other candidates",
    r"we have chosen to move forward with other applicants",
)
_OFFER_PATTERNS = (
    r"pleased to offer",
    r"\bjob offer\b",
    r"offer of employment",
    r"extend (?:you )?an offer",
    r"excited to offer you",
    r"formal offer",
    r"offer letter",
)
_INTERVIEW_PATTERNS = (
    r"invite you (?:to|for) (?:an )?interview",
    r"schedule (?:a|an) (?:interview|call) with",
    r"interview invitation",
    r"(?:arrange|set up) (?:a|an) (?:interview|time to (?:meet|chat))",
    r"next step is an interview",
    r"would like to (?:meet|interview) you",
)
_ASSESSMENT_PATTERNS = (
    r"complete (?:the |an |your )?(?:assessment|test|challenge|task|exercise)",
    r"take[- ]home (?:task|assignment|test|challenge)",
    r"\bonline assessment\b",
    r"coding (?:challenge|test)",
    r"skills? test",
    r"please complete.*(?:by|within)",
)
_SCREENING_PATTERNS = (
    r"phone screen",
    r"screening call",
    r"initial (?:call|screen)\b",
    r"recruiter screen",
    r"quick call to (?:discuss|learn more)",
    r"schedule.*screen",
)
_RECRUITER_RESPONSE_PATTERNS = (
    r"reaching out (?:regarding|about|to discuss) your application",
    r"wanted to connect (?:regarding|about) your",
    r"following up (?:regarding|on) your application",
)
_ACKNOWLEDGEMENT_PATTERNS = (
    r"thank(?:s)? .{0,40}for applying",
    r"thank you for (?:your interest|applying)",
    r"application (?:has been |was )?received",
    r"we(?:'| ha)ve received your application",
    r"currently (?:reviewing|under review)",
    r"your application is under review",
)


def _normalize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _company_tokens(company: str) -> set[str]:
    return {
        word for word in _normalize_words(company)
        if len(word) >= 3 and word not in _COMPANY_STOPWORDS
    }


def _title_tokens(title: str) -> set[str]:
    return {
        word for word in _normalize_words(title)
        if len(word) >= 3 and word not in _STOPWORDS_GENERIC_TITLE
    }


def _sender_domain(sender: str) -> str:
    match = re.search(r"@([\w.-]+)", sender or "")
    return (match.group(1) or "").lower() if match else ""


@dataclass(frozen=True)
class EmailContent:
    """A normalized, already-fetched Gmail message. Deliberately holds only
    read data (no send/reply affordances) -- the one shape both the live
    Gmail-API path and a smoke test fed pre-fetched content build."""
    message_id: str
    thread_id: str
    sender: str
    subject: str
    date: str
    snippet: str
    body_text: str
    in_reply_to: str = ""
    references: str = ""

    @property
    def combined_text(self) -> str:
        return "\n".join((self.subject, self.snippet, self.body_text))


@dataclass(frozen=True)
class MatchCandidate:
    tracker_id: int
    company: str
    job_title: str
    score: int
    signals: tuple[str, ...]


@dataclass(frozen=True)
class MatchResult:
    outcome: str  # MATCHED | AMBIGUOUS | NO_MATCH
    candidate: MatchCandidate | None = None
    tied_candidates: tuple[MatchCandidate, ...] = ()


def _extract_headers(payload: dict) -> dict[str, str]:
    headers = {}
    for header in (payload or {}).get("headers", []) or []:
        name = (header.get("name") or "").lower()
        headers[name] = header.get("value") or ""
    return headers


def _decode_body(payload: dict) -> str:
    """Best-effort plain-text extraction from a Gmail API message payload.
    Read-only string decoding -- never touches the message itself."""
    if not payload:
        return ""
    mime_type = payload.get("mimeType", "")
    body_data = (payload.get("body") or {}).get("data")
    if body_data and mime_type in ("text/plain", "") :
        try:
            return base64.urlsafe_b64decode(body_data + "=" * (-len(body_data) % 4)).decode("utf-8", errors="replace")
        except Exception:
            return ""
    for part in payload.get("parts", []) or []:
        if part.get("mimeType") == "text/plain":
            data = (part.get("body") or {}).get("data")
            if data:
                try:
                    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
                except Exception:
                    continue
    for part in payload.get("parts", []) or []:
        nested = _decode_body(part)
        if nested:
            return nested
    return ""


def email_content_from_gmail_api_message(raw: dict) -> EmailContent:
    """Convert a raw `users().messages().get()` response (the live,
    read-only Gmail API shape) into `EmailContent`."""
    payload = raw.get("payload", {}) or {}
    headers = _extract_headers(payload)
    return EmailContent(
        message_id=raw.get("id", ""),
        thread_id=raw.get("threadId", ""),
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        date=headers.get("date", ""),
        snippet=raw.get("snippet", ""),
        body_text=_decode_body(payload),
        in_reply_to=headers.get("in-reply-to", ""),
        references=headers.get("references", ""),
    )


def is_bulk_notification(email: EmailContent) -> bool:
    """True for generic job-alert/newsletter digests -- checked BEFORE any
    opportunity matching so a digest that happens to name a tracked company
    among many listings is never mistaken for a real employer response."""
    sender = (email.sender or "").lower()
    if any(marker in sender for marker in _BULK_SENDER_SUBSTRINGS):
        return True
    subject = (email.subject or "").lower()
    return any(re.search(pattern, subject) for pattern in _BULK_SUBJECT_PATTERNS)


def _score_candidate(
    email: EmailContent, record: dict, recruiter_contacts: list[dict],
) -> MatchCandidate | None:
    text_blob = email.combined_text.lower()
    sender_domain = _sender_domain(email.sender)
    signals: list[str] = []
    score = 0

    company_tokens = _company_tokens(record.get("company") or "")
    company_hit = any(token in text_blob for token in company_tokens) or any(
        token and token in sender_domain for token in company_tokens
    )
    if company_hit:
        score += _SCORE_COMPANY
        signals.append("company")

    title_tokens = _title_tokens(record.get("job_title") or "")
    if title_tokens:
        overlap = sum(1 for token in title_tokens if token in text_blob)
        if overlap >= max(1, len(title_tokens) // 2):
            score += _SCORE_TITLE
            signals.append("title")

    external_job_id = (record.get("external_job_id") or "").strip()
    job_url = (record.get("job_url") or "").strip()
    if (external_job_id and len(external_job_id) >= 4 and external_job_id in text_blob) or (
        job_url and job_url in text_blob
    ):
        score += _SCORE_JOB_REFERENCE
        signals.append("job_reference")

    for contact in recruiter_contacts:
        contact_reference = (contact.get("contact_reference") or "").strip().lower()
        if contact_reference and contact_reference in (email.sender or "").lower():
            score += _SCORE_RECRUITER_CONTACT
            signals.append("recruiter_contact")
            break

    gmail_message_id = (record.get("gmail_message_id") or "").strip()
    if gmail_message_id and (
        gmail_message_id in (email.in_reply_to or "") or gmail_message_id in (email.references or "")
    ):
        score += _SCORE_THREAD_EVIDENCE
        signals.append("thread_evidence")

    if score <= 0:
        return None
    return MatchCandidate(
        tracker_id=record["id"], company=record.get("company") or "",
        job_title=record.get("job_title") or "", score=score, signals=tuple(signals),
    )


def match_opportunity(
    email: EmailContent, candidate_records: list[dict], recruiter_contacts_by_tracker: dict[int, list[dict]],
) -> MatchResult:
    """Score every in-scope opportunity against this message and decide
    whether the evidence is sufficient to act on -- conservatively. Never
    picks a "best guess": a tie within `AMBIGUITY_MARGIN` is reported as
    AMBIGUOUS rather than resolved to either side."""
    scored: list[MatchCandidate] = []
    for record in candidate_records:
        candidate = _score_candidate(email, record, recruiter_contacts_by_tracker.get(record["id"], []))
        if candidate and candidate.score >= MIN_MATCH_SCORE:
            scored.append(candidate)

    if not scored:
        return MatchResult(outcome=NO_MATCH)

    scored.sort(key=lambda candidate: candidate.score, reverse=True)
    top = scored[0]
    tied = [c for c in scored if top.score - c.score < AMBIGUITY_MARGIN]
    if len(tied) > 1:
        return MatchResult(outcome=AMBIGUOUS, tied_candidates=tuple(tied))
    return MatchResult(outcome=MATCHED, candidate=top)


def classify_email(email: EmailContent) -> str:
    """Deterministic, ordered keyword/phrase classification -- most decisive
    outcomes (rejection/offer) checked first. Falls through to UNKNOWN
    (routed to human review by the caller) rather than guessing."""
    text = email.combined_text.lower()
    for patterns, label in (
        (_REJECTION_PATTERNS, REJECTION),
        (_OFFER_PATTERNS, OFFER),
        (_INTERVIEW_PATTERNS, INTERVIEW_INVITATION),
        (_ASSESSMENT_PATTERNS, ASSESSMENT_REQUEST),
        (_SCREENING_PATTERNS, SCREENING_REQUEST),
        (_RECRUITER_RESPONSE_PATTERNS, RECRUITER_RESPONSE),
        (_ACKNOWLEDGEMENT_PATTERNS, ACKNOWLEDGEMENT),
    ):
        if any(re.search(pattern, text) for pattern in patterns):
            return label
    return UNKNOWN


@dataclass
class MonitorReport:
    checked: int = 0
    matched: int = 0
    unmatched: int = 0
    already_processed: int = 0
    acknowledgements: int = 0
    recruiter_responses: int = 0
    screening_requests: int = 0
    interviews: int = 0
    assessments: int = 0
    rejections: int = 0
    offers: int = 0
    human_review: int = 0
    details: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "messages_checked": self.checked,
            "matched": self.matched,
            "unmatched": self.unmatched,
            "already_processed": self.already_processed,
            "acknowledgements": self.acknowledgements,
            "recruiter_responses": self.recruiter_responses,
            "screening_requests": self.screening_requests,
            "interviews": self.interviews,
            "assessments": self.assessments,
            "rejections": self.rejections,
            "offers": self.offers,
            "human_review": self.human_review,
            "details": self.details,
        }


_CLASSIFICATION_REPORT_FIELD = {
    ACKNOWLEDGEMENT: "acknowledgements",
    RECRUITER_RESPONSE: "recruiter_responses",
    SCREENING_REQUEST: "screening_requests",
    INTERVIEW_INVITATION: "interviews",
    ASSESSMENT_REQUEST: "assessments",
    REJECTION: "rejections",
    OFFER: "offers",
}

# Best-effort supplementary stage transition for classifications that
# `OpportunityCRMService.record_employer_response` does not already map on
# its own (ACKNOWLEDGEMENT/RECRUITER_CONTACT/SCREENING_REQUEST/REJECTION/
# OFFER are handled inside that method already -- see its `stage_for_response`
# table). Applied only when it is a currently-allowed transition; otherwise
# left alone (the employer_response row + event still preserve the evidence).
_SUPPLEMENTARY_STAGE = {
    INTERVIEW_INVITATION: "INTERVIEW_1",
    ASSESSMENT_REQUEST: "SCREENING",
}

# Opportunities are only in scope for outcome matching once they have
# actually been applied to -- matching against a not-yet-applied opportunity
# would be guessing at correspondence that cannot exist yet.
_MATCHABLE_STAGES_MIN_APPLIED = True


class GmailOutcomeMonitor:
    def __init__(
        self,
        crm: OpportunityCRMService | None = None,
        gmail: GmailService | None = None,
        lookback_days: int = 30,
        max_messages: int = 200,
    ) -> None:
        self.crm = crm or OpportunityCRMService()
        # Always the narrow read-only scope/token -- never the compose-scope
        # credential drafting/sending flows use -- and dry_run/auto_send are
        # pinned regardless of caller input, since this class must never be
        # able to send even if constructed carelessly.
        self.gmail = gmail or GmailService(
            token_path=GMAIL_READONLY_TOKEN_PATH,
            scopes=GMAIL_READONLY_SCOPES,
            dry_run=True,
            auto_send=False,
        )
        self.lookback_days = lookback_days
        self.max_messages = max_messages

    # -- candidate opportunities ------------------------------------------
    def _candidate_records(self) -> list[dict]:
        """Every opportunity that has actually been applied to -- an
        employer/recruiter response cannot exist for anything earlier."""
        return [
            record for record in self.crm.history.list_records()
            if record.get("applied_at")
        ]

    def _recruiter_contacts_by_tracker(self, tracker_ids: list[int]) -> dict[int, list[dict]]:
        contacts: dict[int, list[dict]] = {}
        for tracker_id in tracker_ids:
            rows = self.crm.connection.execute(
                "SELECT * FROM recruiter_contacts WHERE tracker_id = ?", (tracker_id,)
            ).fetchall()
            if rows:
                contacts[tracker_id] = [dict(row) for row in rows]
        return contacts

    def _already_processed(self, message_id: str) -> bool:
        row = self.crm.connection.execute(
            "SELECT 1 FROM opportunity_events WHERE evidence_reference = ? LIMIT 1", (message_id,)
        ).fetchone()
        return row is not None

    # -- Gmail fetch (read-only) --------------------------------------------
    def _fetch_messages(self) -> list[EmailContent]:
        query = f"in:inbox newer_than:{self.lookback_days}d"
        emails: list[EmailContent] = []
        page_token = None
        while len(emails) < self.max_messages:
            response = self.gmail.list_message_ids(
                query, max_results=min(100, self.max_messages - len(emails)), page_token=page_token
            )
            for item in response.get("messages", []) or []:
                raw = self.gmail.get_message(item["id"], message_format="full")
                emails.append(email_content_from_gmail_api_message(raw))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return emails

    # -- core processing -----------------------------------------------------
    def process_message(self, email: EmailContent, report: MonitorReport) -> None:
        """Match, classify, and (conservatively) update the CRM for one
        already-fetched message. Public so a smoke test can feed real,
        already-read Gmail content through the exact same logic `run()`
        uses, without requiring a live Gmail-API round trip."""
        report.checked += 1

        if self._already_processed(email.message_id):
            report.already_processed += 1
            return

        if is_bulk_notification(email):
            report.unmatched += 1
            report.details.append({"message_id": email.message_id, "subject": email.subject, "outcome": "IGNORED_BULK_NOTIFICATION"})
            return

        candidates = self._candidate_records()
        tracker_ids = [record["id"] for record in candidates]
        recruiter_contacts = self._recruiter_contacts_by_tracker(tracker_ids)
        match = match_opportunity(email, candidates, recruiter_contacts)

        if match.outcome == NO_MATCH:
            report.unmatched += 1
            report.details.append({"message_id": email.message_id, "subject": email.subject, "outcome": "NO_MATCH"})
            return

        if match.outcome == AMBIGUOUS:
            report.human_review += 1
            report.details.append({
                "message_id": email.message_id, "subject": email.subject, "outcome": "AMBIGUOUS_MATCH",
                "candidates": [c.tracker_id for c in match.tied_candidates],
            })
            return

        # MATCHED: exactly one sufficiently-evidenced opportunity.
        tracker_id = match.candidate.tracker_id
        report.matched += 1
        classification = classify_email(email)
        summary = email.snippet or email.subject

        if classification == UNKNOWN:
            self.crm.record_employer_response(
                tracker_id, "UNKNOWN", received_at=email.date, source="GMAIL",
                summary=summary, evidence_reference=email.message_id,
            )
            self.crm.record_human_blocker(
                tracker_id, "OTHER",
                detail=f"Gmail message {email.message_id} matched this opportunity but its outcome could not be confidently classified -- human review required.",
            )
            report.human_review += 1
            report.details.append({
                "message_id": email.message_id, "subject": email.subject, "tracker_id": tracker_id,
                "outcome": "UNKNOWN_CLASSIFICATION_HUMAN_REVIEW",
            })
            return

        response_type = _EMPLOYER_RESPONSE_TYPE[classification]
        self.crm.record_employer_response(
            tracker_id, response_type, received_at=email.date, source="GMAIL",
            summary=summary, evidence_reference=email.message_id,
        )
        supplementary_stage = _SUPPLEMENTARY_STAGE.get(classification)
        if supplementary_stage:
            record = self.crm.get_opportunity(tracker_id)
            current_stage = record.get("crm_stage") or "DISCOVERED"
            if supplementary_stage in ALLOWED_TRANSITIONS.get(current_stage, frozenset()) and current_stage != supplementary_stage:
                self.crm.transition_stage(
                    tracker_id, supplementary_stage, reason=f"Gmail: {classification}",
                    evidence_reference=email.message_id,
                )

        report_field = _CLASSIFICATION_REPORT_FIELD[classification]
        setattr(report, report_field, getattr(report, report_field) + 1)
        report.details.append({
            "message_id": email.message_id, "subject": email.subject, "tracker_id": tracker_id,
            "classification": classification, "outcome": "RECORDED",
        })

    def run(self) -> dict:
        report = MonitorReport()
        for email in self._fetch_messages():
            self.process_message(email, report)
        return report.to_dict()
