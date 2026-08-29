import base64
from email import policy
from email.parser import BytesParser

import pytest

from app.config import GMAIL_SENDER_ADDRESS
from app.services.application_history_service import ApplicationHistoryService
from app.services.gmail_service import GmailService


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeDrafts:
    def __init__(self, response=None, error=None):
        self.response = response or {"id": "draft-123"}
        self.error = error
        self.create_kwargs = None
        self.update_kwargs = None

    def create(self, **kwargs):
        if self.error:
            raise self.error
        self.create_kwargs = kwargs
        self.kwargs = kwargs
        return FakeRequest(self.response)

    def update(self, **kwargs):
        if self.error:
            raise self.error
        self.update_kwargs = kwargs
        self.kwargs = kwargs
        return FakeRequest(self.response)


class FakeUsers:
    def __init__(self, drafts):
        self._drafts = drafts

    def drafts(self):
        return self._drafts


class FakeGmailApi:
    def __init__(self, drafts):
        self._users = FakeUsers(drafts)

    def users(self):
        return self._users


def parse_message(service, recipient, subject, body, attachments):
    mime = service.build_mime_message(recipient, subject, body, attachments)
    raw = base64.urlsafe_b64decode(service.build_gmail_body(mime)["raw"])
    return BytesParser(policy=policy.default).parsebytes(raw)


def test_mime_subject_body_and_attachments(tmp_path):
    resume = tmp_path / "Resume.docx"
    cover = tmp_path / "CoverLetter.docx"
    resume.write_bytes(b"resume")
    cover.write_bytes(b"cover")

    message = parse_message(
        GmailService(api_service=object()),
        "safe@example.com",
        "Application for Analyst - Candidate",
        "Hello",
        [resume, cover],
    )

    assert message["To"] == "safe@example.com"
    assert message["Subject"] == "Application for Analyst - Candidate"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Hello"
    assert {part.get_filename() for part in message.iter_attachments()} == {
        "Resume.docx",
        "CoverLetter.docx",
    }


def test_missing_recipient_and_attachment_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="recipient"):
        GmailService.build_mime_message("", "Subject", "Body")

    with pytest.raises(FileNotFoundError, match="Attachment"):
        GmailService.build_mime_message(
            "safe@example.com", "Subject", "Body", [tmp_path / "missing.docx"]
        )


def test_dry_run_blocks_send():
    service = GmailService(api_service=object(), dry_run=True, auto_send=True)
    with pytest.raises(RuntimeError, match="send is disabled"):
        service.send_message("safe@example.com", "Subject", "Body")


