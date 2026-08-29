"""Task 21.11 Addendum: CoverLetterGenerator must draw on the career
evidence library (VERIFIED facts only) rather than the raw simplified
profile, and must never pass an unconfirmed/conflicting claim into the AI
prompt. Hermetic: the real OpenAI call and real docx/file writes are
monkeypatched out; no production data is written."""

from pathlib import Path

import app.services.cover_letter_generator as cover_letter_generator_module


def test_generate_passes_enriched_profile_not_raw_profile(tmp_path, monkeypatch):
    real_evidence_library = Path.cwd() / "app" / "data" / "candidate_evidence_library.json"
    library_dir = tmp_path / "app" / "data"
    library_dir.mkdir(parents=True, exist_ok=True)
    (library_dir / "candidate_evidence_library.json").write_text(
        real_evidence_library.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    captured = {}

    def fake_generate_cover_letter(profile, job_analysis, career_decision):
        captured["profile"] = profile
        return "Dear Hiring Manager, ... Sincerely, Shankar Gautam"

    def fake_generate_cover_letter_docx(text, company, job_title):
        return str(tmp_path / "CoverLetter.docx")

    monkeypatch.setattr(cover_letter_generator_module, "generate_cover_letter", fake_generate_cover_letter)
    monkeypatch.setattr(cover_letter_generator_module, "generate_cover_letter_docx", fake_generate_cover_letter_docx)

    raw_profile = {
        "candidate": {"full_name": "Shankar Gautam"},
        "employment_history": [
            {"company": "Australian Accounting Firm", "position": "Offshore Accounting Manager",
             "responsibilities": ["Management Accounting"], "technologies": ["Xero"]},
        ],
    }

    cover_letter_generator_module.CoverLetterGenerator().generate(
        raw_profile, {"company": "EnVision Partners", "job_title": "Tax Accountant"}, None, {},
    )

    enriched_entry = captured["profile"]["employment_history"][0]
    # Evidence-library enrichment reached the AI prompt's input.
    assert "SMSF" in " ".join(enriched_entry["responsibilities"])
    # Facts belonging to GSN/board -- entirely absent from this raw_profile --
    # must never leak in just because they exist elsewhere in the library.
    serialized = str(captured["profile"])
    for marker in ("$140 million", "40 professionals", "17 years", "single-handedly"):
        assert marker not in serialized
