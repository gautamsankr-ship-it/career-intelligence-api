import base64

import pytest

from app.config import GMAIL_READONLY_SCOPES, GMAIL_READONLY_TOKEN_PATH
from app.services.application_history_service import ApplicationHistoryService, job_fingerprint
from app.services.gmail_outcome_monitor_service import (
    ACKNOWLEDGEMENT,
    ASSESSMENT_REQUEST,
    GmailOutcomeMonitor,
    INTERVIEW_INVITATION,
    MonitorReport,
    OFFER,
    REJECTION,
    RECRUITER_RESPONSE,
    SCREENING_REQUEST,
    UNKNOWN,
    EmailContent,
    classify_email,
    email_content_from_gmail_api_message,
    is_bulk_notification,
    match_opportunity,
)
from app.services.gmail_service import GmailService
from app.services.opportunity_crm_service import OpportunityCRMService


# --- fixtures -----------------------------------------------------------
def crm(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    return OpportunityCRMService(history)


def applied_opportunity(service, *, external_id, company, job_title):
    fingerprint = job_fingerprint(source="LinkedIn", external_job_id=external_id)
    record = service.create_opportunity(fingerprint, company=company, job_title=job_title)
    service.history.update_record(fingerprint, applied_at="2026-08-31T00:00:00+00:00")
    return service.get_opportunity(record["id"])


def email(
    message_id="msg-1", subject="", body="", sender="recruiter@acme.com",
    snippet="", thread_id="thread-1", date="2026-09-01T00:00:00Z",
    in_reply_to="", references="",
) -> EmailContent:
    return EmailContent(
        message_id=message_id, thread_id=thread_id, sender=sender, subject=subject,
        date=date, snippet=snippet or body[:100], body_text=body,
        in_reply_to=in_reply_to, references=references,
    )


# --- classification -------------------------------------------------------
@pytest.mark.parametrize("body,expected", [
    ("Unfortunately, we have decided to move forward with other candidates.", REJECTION),
    ("We are pleased to offer you the position of Finance Manager.", OFFER),
    ("We would like to invite you to interview for this role next week.", INTERVIEW_INVITATION),
    ("Please complete the online assessment within 5 days.", ASSESSMENT_REQUEST),
    ("We'd like to schedule a phone screen with our recruiter.", SCREENING_REQUEST),
    ("I'm reaching out regarding your application to discuss next steps.", RECRUITER_RESPONSE),
    ("Thank you for applying. Your application is currently under review.", ACKNOWLEDGEMENT),
    ("Here is our quarterly newsletter about the industry.", UNKNOWN),
])
def test_classify_email_categories(body, expected):
    assert classify_email(email(body=body)) == expected


def test_classify_real_jobgether_acknowledgement_wording():
    """Real production wording (Tracker 61's actual Jobgether email) must
    classify as ACKNOWLEDGEMENT, not ASSESSMENT_REQUEST, even though it
    mentions an 'Assessment Report' -- it never asks the candidate to
    complete anything, it just states one is viewable."""
    body = (
        "Thanks again for applying to Head of Finance. Our system has calculated a "
        "Match Score and generated a detailed Assessment Report, now available on "
        "your Jobgether profile. This score is for information only. It does not "
        "confirm whether you've been approved or rejected."
    )
    assert classify_email(email(body=body)) == ACKNOWLEDGEMENT


def test_classify_real_isla_health_acknowledgement_wording():
    body = (
        "Thank you for your interest in Isla and applying for the role of Finance "
        "Manager. Our team is currently reviewing applications and we'll be in "
        "touch once we've completed the review process."
    )
    assert classify_email(email(body=body)) == ACKNOWLEDGEMENT


# --- bulk notification filtering -------------------------------------------
def test_generic_linkedin_job_alert_is_flagged_as_bulk():
    msg = email(
        subject="5 new jobs for Finance Manager", sender="jobalerts-noreply@linkedin.com",
        body="Finance Manager at Acme Corp, Finance Manager at Beta Inc.",
    )
    assert is_bulk_notification(msg) is True


def test_real_application_status_email_is_not_flagged_as_bulk():
    """A genuine ATS/ESP-sent application-status update (Brevo-powered, has
    an unsubscribe link) must NOT be caught by the bulk filter just because
    it looks like marketing mail -- only alert/digest subject patterns and
    known job-alert senders count."""
    msg = email(
        subject="Next Steps for Your Job Application: Head of Finance at Jobgether",
        sender="shortlist@jobgether.com", body="Thank you for applying to Head of Finance.",
    )
    assert is_bulk_notification(msg) is False


# --- matching ---------------------------------------------------------------
def test_confident_match_disambiguates_same_company_by_title(tmp_path):
    service = crm(tmp_path)
    head_of_finance = applied_opportunity(service, external_id="1", company="Jobgether", job_title="Head of Finance")
    revops = applied_opportunity(service, external_id="2", company="Jobgether", job_title="Revenue Operations Analyst, Partnerships")
    msg = email(
        subject="Next Steps for Your Job Application: Head of Finance at Jobgether",
        sender="shortlist@jobgether.com",
        body="Thank you for applying to Head of Finance. Your profile is currently under review.",
    )
    result = match_opportunity(msg, [head_of_finance, revops], {})
    assert result.outcome == "MATCHED"
    assert result.candidate.tracker_id == head_of_finance["id"]


def test_company_mention_alone_is_insufficient_evidence(tmp_path):
    """A single weak signal (company name only, no title/reference/recruiter/
    thread evidence) must never be enough to match."""
    service = crm(tmp_path)
    opportunity = applied_opportunity(service, external_id="1", company="Acme Corp", job_title="Software Engineer")
    msg = email(subject="Your Acme Corp account statement", sender="billing@acmecorp.com", body="Acme Corp monthly update.")
    result = match_opportunity(msg, [opportunity], {})
    assert result.outcome == "NO_MATCH"


def test_ambiguous_tie_routes_to_human_review_not_a_guess(tmp_path):
    service = crm(tmp_path)
    engineer_1 = applied_opportunity(service, external_id="1", company="Acme Corp", job_title="Software Engineer")
    engineer_2 = applied_opportunity(service, external_id="2", company="Acme Corp", job_title="Software Engineer II")
    msg = email(
        subject="Update on your Software Engineer application at Acme Corp",
        sender="hr@acmecorp.com", body="We wanted to update you on your Software Engineer application.",
    )
    result = match_opportunity(msg, [engineer_1, engineer_2], {})
    assert result.outcome == "AMBIGUOUS"
    assert {c.tracker_id for c in result.tied_candidates} == {engineer_1["id"], engineer_2["id"]}


def test_known_recruiter_contact_is_sufficient_on_its_own(tmp_path):
    service = crm(tmp_path)
    opportunity = applied_opportunity(service, external_id="1", company="Acme Corp", job_title="Software Engineer")
    service.record_recruiter_contact(opportunity["id"], name="Jane Recruiter", contact_reference="jane@acmecorp.com")
    contacts = {opportunity["id"]: [{"contact_reference": "jane@acmecorp.com"}]}
    msg = email(sender="Jane Recruiter <jane@acmecorp.com>", subject="Quick note", body="Just checking in.")
    result = match_opportunity(msg, [opportunity], contacts)
    assert result.outcome == "MATCHED"
    assert result.candidate.tracker_id == opportunity["id"]


# --- end-to-end CRM processing ----------------------------------------------
def test_process_message_records_acknowledgement_and_transitions_stage(tmp_path):
    service = crm(tmp_path)
    opportunity = applied_opportunity(service, external_id="1", company="Isla Health | B Corp", job_title="Finance Manager")
    monitor = GmailOutcomeMonitor(crm=service, gmail=object())
    msg = email(
        message_id="real-isla-msg", sender="talia.novella@isla.health", subject="Finance Manager - Isla",
        body="Thank you for your interest in Isla and applying for the role of Finance Manager. Currently under review.",
    )
    report = MonitorReport()
    monitor.process_message(msg, report)

    updated = service.get_opportunity(opportunity["id"])
    assert updated["crm_stage"] == "ACKNOWLEDGED"
    responses = service.get_opportunity_detail(opportunity["id"])["employer_responses"]
    assert len(responses) == 1
    assert responses[0]["response_type"] == "ACKNOWLEDGEMENT"
    assert responses[0]["evidence_reference"] == "real-isla-msg"
    assert report.matched == 1
    assert report.acknowledgements == 1


def test_process_message_unknown_classification_creates_human_blocker(tmp_path):
    service = crm(tmp_path)
    opportunity = applied_opportunity(service, external_id="1", company="Acme Corp", job_title="Finance Manager")
    monitor = GmailOutcomeMonitor(crm=service, gmail=object())
    msg = email(
        message_id="ambiguous-content-msg", sender="team@acmecorp.com",
        subject="About your Finance Manager application at Acme Corp",
        body="We wanted to reach out about your Finance Manager application at Acme Corp. Stay tuned.",
    )
    report = MonitorReport()
    monitor.process_message(msg, report)

    updated = service.get_opportunity(opportunity["id"])
    # UNKNOWN never auto-advances the stage -- it only preserves evidence and
    # opens a human blocker.
    assert updated["crm_stage"] != "ACKNOWLEDGED"
    responses = service.get_opportunity_detail(opportunity["id"])["employer_responses"]
    assert responses[0]["response_type"] == "UNKNOWN"
    blockers = service.list_open_blockers(opportunity["id"])
    assert len(blockers) == 1
    assert blockers[0]["blocker_type"] == "OTHER"
    assert report.human_review == 1


def test_ambiguous_match_never_writes_to_crm(tmp_path):
    service = crm(tmp_path)
    engineer_1 = applied_opportunity(service, external_id="1", company="Acme Corp", job_title="Software Engineer")
    engineer_2 = applied_opportunity(service, external_id="2", company="Acme Corp", job_title="Software Engineer II")
    monitor = GmailOutcomeMonitor(crm=service, gmail=object())
    msg = email(
        message_id="ambiguous-match-msg", sender="hr@acmecorp.com",
        subject="Update on your Software Engineer application at Acme Corp",
        body="We wanted to update you on your Software Engineer application.",
    )
    report = MonitorReport()
    monitor.process_message(msg, report)

    for tracker_id in (engineer_1["id"], engineer_2["id"]):
        assert service.get_opportunity(tracker_id)["crm_stage"] == "DISCOVERED"
        assert service.get_opportunity_detail(tracker_id)["employer_responses"] == []
        assert service.list_open_blockers(tracker_id) == []
    assert report.human_review == 1
    assert report.matched == 0


def test_generic_job_alert_is_ignored_even_when_mentioning_tracked_company(tmp_path):
    service = crm(tmp_path)
    opportunity = applied_opportunity(service, external_id="1", company="Acme Corp", job_title="Finance Manager")
    monitor = GmailOutcomeMonitor(crm=service, gmail=object())
    msg = email(
        message_id="bulk-alert-msg", sender="jobalerts-noreply@linkedin.com",
        subject="5 new jobs for Finance Manager", body="Finance Manager at Acme Corp is hiring.",
    )
    report = MonitorReport()
    monitor.process_message(msg, report)

    assert service.get_opportunity_detail(opportunity["id"])["employer_responses"] == []
    assert report.unmatched == 1
    assert report.matched == 0


def test_idempotent_processing_same_message_never_duplicates_crm_events(tmp_path):
    service = crm(tmp_path)
    opportunity = applied_opportunity(service, external_id="1", company="Isla Health", job_title="Finance Manager")
    monitor = GmailOutcomeMonitor(crm=service, gmail=object())
    msg = email(
        message_id="dup-msg", sender="talia@isla.health", subject="Finance Manager - Isla",
        body="Thank you for your interest in Isla and applying for the role of Finance Manager.",
    )
    report = MonitorReport()
    monitor.process_message(msg, report)
    timeline_after_first_run = service.get_timeline(opportunity["id"])

    monitor.process_message(msg, report)  # same Gmail message id, processed again
    timeline_after_second_run = service.get_timeline(opportunity["id"])

    responses = service.get_opportunity_detail(opportunity["id"])["employer_responses"]
    assert len(responses) == 1  # not duplicated
    assert timeline_after_second_run == timeline_after_first_run  # nothing new was written
    assert report.matched == 1
    assert report.already_processed == 1


# --- no Gmail write actions --------------------------------------------------
class _ReadOnlyMessages:
    """Exposes only list()/get() -- no drafts()/send()/modify() at all, so
    any accidental write call raises AttributeError instead of silently
    succeeding."""

    def __init__(self, list_response, message_by_id):
        self._list_response = list_response
        self._message_by_id = message_by_id
        self.list_calls = []
        self.get_calls = []

    def list(self, userId, q, maxResults, pageToken=None):
        self.list_calls.append({"userId": userId, "q": q, "maxResults": maxResults, "pageToken": pageToken})
        return _FakeRequest(self._list_response)

    def get(self, userId, id, format):
        self.get_calls.append({"userId": userId, "id": id, "format": format})
        return _FakeRequest(self._message_by_id[id])


class _FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class _ReadOnlyUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _ReadOnlyGmailApi:
    def __init__(self, messages):
        self._users = _ReadOnlyUsers(messages)

    def users(self):
        return self._users


def _raw_gmail_message(message_id, sender, subject, body):
    encoded = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "snippet": body[:80],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Tue, 1 Sep 2026 00:00:00 +0000"},
            ],
            "body": {"data": encoded},
        },
    }