def test_successful_draft_updates_history(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    fingerprint = "job-fingerprint"
    history.claim_job(fingerprint, status="ELIGIBLE")
    fake_api = FakeGmailApi(FakeDrafts())
    service = GmailService(api_service=fake_api)

    try:
        draft_id = service.create_draft_for_application(
            history,
            fingerprint,
            "safe@example.com",
            "Subject",
            "Body",
        )
        record = history.get_record(fingerprint)
        assert draft_id == "draft-123"
        assert record["status"] == "DRAFTED"
        assert record["gmail_message_id"] == "draft-123"
        assert record["sent_at"] is None
    finally:
        history.close()


def test_build_mime_message_defaults_from_to_the_configured_sender_address():
    """Without this, Gmail falls back to whichever "Send As" alias is
    currently marked default on the account, which may be the wrong one."""
    message = GmailService.build_mime_message("safe@example.com", "Subject", "Body")
    assert message["From"] == GMAIL_SENDER_ADDRESS


def test_build_mime_message_sender_can_be_overridden_explicitly():
    message = GmailService.build_mime_message(
        "safe@example.com", "Subject", "Body", sender="other@example.com"
    )
    assert message["From"] == "other@example.com"


def test_create_draft_sends_configured_sender_in_mime_message():
    drafts = FakeDrafts(response={"id": "draft-123"})
    service = GmailService(api_service=FakeGmailApi(drafts))

    service.create_draft("safe@example.com", "Subject", "Body")

    raw = base64.urlsafe_b64decode(drafts.create_kwargs["body"]["message"]["raw"])
    mime = BytesParser(policy=policy.default).parsebytes(raw)
    assert mime["From"] == GMAIL_SENDER_ADDRESS


def test_update_draft_sends_configured_sender_in_mime_message():
    drafts = FakeDrafts(response={"id": "draft-123"})
    service = GmailService(api_service=FakeGmailApi(drafts))

    service.update_draft("draft-123", "safe@example.com", "Subject", "Body")

    raw = base64.urlsafe_b64decode(drafts.update_kwargs["body"]["message"]["raw"])
    mime = BytesParser(policy=policy.default).parsebytes(raw)
    assert mime["From"] == GMAIL_SENDER_ADDRESS


def test_update_draft_calls_drafts_update_with_correct_id_and_message(tmp_path):
    resume = tmp_path / "Resume.docx"
    resume.write_bytes(b"resume-bytes")
    drafts = FakeDrafts(response={"id": "draft-123"})
    fake_api = FakeGmailApi(drafts)
    service = GmailService(api_service=fake_api)

    updated_id = service.update_draft(
        "draft-123",
        "safe@example.com",
        "EnVision – Shankar Gautam",
        "Hi Sarah,\n\nBody text.",
        [resume],
    )

    assert updated_id == "draft-123"
    assert drafts.update_kwargs is not None
    assert drafts.create_kwargs is None  # create() must never be called by update_draft
    assert drafts.update_kwargs["userId"] == "me"
    assert drafts.update_kwargs["id"] == "draft-123"

    raw = base64.urlsafe_b64decode(drafts.update_kwargs["body"]["message"]["raw"])
    mime = BytesParser(policy=policy.default).parsebytes(raw)
    assert mime["To"] == "safe@example.com"
    assert mime["Subject"] == "EnVision – Shankar Gautam"
    assert mime.get_body(preferencelist=("plain",)).get_content().startswith("Hi Sarah,")
    assert {part.get_filename() for part in mime.iter_attachments()} == {"Resume.docx"}


def test_update_draft_never_reaches_a_send_endpoint():
    """FakeUsers exposes only drafts() -- no messages(). If update_draft tried to
    send, this would raise AttributeError instead of silently succeeding."""
    service = GmailService(api_service=FakeGmailApi(FakeDrafts()))
    draft_id = service.update_draft("draft-123", "safe@example.com", "Subject", "Body")
    assert draft_id == "draft-123"


def test_update_draft_requires_explicit_draft_id():
    service = GmailService(api_service=FakeGmailApi(FakeDrafts()))
    with pytest.raises(ValueError, match="draft ID"):
        service.update_draft("", "safe@example.com", "Subject", "Body")


def test_update_draft_requires_existing_attachment(tmp_path):
    service = GmailService(api_service=FakeGmailApi(FakeDrafts()))
    with pytest.raises(FileNotFoundError, match="Attachment"):
        service.update_draft(
            "draft-123",
            "safe@example.com",
            "Subject",
            "Body",
            [tmp_path / "missing.docx"],
        )


def test_update_draft_propagates_gmail_api_failure():
    service = GmailService(
        api_service=FakeGmailApi(FakeDrafts(error=RuntimeError("mock Gmail update failure")))
    )
    with pytest.raises(RuntimeError, match="mock Gmail update failure"):
        service.update_draft("draft-123", "safe@example.com", "Subject", "Body")


def test_update_draft_does_not_touch_tracker_application_state(tmp_path):
    """update_draft has no history/job_fingerprint parameters at all -- confirm a
    tracker record already in DRAFTED state (Tracker 41's real state) is
    completely untouched by a draft update, in particular never becomes APPLIED."""
    history = ApplicationHistoryService(tmp_path / "history.db")
    fingerprint = "job-fingerprint"
    history.claim_job(fingerprint, status="ELIGIBLE")
    history.update_record(fingerprint, status="DRAFTED", gmail_message_id="draft-123")

    try:
        service = GmailService(api_service=FakeGmailApi(FakeDrafts(response={"id": "draft-123"})))
        service.update_draft("draft-123", "safe@example.com", "Subject", "Body")

        record = history.get_record(fingerprint)
        assert record["status"] == "DRAFTED"
        assert record["application_status"] == "DRAFTED"
        assert record["applied_at"] is None
        assert record["sent_at"] is None
    finally:
        history.close()


def test_failed_draft_updates_history(tmp_path):
    history = ApplicationHistoryService(tmp_path / "history.db")
    fingerprint = "job-fingerprint"
    history.claim_job(fingerprint, status="ELIGIBLE")
    service = GmailService(
        api_service=FakeGmailApi(FakeDrafts(error=RuntimeError("mock Gmail failure")))
    )

    try:
        with pytest.raises(RuntimeError, match="draft creation failed"):
            service.create_draft_for_application(
                history,
                fingerprint,
                "safe@example.com",
                "Subject",
                "Body",
            )
        record = history.get_record(fingerprint)
        assert record["status"] == "FAILED"
        assert "mock Gmail failure" in record["error_message"]
    finally:
        history.close()
