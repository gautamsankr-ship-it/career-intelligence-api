"""Task 21.11: concise application email composition -- no duplicate cover
letter, verified contact name used only when actually evidenced."""

from app.services.application_email_composer import ApplicationEmailComposer


def test_uses_verified_contact_name_when_available():
    email = ApplicationEmailComposer().compose(
        recipient="hr@envision.com.au",
        subject="EnVision – Shankar Gautam",
        candidate_name="Shankar Gautam",
        role_title="Tax & Business Advisory Accountant",
        employer_name="EnVision Partners",
        contact_name="Sarah",
        custom_response="Grounded response text.",
    )
    assert email.body.startswith("Hi Sarah,")
    assert email.to == "hr@envision.com.au"
    assert email.subject == "EnVision – Shankar Gautam"


def test_falls_back_to_neutral_greeting_when_no_contact_name():
    email = ApplicationEmailComposer().compose(
        recipient="jobs@example.com",
        subject="Application",
        candidate_name="Jane Candidate",
        role_title="Senior Accountant",
        employer_name="Acme Partners",
        contact_name=None,
    )
    assert email.body.startswith("Dear Acme Partners Hiring Team,")


def test_body_contains_application_sentence_and_signature():
    email = ApplicationEmailComposer().compose(
        recipient="jobs@example.com", subject="Application", candidate_name="Jane Candidate",
        role_title="Senior Accountant", employer_name="Acme Partners", contact_name=None,
    )
    assert "Senior Accountant" in email.body
    assert "Acme Partners" in email.body
    assert email.body.strip().endswith("Jane Candidate")


def test_does_not_duplicate_a_full_cover_letter_when_no_custom_response():
    email = ApplicationEmailComposer().compose(
        recipient="jobs@example.com", subject="Application", candidate_name="Jane Candidate",
        role_title="Senior Accountant", employer_name="Acme Partners", contact_name=None,
    )
    # Body stays short: greeting + one sentence + closing + signature only.
    assert len(email.body.split()) < 60


def test_custom_response_is_included_when_provided():
    email = ApplicationEmailComposer().compose(
        recipient="jobs@example.com", subject="Application", candidate_name="Jane Candidate",
        role_title="Senior Accountant", employer_name="Acme Partners", contact_name=None,
        custom_response="This is my grounded written response to the employer's question.",
    )
    assert "This is my grounded written response" in email.body
