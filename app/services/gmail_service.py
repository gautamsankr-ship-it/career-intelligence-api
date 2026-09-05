"""Safe Gmail OAuth, MIME, draft, and explicitly guarded send operations."""

from __future__ import annotations

import base64
import mimetypes
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.config import (
    GMAIL_AUTO_SEND,
    GMAIL_CREDENTIALS_PATH,
    GMAIL_DRY_RUN,
    GMAIL_SCOPES,
    GMAIL_SENDER_ADDRESS,
    GMAIL_TOKEN_PATH,
)


class GmailService:
    """Gmail integration defaulting to draft-only behavior."""

    def __init__(
        self,
        credentials_path: str | Path = GMAIL_CREDENTIALS_PATH,
        token_path: str | Path = GMAIL_TOKEN_PATH,
        dry_run: bool = GMAIL_DRY_RUN,
        auto_send: bool = GMAIL_AUTO_SEND,
        api_service=None,
        scopes: Iterable[str] = GMAIL_SCOPES,
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.dry_run = dry_run
        self.auto_send = auto_send
        self._api_service = api_service
        self.scopes = tuple(scopes)

    def authenticate(self):
        if self._api_service is not None:
            return self._api_service

        credentials = None
        if self.token_path.exists():
            try:
                credentials = Credentials.from_authorized_user_file(
                    self.token_path,
                    self.scopes,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Could not read Gmail token file '{self.token_path}': {exc}"
                ) from exc

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except Exception as exc:
                raise RuntimeError(f"Could not refresh Gmail OAuth token: {exc}") from exc
        elif not credentials or not credentials.valid:
            if not self.credentials_path.exists():
                raise FileNotFoundError(
                    f"Gmail OAuth credentials not found at '{self.credentials_path}'. "
                    "Download the Desktop App credentials as described in GMAIL_SETUP.md."
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path,
                    self.scopes,
                )
                credentials = flow.run_local_server(port=0)
            except Exception as exc:
                raise RuntimeError(f"Gmail OAuth authentication failed: {exc}") from exc

        try:
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
            self._api_service = build("gmail", "v1", credentials=credentials)
        except Exception as exc:
            raise RuntimeError(f"Could not initialize Gmail API service: {exc}") from exc
        return self._api_service

    @staticmethod
    def build_mime_message(
        recipient: str,
        subject: str,
        body: str,
        attachments: Iterable[str | Path] = (),
        sender: str | None = GMAIL_SENDER_ADDRESS,
    ) -> EmailMessage:
        recipient = (recipient or "").strip()
        if not recipient:
            raise ValueError("A recipient email address must be supplied explicitly.")

        message = EmailMessage()
        # Without an explicit From, Gmail falls back to whichever "Send As"
        # alias is currently marked default on the account -- which may not
        # be the intended sender -- so this is always set explicitly rather
        # than left to that account-level default.
        if sender:
            message["From"] = sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        for attachment in attachments:
            path = Path(attachment)
            if not path.is_file():
                raise FileNotFoundError(f"Attachment not found: {path}")
            content_type, _ = mimetypes.guess_type(path.name)
            maintype, subtype = (content_type or "application/octet-stream").split(
                "/", 1
            )
            message.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )
        return message

    @classmethod
    def build_gmail_body(cls, message: EmailMessage) -> dict[str, str]:
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        return {"raw": raw}

    def create_draft(
        self,
        recipient: str,
        subject: str,
        body: str,
        attachments: Iterable[str | Path] = (),
        sender: str | None = GMAIL_SENDER_ADDRESS,
    ) -> str:
        message = self.build_mime_message(recipient, subject, body, attachments, sender)
        response = (
            self.authenticate()
            .users()
            .drafts()
            .create(userId="me", body={"message": self.build_gmail_body(message)})
            .execute()
        )
        draft_id = response.get("id") or response.get("message", {}).get("id")
        if not draft_id:
            raise RuntimeError("Gmail draft response did not include a draft ID.")
        return draft_id

    def update_draft(
        self,
        draft_id: str,
        recipient: str,
        subject: str,
        body: str,
        attachments: Iterable[str | Path] = (),
        sender: str | None = GMAIL_SENDER_ADDRESS,
    ) -> str:
        """Replace an existing draft's content in place. Never sends; the
        Gmail drafts.update endpoint only ever creates/updates a draft."""
        draft_id = (draft_id or "").strip()
        if not draft_id:
            raise ValueError("An existing draft ID must be supplied explicitly to update a draft.")

        message = self.build_mime_message(recipient, subject, body, attachments, sender)
        response = (
            self.authenticate()
            .users()
            .drafts()
            .update(userId="me", id=draft_id, body={"message": self.build_gmail_body(message)})
            .execute()
        )
        updated_id = response.get("id") or response.get("message", {}).get("id")
        if not updated_id:
            raise RuntimeError("Gmail draft update response did not include a draft ID.")
        return updated_id

    def send_message(
        self,
        recipient: str,
        subject: str,
        body: str,
        attachments: Iterable[str | Path] = (),
        sender: str | None = GMAIL_SENDER_ADDRESS,
    ) -> str:
        if self.dry_run or not self.auto_send:
            raise RuntimeError(
                "Gmail send is disabled. Keep GMAIL_DRY_RUN=True and "
                "GMAIL_AUTO_SEND=False until explicitly enabled."
            )
        message = self.build_mime_message(recipient, subject, body, attachments, sender)
        response = (
            self.authenticate()
            .users()
            .messages()
            .send(userId="me", body=self.build_gmail_body(message))
            .execute()
        )
        message_id = response.get("id")
        if not message_id:
            raise RuntimeError("Gmail send response did not include a message ID.")
        return message_id

    def create_draft_for_application(
        self,
        history,
        job_fingerprint: str,
        recipient: str,
        subject: str,
        body: str,
        attachments: Iterable[str | Path] = (),
        sender: str | None = GMAIL_SENDER_ADDRESS,
    ) -> str:
        """Create a draft and update the Task 3 history record safely."""
        try:
            draft_id = self.create_draft(recipient, subject, body, attachments, sender)
        except Exception as exc:
            history.update_record(
                job_fingerprint,
                status="FAILED",
                error_message=str(exc),
            )
            raise RuntimeError(f"Gmail draft creation failed: {exc}") from exc

        history.update_record(
            job_fingerprint,
            status="DRAFTED",
            gmail_message_id=draft_id,
            error_message=None,
        )
        return draft_id

    # -- Task 21.34: read-only inbox access -------------------------------
    # These two methods only ever call messages().list / messages().get --
    # never send/modify/delete/label -- so Gmail Outcome Monitoring (which
    # must stay strictly read-only) can reuse this same class's OAuth
    # handling instead of duplicating it.
    def list_message_ids(
        self, query: str, max_results: int = 100, page_token: str | None = None
    ) -> dict:
        request = (
            self.authenticate()
            .users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results, pageToken=page_token)
        )
        return request.execute()

    def get_message(self, message_id: str, message_format: str = "full") -> dict:
        return (
            self.authenticate()
            .users()
            .messages()
            .get(userId="me", id=message_id, format=message_format)
            .execute()
        )
