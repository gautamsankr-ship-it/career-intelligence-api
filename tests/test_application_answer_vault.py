from __future__ import annotations

from pathlib import Path

from app.models.application_answer import ApplicationAnswer, ApplicationRule
from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_answer_vault import ApplicationAnswerVault


def engine(tmp_path: Path) -> tuple[ApplicationAnswerVault, ApplicationAnswerEngine]:
    vault = ApplicationAnswerVault(tmp_path / "vault.json")
    return vault, ApplicationAnswerEngine(vault)


def test_current_location_autofills_from_the_approved_standing_fact(tmp_path):
    """Task 21.30: the candidate's current location (Kathmandu, Nepal) is
    now an explicitly human-approved STANDING fact -- resolved the same way
    as email/phone/name, via the normal approved-vault-answer path, with no
    per-application context required. Never inferred from a target market
    or from planned future relocation -- a flat, non-market-scoped value."""
    _, service = engine(tmp_path)
    country = service.resolve("What is your current country of residence?")
    assert (country.concept, country.answer, country.manual_review) == ("CURRENT_LOCATION_COUNTRY", "Nepal", False)
    city = service.resolve("Current city")
    assert (city.concept, city.answer, city.manual_review) == ("CURRENT_CITY", "Kathmandu", False)
    city2 = service.resolve("City of residence")
    assert (city2.concept, city2.answer, city2.manual_review) == ("CURRENT_CITY", "Kathmandu", False)
    full = service.resolve("Where are you currently based?")
    assert (full.concept, full.answer, full.manual_review) == ("CURRENT_LOCATION", "Kathmandu, Nepal", False)
    # Never affected by the vacancy's target market.
    for market in ("united_kingdom", "united_states", "australia"):
        assert service.resolve("Country of residence", market=market).answer == "Nepal"


def test_current_location_never_changes_work_authorization_answers(tmp_path):
    """Location and work authorization remain fully separate concepts."""
    _, service = engine(tmp_path)
    auth = service.resolve("Are you legally authorized to work in Australia?", market="australia")
    assert (auth.concept, auth.answer) == ("WORK_AUTHORIZATION_AUSTRALIA", "NO")


def test_specific_qualification_is_not_overclaimed(tmp_path):
    """Task 21.31: the candidate holds a Chartered Accountant qualification
    through ICAI/ICAN, never ACA or ACCA specifically. An exact-designation
    question is honestly answered NO -- not manual review -- and a broad
    qualified-accountant question remains YES."""
    _, service = engine(tmp_path)
    acca = service.resolve("Are you ACCA qualified?")
    assert (acca.concept, acca.answer, acca.manual_review) == ("ACCOUNTING_QUALIFICATION_ACA_ACCA", "NO", False)
    assert service.resolve("Are you a qualified accountant?").answer == "YES"


def test_aca_acca_exact_designation_questions_answer_no(tmp_path):
    _, service = engine(tmp_path)
    for question in ("Are you ACA?", "Are you ACCA?", "Are you ACA or ACCA qualified?", "Do you hold ACA/ACCA?"):
        result = service.resolve(question)
        assert (result.concept, result.answer, result.manual_review) == ("ACCOUNTING_QUALIFICATION_ACA_ACCA", "NO", False), question


def test_broad_qualification_questions_answer_yes(tmp_path):
    _, service = engine(tmp_path)
    for question in ("Are you a Chartered Accountant?", "Are you a qualified accountant?"):
        result = service.resolve(question)
        assert (result.concept, result.answer, result.manual_review) == ("ACCOUNTING_QUALIFICATION", "YES", False), question


def test_aca_acca_or_equivalent_recognizes_ca_and_does_not_autofail(tmp_path):
    """"...or equivalent" phrasing must never auto-fail the candidate just
    because they do not personally hold ACA/ACCA -- ICAI/ICAN Chartered
    Accountancy is the approved equivalent professional qualification."""
    _, service = engine(tmp_path)
    for question in ("ACA/ACCA or equivalent", "ACA, ACCA, CIMA or equivalent professional qualification", "Qualified accountant or equivalent"):
        result = service.resolve(question)
        assert result.answer == "YES" and not result.manual_review, question


