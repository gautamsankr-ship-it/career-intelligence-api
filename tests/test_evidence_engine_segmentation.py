"""Task 21.15J: EvidenceEngine indexes most profile fields as short,
single-fact atomic terms (skills, technology, experience.*,
responsibilities.*, education, project fields) -- those are left completely
unchanged. The three professional_summary fields (headline/career_direction/
value_proposition) are genuinely different: each packs several distinct
facts into one long, comma/"and"-joined sentence, so word-overlap matching
against the whole sentence dilutes a real, verified phrase into
near-invisibility. Confirmed root cause (Task 21.15H): a requirement of
"public practice experience" could not retrieve the candidate's verified
"Australian public practice" because it was buried inside a 26-word atomic
headline term.

This fix adds clause-level segments (split on comma/semicolon/" and "/" & ",
3+ words only) as ADDITIONAL candidate terms alongside the existing full-
sentence term -- purely additive, no fact invented, every segment an exact
substring of the original verified text.

These tests use the REAL master_candidate_profile.json (read-only, same
convention as test_industry_scoring.py / test_candidate_evidence_service.py)
since EvidenceEngine has no injection point for a fake profile.
"""

from app.services.evidence_engine import EvidenceEngine


def _engine():
    return EvidenceEngine()


# --- Required regression case ------------------------------------------------

def test_public_practice_experience_now_matches_verified_headline_evidence():
    engine = _engine()
    result = engine.evidence_score("public practice experience")
    assert result["score"] >= 6  # a factual-supported-requirement strength, not a perfect fabricated match
    assert "public practice" in result["matched"]


def test_public_practice_match_carries_provenance_to_its_source_sentence():
    engine = _engine()
    result = engine.evidence_score("public practice experience")
    provenance = result["provenance"]
    assert provenance is not None
    assert provenance["source_field"] == "professional_summary.headline"
    assert "Australian public practice" in provenance["source_text"]
    assert provenance["derived_segment"] == "Australian public practice"


def test_original_full_sentence_term_is_preserved_not_replaced():
    """Segmentation must be additive -- the pre-existing atomic full-sentence
    term must still exist and still match itself."""
    engine = _engine()
    headline = engine.profile["professional_summary"]["headline"]
    result = engine.evidence_score(headline)
    assert result["score"] == 10


# --- False-positive controls (Task 21.15J Section 5) -------------------------

def test_generic_accounting_terms_are_unaffected_by_segmentation():
    """These already scored via pre-existing short atomic terms (a
    documented, out-of-scope EvidenceEngine fuzzy-matching characteristic --
    see Task 21.15G/H). provenance is None, proving this fix's new segments
    are not the cause and did not change this pre-existing behaviour."""
    engine = _engine()
    for term in ("accounting", "forensic accounting", "insurance claims", "infrastructure"):
        result = engine.evidence_score(term)
        assert result["provenance"] is None, f"{term!r} should not be matched via a derived segment"


def test_blockchain_analytics_does_not_receive_strong_evidence_from_segmentation():
    """A specialized capability the candidate does not have must not become
    strongly supported merely because a derived segment shares one generic
    word ("analytics") with it."""
    engine = _engine()
    result = engine.evidence_score("blockchain analytics")
    assert result["score"] < 7  # never STRONG_EVIDENCE-tier
    assert result["provenance"] is None  # not attributable to this fix's new segments


def test_short_bare_word_clauses_are_not_promoted_to_standalone_terms():
    """Single- or two-word clauses split out of the headline/career_direction
    text (e.g. "taxation", "Data Analytics") must not become their own
    freestanding candidate terms -- confirmed during Task 21.15J testing to
    create exactly this kind of generic-word false-positive risk."""
    engine = _engine()
    for normalized_segment in engine._term_provenance:
        assert len(normalized_segment.split()) >= 3


def test_non_prose_profile_fields_remain_unsegmented():
    """Only the three professional_summary fields are segmented -- skills,
    responsibilities, experience, education, and project fields (several of
    which contain their own internal commas, e.g. a responsibilities bullet
    like "Prepared monthly, quarterly and annual financial statements.")
    must remain single atomic terms, exactly as before this fix."""
    engine = _engine()
    allowed_prefixes = ("professional_summary.",)
    for provenance in engine._term_provenance.values():
        assert provenance["source_field"].startswith(allowed_prefixes)


def test_segmentation_is_deterministic_across_repeated_construction():
    first = _engine()
    second = _engine()
    assert first.candidate_terms == second.candidate_terms
    assert first._term_provenance == second._term_provenance
