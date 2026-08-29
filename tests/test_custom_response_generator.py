"""Task 21.11: grounded, word-limited employer written-response generation.
Every fixture here is synthetic; nothing reads the production profile."""

from app.services.custom_response_generator import CustomResponseGenerator


PROFILE = {
    "professional_summary": {
        "headline": "Chartered Accountant with 15+ years of experience across accounting, taxation and audit.",
    },
    "employment_history": [
        {
            "company": "Example Accounting Firm",
            "position": "Offshore Accounting Manager",
            "responsibilities": ["Management Accounting", "Financial Reporting", "Australian Tax"],
        },
        {
            "company": "GSN Associates",
            "position": "Managing Partner",
            "responsibilities": ["Audit", "Business Advisory", "Leadership"],
        },
    ],
}


def test_response_respects_word_limit():
    response = CustomResponseGenerator().generate(PROFILE, max_words=60, employer_name="Acme Partners")
    assert len(response.split()) <= 60


def test_response_never_truncates_mid_sentence():
    response = CustomResponseGenerator().generate(PROFILE, max_words=60, employer_name="Acme Partners")
    assert response.strip().endswith(".")


def test_response_names_the_employer_not_hardcoded():
    response = CustomResponseGenerator().generate(PROFILE, max_words=200, employer_name="Totally Different Co")
    assert "Totally Different Co" in response
    assert "EnVision" not in response


def test_response_preserves_natural_years_phrasing_not_reduced_to_bare_years():
    """Task 21.13 section 2: prefer 'over 15 years'/'15+ years' rather than
    silently reducing verified experience to a bare '15 years'."""
    response = CustomResponseGenerator().generate(PROFILE, max_words=200, employer_name="Acme Partners")
    assert "15+ years" in response or "over 15 years" in response
    assert "with 15 years of experience" not in response


def test_response_grounded_only_in_supplied_profile_no_fabricated_claims():
    response = CustomResponseGenerator().generate(PROFILE, max_words=200, employer_name="Acme Partners")
    for forbidden in ("ATO", "BAS", "IAS", "citizenship", "visa", "AASB", "sponsorship"):
        assert forbidden not in response


def test_response_ranks_evidence_by_vacancy_relevance():
    """With Australian-accounting vacancy keywords, the Australian role's
    evidence should appear; a different vacancy's keywords would surface
    the other role instead (same mechanism as resume ranking)."""
    keywords = {"management accounting", "australian tax", "xero"}
    response = CustomResponseGenerator().generate(
        PROFILE, max_words=200, employer_name="Acme Partners", vacancy_keywords=keywords
    )
    assert "Example Accounting Firm" in response or "Offshore Accounting Manager" in response


def test_short_word_limit_still_produces_a_complete_response():
    response = CustomResponseGenerator().generate(PROFILE, max_words=15, employer_name="Acme Partners")
    assert response.strip().endswith(".")
    assert len(response.split()) <= 25  # generous slack for the hard-limit single-sentence fallback


def test_empty_profile_does_not_crash_and_still_produces_a_closing():
    response = CustomResponseGenerator().generate({}, max_words=100, employer_name="Acme Partners")
    assert isinstance(response, str)
    assert "Acme Partners" in response


# --- Task 21.12 section 16/17: richer response when the word budget allows ---

RICH_PROFILE = {
    "professional_summary": {
        "headline": "Chartered Accountant with 15+ years of experience across accounting, taxation and audit.",
    },
    "employment_history": [
        {
            "company": "Example Accounting Firm",
            "position": "Offshore Accounting Manager",
            "responsibilities": [
                "Management Accounting", "Financial Reporting", "Australian Tax",
                "Prepared and lodged BAS, IAS, FBT returns and GST returns for clients.",
                "Managed the full SMSF workflow, including annual accounting and tax compliance.",
            ],
            "achievements": [
                "Built offshore accounting team",
                "Achieved 145% of annual revenue target.",
                "Improved tax filing compliance from 74% to 95%.",
            ],
        },
    ],
}


def test_rich_response_uses_more_of_a_generous_word_budget_than_a_tight_one():
    tight = CustomResponseGenerator().generate(RICH_PROFILE, max_words=60, employer_name="Acme Partners")
    rich = CustomResponseGenerator().generate(RICH_PROFILE, max_words=200, employer_name="Acme Partners")
    assert len(rich.split()) > len(tight.split())
    assert len(rich.split()) <= 200


def test_rich_response_includes_a_quantified_achievement_when_available():
    response = CustomResponseGenerator().generate(RICH_PROFILE, max_words=200, employer_name="Acme Partners")
    assert "145%" in response or "74% to 95%" in response


def test_rich_response_prefers_vacancy_relevant_detail_sentence():
    """With SMSF/BAS-flavoured vacancy keywords and a generous word budget,
    the relevant detail sentence (not just the terse lead-in) should surface."""
    keywords = {"smsf", "bas", "gst"}
    response = CustomResponseGenerator().generate(
        RICH_PROFILE, max_words=200, employer_name="Acme Partners", vacancy_keywords=keywords
    )
    assert "SMSF" in response or "BAS" in response


def test_response_below_150_word_limit_does_not_add_achievement_padding():
    """Below the 'generous budget' threshold, no achievement sentences are
    appended -- richness is a quality target for generous limits, not a
    universal minimum forced onto every response."""
    response = CustomResponseGenerator().generate(RICH_PROFILE, max_words=100, employer_name="Acme Partners")
    assert "145%" not in response
    assert "74% to 95%" not in response


def test_rich_response_still_respects_the_word_limit_even_when_evidence_is_abundant():
    response = CustomResponseGenerator().generate(RICH_PROFILE, max_words=150, employer_name="Acme Partners")
    assert len(response.split()) <= 150
    assert response.strip().endswith(".")