def test_qualification_multiple_choice_prefers_ca_never_selects_aca_acca(tmp_path):
    """Never select an ACA/ACCA-specific option -- prefer an explicit CA/
    Chartered Accountant choice, then Equivalent/Other, in that order."""
    _, service = engine(tmp_path)
    with_ca = service.resolve("Are you ACA or ACCA qualified?", choices=["ACA", "ACCA", "CA", "CPA", "CIMA", "Other"])
    assert with_ca.answer == "CA" and not with_ca.manual_review

    without_ca = service.resolve("Are you ACA or ACCA qualified?", choices=["ACA", "ACCA", "Other"])
    assert without_ca.answer == "Other" and not without_ca.manual_review


def test_qualification_choice_list_with_no_honest_option_requires_human_review(tmp_path):
    """If neither CA/Chartered Accountant nor Equivalent/Other is offered,
    never select ACA or ACCA to force an answer -- route to human review."""
    _, service = engine(tmp_path)
    result = service.resolve("Are you ACA or ACCA qualified?", choices=["ACA", "ACCA"])
    assert result.manual_review


def test_plain_yes_no_qualification_choices_are_unaffected_by_multichoice_logic(tmp_path):
    """A plain Yes/No choice list must still map through the ordinary
    approved-value path, not the designation-choice picker."""
    _, service = engine(tmp_path)
    result = service.resolve("Are you ACCA qualified?", choices=["Yes", "No"])
    assert result.answer == "No" and not result.manual_review


def test_tracker_81_aca_acca_answer_unaffected(tmp_path):
    """Task 21.31: Tracker 81 was already submitted accurately with
    ACA/ACCA = NO before this correction -- confirms the new policy
    produces the identical answer for that exact question, so nothing
    about the already-submitted application is contradicted."""
    _, service = engine(tmp_path)
    result = service.resolve("Are you a qualified ACA or ACCA accountant?")
    assert (result.answer, result.manual_review) == ("NO", False)


def test_country_specific_authorization_and_sponsorship_are_separate(tmp_path):
    _, service = engine(tmp_path)
    uk = service.resolve("Do you have the right to work in the UK?", market="united_kingdom")
    us = service.resolve("Are you authorized to work in the United States?", market="united_states")
    sponsor = service.resolve("Will you require visa sponsorship?", market="united_kingdom")
    assert (uk.concept, uk.answer) == ("WORK_AUTHORIZATION_UK", "NO")
    assert (us.concept, us.answer) == ("WORK_AUTHORIZATION_US", "NO")
    assert (sponsor.concept, sponsor.answer, sponsor.manual_review) == ("SPONSORSHIP_UK", "YES", False)


def test_remote_preference_is_not_work_authorization(tmp_path):
    _, service = engine(tmp_path)
    remote = service.resolve("Are you comfortable working remotely?")
    auth = service.resolve("Are you authorized to work in Australia?", market="australia")
    assert remote.concept == "REMOTE_WORK_PREFERENCE" and remote.answer == "YES"
    assert auth.concept == "WORK_AUTHORIZATION_AUSTRALIA" and auth.answer == "NO"


def test_boolean_skill_is_not_years_claim(tmp_path):
    _, service = engine(tmp_path)
    assert service.resolve("Do you have SQL experience?").answer == "YES"
    assert service.resolve("How many years of SQL experience do you have?").manual_review


def test_sensitive_questions_always_pause(tmp_path):
    _, service = engine(tmp_path)
    for question in ("Have you been convicted of a criminal offence?", "Do you hold security clearance?", "What is your ethnicity?", "Do you agree to the privacy declaration?"):
        assert service.resolve(question).manual_review


def test_generated_fintech_answer_uses_supported_transition_only(tmp_path):
    _, service = engine(tmp_path)
    result = service.resolve("Why are you moving into FinTech?", vacancy={"title": "Finance Systems Manager"})
    assert result.answer_source == "GENERATED_WITH_EVIDENCE"
    assert "15+ years" in result.answer
    assert "FinTech employment" not in result.answer


def test_generated_answer_respects_character_limit(tmp_path):
    _, service = engine(tmp_path)
    result = service.resolve("Why are you interested in this role?", vacancy={"title": "Finance Manager"})
    assert len(service.fit_character_limit(result, 80).answer) <= 80


def test_draft_and_retired_answers_never_autofill(tmp_path):
    vault, service = engine(tmp_path)
    for status in ("DRAFT", "RETIRED"):
        vault.add_or_update_answer(ApplicationAnswer("custom", "CUSTOM", "YES", automation_policy="AUTO_FILL", confidence="HIGH", answer_source="USER_APPROVED_ANSWER", status=status))
        assert service.resolve("Something unknown").manual_review


