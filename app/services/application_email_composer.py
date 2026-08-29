"""Compose a concise application email for an EMAIL-route vacancy. Never
duplicates a full cover letter inside the email -- greeting, one short
application sentence, the employer-requested custom response (if any), a
short closing, and the candidate's verified name."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComposedEmail:
    to: str
    subject: str
    body: str


class ApplicationEmailComposer:
    def compose(
        self,
        recipient: str,
        subject: str,
        candidate_name: str,
        role_title: str,
        employer_name: str,
        contact_name: str | None = None,
        custom_response: str | None = None,
    ) -> ComposedEmail:
        greeting = f"Hi {contact_name}," if contact_name else f"Dear {employer_name} Hiring Team,"

        lines = [
            greeting,
            "",
            f"I'm writing to apply for the {role_title} role at {employer_name}." if role_title and employer_name
            else f"I'm writing to apply for this role at {employer_name}." if employer_name
            else "I'm writing to apply for this role.",
        ]

        if custom_response:
            lines += ["", custom_response]

        lines += [
            "",
            "Thank you for considering my application. I look forward to the opportunity to discuss it further.",
            "",
            "Kind regards,",
            candidate_name,
        ]

        return ComposedEmail(to=recipient, subject=subject, body="\n".join(lines))
