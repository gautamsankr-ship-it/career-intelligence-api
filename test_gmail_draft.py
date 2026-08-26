"""Create one safe Gmail draft for manual OAuth verification."""

import argparse

from app.services.gmail_service import GmailService


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a safe Gmail draft.")
    parser.add_argument("recipient", help="Explicit test recipient email address")
    parser.add_argument("resume", help="Path to a safe sample Resume.docx")
    parser.add_argument("cover_letter", help="Path to a safe sample CoverLetter.docx")
    args = parser.parse_args()

    gmail = GmailService()
    draft_id = gmail.create_draft(
        recipient=args.recipient,
        subject="Gmail MVP draft verification",
        body=(
            "Hello,\n\n"
            "This is a safe Gmail MVP draft verification. It has not been sent.\n\n"
            "Regards"
        ),
        attachments=[args.resume, args.cover_letter],
    )
    print(f"Draft created successfully. Gmail draft ID: {draft_id}")


if __name__ == "__main__":
    main()