def test_manual_learning_requires_approval_and_audits(tmp_path):
    vault, service = engine(tmp_path)
    answer = ApplicationAnswer("notice", "NOTICE_PERIOD", "Two weeks", automation_policy="AUTO_FILL", confidence="HIGH", answer_source="USER_APPROVED_ANSWER")
    vault.learn_draft(answer)
    assert service.resolve("What is your notice period?").manual_review
    assert vault.approve("NOTICE_PERIOD", "Confirmed by candidate")
    result = service.resolve("What is your notice period?")
    assert result.answer == "Two weeks" and result.automation_policy == "AUTO_FILL"
    assert vault.data["audit"]


def test_date_aware_rule_and_choice_mapping(tmp_path):
    vault, service = engine(tmp_path)
    vault.data["rules"].append(ApplicationRule("future_uk", "WORK_AUTHORIZATION_UK", {"market": "united_kingdom", "effective_from": "2030-01-01"}, "YES", priority=200).to_dict())
    vault.save()
    assert service.resolve("Do you have the right to work in UK?", market="united_kingdom", application_date="2029-12-31").answer == "NO"
    assert service.resolve("Do you have the right to work in UK?", market="united_kingdom", application_date="2030-01-01").answer == "YES"
    result = service.resolve("Are you a qualified accountant?", choices=["Yes", "No"])
    assert result.answer == "Yes"


def test_unknown_and_relocation_assistance_do_not_match_sponsorship(tmp_path):
    _, service = engine(tmp_path)
    assert service.resolve("Do you require relocation assistance?").concept == "RELOCATION_ASSISTANCE"
    assert service.resolve("Do you require relocation assistance?").manual_review


def test_task20_1_contact_notice_and_start_date_rules(tmp_path):
    _, service = engine(tmp_path)
    assert service.resolve("What is your personal email address?").answer == "gautamsankr@gmail.com"
    assert service.resolve("What is your mobile number?").answer == "+9779851139824"
    assert service.resolve("How much notice do you require?").answer == "7 calendar days"
    assert service.resolve("What is your earliest start date?", application_date="2026-09-10").answer == "2026-09-17"


def test_task20_1_sponsorship_does_not_change_work_authorization(tmp_path):
    _, service = engine(tmp_path)
    for market, country in (("united_kingdom", "UK"), ("united_states", "United States"), ("australia", "Australia")):
        sponsorship = service.resolve("Will you require immigration sponsorship?", market=market)
        authorization = service.resolve(f"Are you authorized to work in {country}?", market=market)
        assert sponsorship.answer == "YES"
        assert authorization.answer == "NO"


def test_task20_1_relocation_and_travel_rules_are_market_specific(tmp_path):
    _, service = engine(tmp_path)
    assert service.resolve("Are you willing to relocate?", market="united_kingdom").answer == "YES"
    assert service.resolve("Are you willing to relocate?", market="united_states").answer == "NO"
    assert service.resolve("Are you willing to relocate?", market="australia").answer == "NO"
    assert service.resolve("Are you willing to travel?", market="united_kingdom").answer == "YES"
    assert service.resolve("Are you willing to travel?", market="united_states").answer == "NO"
    assert service.resolve("What percentage are you willing to travel?", market="united_kingdom").manual_review


def test_task20_1_salary_response_and_numeric_safety(tmp_path):
    _, service = engine(tmp_path)
    free_text = service.resolve("What is your expected salary?")
    numeric = service.resolve("What is your desired pay?", field_type="CURRENCY")
    assert "USD 30 per hour" in free_text.answer
    assert free_text.automation_policy == "AUTO_FILL_WITH_RULES"
    assert numeric.manual_review


def test_task20_1_canonical_profile_uses_current_contact_details():
    import json
    profile = json.loads(Path("app/data/master_candidate_profile.json").read_text(encoding="utf-8"))
    candidate = profile["candidate"]
    assert candidate["email"] == "gautamsankr@gmail.com"
    assert candidate["phone"] == "+9779851139824"
    assert "shankar@ghnnepal.com" not in candidate["email"]


def test_task20_1_master_resume_uses_current_contact_email():
    resume = Path("app/data/master_resume.txt").read_text(encoding="utf-8")
    assert "gautamsankr@gmail.com" in resume
    assert "shankar@gsnnepal.com" not in resume
