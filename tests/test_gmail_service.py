import base64
from email import policy
from email.parser import BytesParser

import pytest

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

    def create(self, **kwargs):
        if self.error:
            raise self.error
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
