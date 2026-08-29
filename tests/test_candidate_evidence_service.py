"""Task 21.11/21.12: the career evidence library merges richer, verified
career facts (from the candidate's full resume / a past cover letter / the
candidate's own explicit confirmations) into generation without ever
promoting an unconfirmed or conflicting claim, and without ever letting a
claim migrate to the wrong employer or get strengthened beyond its verified
wording.

Reads the REAL evidence library (app/data/candidate_evidence_library.json)
-- it is reference data, not a fabricated fixture -- but performs no file
writes and touches no other production data."""

import pytest

from app.services.candidate_evidence_service import (
    NEEDS_CONFIRMATION,
    VERIFIED,
    enrich_board_positions,
    enrich_employment_history,
    enrich_ventures,
    get_enriched_profile,
    load_library,
    quantified_achievements,
    reconciliation_report,
)


@pytest.fixture
def library():
    return load_library()


# Task 21.12 section 12: the writing engine may rewrite/compress/select, but
# must never increase the factual strength of a VERIFIED claim beyond its
# stored wording. None of these phrases should ever appear anywhere in the
# library or an enriched profile.
FORBIDDEN_STRENGTHENED_CLAIMS = (
    "single-handedly",
    "led the entire IFRS",
    "led the IFRS transition",
    "executed a merger",
    "sole executor",
    "Chief Investment Officer",
    "Portfolio Manager",
    "Head of Investments",
    "co-developed Sewa",  # verified role is Co-Founder (business), not a developer role
    "affiliated with Xero",
    "endorsed by Xero",
    "integrates with Xero",
    "17 years",
)


def _full_profile():
    return {
        "employment_history": [
            {"company": "Australian Accounting Firm", "position": "Offshore Accounting Manager",
             "responsibilities": [], "technologies": [], "team_size": None},
            {"company": "GSN Associates", "position": "Managing Partner",
             "responsibilities": [], "technologies": [], "team_size": None},
        ],
        "board_positions": [
            {"organization": "Prabhu Mahalaxmi Life Insurance Limited", "role": "Board of Directors",
             "responsibilities": [], "achievements": []},
        ],
        "entrepreneurship": [
            {"venture": "Sewa360 ERP", "role": "Co-Founder", "achievements": []},
            {"venture": "Liberty Holdings", "role": "Co-Founder", "achievements": []},
        ],
    }


def test_library_loads_and_has_expected_sections(library):
    assert "employment_history" in library
    assert "board_positions" in library
    assert "ventures" in library
    assert "conflicts" in library
    assert len(library["conflicts"]) >= 9


def test_enrichment_adds_verified_facts_for_matching_company(library):
    profile = {
        "employment_history": [
            {"company": "Australian Accounting Firm", "position": "Offshore Accounting Manager",
             "responsibilities": ["Management Accounting"], "technologies": ["Xero"]},
        ]
    }
    enriched = get_enriched_profile(profile, library)
    entry = enriched["employment_history"][0]
    responsibilities_text = " ".join(entry["responsibilities"])
    technologies_text = " ".join(entry["technologies"])

    assert "SMSF" in responsibilities_text
    assert "ASIC" in responsibilities_text
    assert "CAS360" in technologies_text
    assert "Management Accounting" in entry["responsibilities"]
    assert "Xero" in entry["technologies"]


def test_no_forbidden_strengthened_claims_anywhere_in_enriched_full_profile(library):
    enriched = get_enriched_profile(_full_profile(), library)
    serialized = str(enriched)
    for marker in FORBIDDEN_STRENGTHENED_CLAIMS:
        assert marker not in serialized, f"forbidden strengthened claim {marker!r} leaked into enriched profile"


def test_no_matching_company_leaves_entry_untouched(library):
    profile = {"employment_history": [{"company": "Unrelated Employer", "responsibilities": ["Did work"]}]}
    enriched = get_enriched_profile(profile, library)
    assert enriched["employment_history"][0]["responsibilities"] == ["Did work"]


# --- Section 11: employer-fact isolation -----------------------------------

def test_gsn_team_size_is_40_not_12(library):
    enriched = enrich_employment_history(
        [{"company": "GSN Associates", "responsibilities": [], "technologies": []}], library
    )
    assert enriched[0]["team_size"] == 40


def test_trident_team_size_is_12_not_40(library):
    enriched = enrich_employment_history(
        [{"company": "Australian Accounting Firm", "responsibilities": [], "technologies": []}], library
    )
    assert enriched[0]["team_size"] == 12


def test_team_sizes_never_cross_contaminate_between_employers(library):
    enriched = enrich_employment_history(_full_profile()["employment_history"], library)
    by_company = {e["company"]: e for e in enriched}
    assert by_company["GSN Associates"]["team_size"] == 40
    assert by_company["Australian Accounting Firm"]["team_size"] == 12
    assert by_company["GSN Associates"]["team_size"] != by_company["Australian Accounting Firm"]["team_size"]


def test_trident_quantified_metrics_never_appear_on_gsn_entry(library):
    enriched = enrich_employment_history(_full_profile()["employment_history"], library)
    by_company = {e["company"]: e for e in enriched}
    gsn_text = str(by_company["GSN Associates"])
    for marker in ("145%", "74% to 95%", "Project Everest"):
        assert marker not in gsn_text