def test_run_only_calls_read_only_gmail_endpoints(tmp_path):
    service = crm(tmp_path)
    applied_opportunity(service, external_id="1", company="Isla Health", job_title="Finance Manager")
    raw = _raw_gmail_message(
        "real-msg-1", "talia@isla.health", "Finance Manager - Isla",
        "Thank you for your interest in Isla and applying for the role of Finance Manager.",
    )
    messages_resource = _ReadOnlyMessages(
        list_response={"messages": [{"id": "real-msg-1"}]}, message_by_id={"real-msg-1": raw},
    )
    fake_api = _ReadOnlyGmailApi(messages_resource)
    gmail = GmailService(api_service=fake_api, scopes=GMAIL_READONLY_SCOPES, dry_run=True, auto_send=False)
    monitor = GmailOutcomeMonitor(crm=service, gmail=gmail)

    report = monitor.run()

    assert report["messages_checked"] == 1
    assert report["matched"] == 1
    assert report["acknowledgements"] == 1
    # Only list()/get() were ever reachable -- there is no drafts()/send() on
    # this fake at all, so a write attempt would have raised, not succeeded.
    assert len(messages_resource.list_calls) == 1
    assert len(messages_resource.get_calls) == 1


def test_email_content_from_gmail_api_message_decodes_headers_and_body():
    raw = _raw_gmail_message("m1", "a@b.com", "Subject Line", "Hello world")
    content = email_content_from_gmail_api_message(raw)
    assert content.message_id == "m1"
    assert content.sender == "a@b.com"
    assert content.subject == "Subject Line"
    assert content.body_text == "Hello world"


def test_monitor_defaults_to_readonly_scope_and_token(tmp_path):
    service = crm(tmp_path)
    monitor = GmailOutcomeMonitor(crm=service)
    assert monitor.gmail.scopes == GMAIL_READONLY_SCOPES
    assert str(monitor.gmail.token_path) == GMAIL_READONLY_TOKEN_PATH
    assert monitor.gmail.dry_run is True
    assert monitor.gmail.auto_send is False