def test_gsn_team_achievement_never_appears_on_trident_entry(library):
    enriched = enrich_employment_history(_full_profile()["employment_history"], library)
    by_company = {e["company"]: e for e in enriched}
    trident_text = str(by_company["Australian Accounting Firm"])
    assert "40 professionals" not in trident_text


def test_project_everest_is_scoped_to_trident_only(library):
    enriched = enrich_employment_history(_full_profile()["employment_history"], library)
    by_company = {e["company"]: e for e in enriched}
    assert "Project Everest" in str(by_company["Australian Accounting Firm"])
    assert "Project Everest" not in str(by_company["GSN Associates"])


def test_project_everest_wording_does_not_claim_sole_credit(library):
    enriched = enrich_employment_history(
        [{"company": "Australian Accounting Firm", "responsibilities": [], "technologies": []}], library
    )
    text = " ".join(enriched[0]["responsibilities"])
    assert "Co-led Project Everest with a partner of Trident Financial Group." in text


# --- Board / Prabhu Mahalaxmi -----------------------------------------------

def test_prabhu_board_achievements_use_careful_non_strengthened_wording(library):
    enriched = enrich_board_positions(
        [{"organization": "Prabhu Mahalaxmi Life Insurance Limited", "responsibilities": [], "achievements": []}],
        library,
    )
    achievements_text = " ".join(enriched[0]["achievements"])
    assert "USD 140 million" in achievements_text
    assert "board/governance capacity" in achievements_text
    assert "Participated in the transition" in achievements_text
    assert "key member" in achievements_text
    for marker in FORBIDDEN_STRENGTHENED_CLAIMS:
        assert marker not in achievements_text


# --- Ventures: Sewa360 / Liberty Holdings -----------------------------------

def test_sewa360_venture_enriched_with_14_firm_adoption(library):
    enriched = enrich_ventures([{"venture": "Sewa360 ERP", "role": "Co-Founder", "achievements": []}], library)
    achievements_text = " ".join(enriched[0]["achievements"])
    assert "14 firms" in achievements_text
    assert "Co-founded" in achievements_text
    assert "co-developed" not in achievements_text.lower().replace("co-founded", "")


def test_sewa360_description_does_not_imply_xero_affiliation(library):
    enriched = enrich_ventures([{"venture": "Sewa360 ERP", "role": "Co-Founder"}], library)
    description = enriched[0].get("description", "")
    assert "Xero Practice Manager" in description  # comparison is allowed
    for marker in ("affiliated with Xero", "endorsed by Xero", "integrates with Xero", "identical functionality"):
        assert marker not in description or "no affiliation" in description.lower()


def test_liberty_holdings_venture_enriched_without_inventing_details(library):
    enriched = enrich_ventures([{"venture": "Liberty Holdings", "role": "Co-Founder", "achievements": []}], library)
    description = enriched[0].get("description", "")
    assert description == "Co-founded Liberty Holdings."
    for marker in ("%", "$", "USD", "revenue", "valuation", "employees"):
        assert marker not in description


# --- Reconciliation status ---------------------------------------------------

def test_all_task_21_11_conflicts_are_now_resolved(library):
    report = reconciliation_report(library)
    assert len(report) >= 9
    for conflict in report:
        assert conflict["status"] == "RESOLVED"
        assert conflict["requires_user_confirmation"] is False
        assert conflict.get("authoritative_value")


def test_sewa360_adoption_conflict_resolved_to_14_firms(library):
    report = reconciliation_report(library)
    sewa_conflict = next(c for c in report if "Sewa360 adoption scale" in c["fact"])
    assert sewa_conflict["status"] == "RESOLVED"
    assert "14 firms" in sewa_conflict["authoritative_value"]


def test_no_needs_confirmation_items_remain(library):
    assert quantified_achievements(library, NEEDS_CONFIRMATION) == []


def test_verified_quantified_achievements_include_all_resolved_facts(library):
    verified_texts = " ".join(item["text"] for item in quantified_achievements(library, VERIFIED))
    assert "12 employees" in verified_texts
    assert "145%" in verified_texts
    assert "74% to 95%" in verified_texts
    assert "40 professionals" in verified_texts


# --- Period corrections -------------------------------------------------

def test_trident_period_correction_is_applied(library):
    profile = {"employment_history": [{"company": "Australian Accounting Firm", "period": "2024 - 2025"}]}
    enriched = get_enriched_profile(profile, library)
    assert enriched["employment_history"][0]["period"] == "July 2023 - October 2025"


def test_gsn_period_correction_is_applied_and_no_longer_says_present(library):
    profile = {"employment_history": [{"company": "GSN Associates", "period": "2022 - Present"}]}
    enriched = get_enriched_profile(profile, library)
    assert enriched["employment_history"][0]["period"] == "April 2012 - July 2023"
    assert "Present" not in enriched["employment_history"][0]["period"]


def test_enrich_employment_history_deduplicates_case_insensitively(library):
    profile_entry = [{"company": "Australian Accounting Firm", "responsibilities": ["SMSF work already listed"]}]
    enriched = enrich_employment_history(profile_entry, library)
    responsibilities = enriched[0]["responsibilities"]
    assert len(responsibilities) == len(set(r.lower() for r in responsibilities))
