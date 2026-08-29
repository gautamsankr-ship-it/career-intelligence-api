"""Regression for Task 21.10A: ResumeGenerator produced malformed headings
("###  | Public Company", "### Founder | ") when board_positions/entrepreneurship
profile entries used different field names than the renderer expected (role vs
designation, venture vs company), and dumped raw Python dicts as text for any
non-list technical_capabilities/software value. All inputs here are synthetic --
this never reads the production candidate profile."""

from pathlib import Path

import pytest

from app.services.resume_generator import ResumeGenerator


def _composition(**overrides):
    base = {
        "branding": {"name": "Jane Candidate", "designation": "Senior Accountant", "email": "jane@example.test"},
        "summary": {},
        "skills": {},
        "experience": {},
        "projects": {},
        "education": {},
        "ats": {},
    }
    base.update(overrides)
    return base


def _generate(tmp_path, monkeypatch, composition, include_ats_summary=False):
    monkeypatch.chdir(tmp_path)
    path = ResumeGenerator().generate(composition, {"job_title": "Senior Accountant"}, include_ats_summary=include_ats_summary)
    return Path(path).read_text(encoding="utf-8")


def test_board_position_uses_role_field_when_designation_absent(tmp_path, monkeypatch):
    """Mirrors the real profile shape: {"organization": ..., "role": ...}, no "designation" key."""
    composition = _composition(experience={
        "board_positions": [{"organization": "Public Company", "role": "Board Director", "responsibilities": []}],
    })
    text = _generate(tmp_path, monkeypatch, composition)
    assert "### Board Director | Public Company" in text
    assert "###  | Public Company" not in text


def test_entrepreneurship_uses_venture_field_when_company_absent(tmp_path, monkeypatch):
    """Mirrors the real profile shape: {"venture": ..., "role": "Founder"}, no "company" key."""
    composition = _composition(experience={
        "entrepreneurship": [{"venture": "Career Intelligence Platform", "role": "Founder", "status": "In Progress"}],
    })
    text = _generate(tmp_path, monkeypatch, composition)
    assert "### Founder | Career Intelligence Platform" in text
    assert "### Founder | \n" not in text
    assert "### Founder |\n" not in text


def test_employment_history_uses_position_field_when_title_absent(tmp_path, monkeypatch):
    """Mirrors the real profile shape: {"company": ..., "position": ...}, no "title" key."""
    composition = _composition(experience={
        "employment_history": [{"company": "GSN Associates", "position": "Managing Partner", "responsibilities": []}],
    })
    text = _generate(tmp_path, monkeypatch, composition)
    assert "### Managing Partner | GSN Associates" in text


def test_education_uses_qualification_field_when_degree_absent(tmp_path, monkeypatch):
    """Mirrors the real profile shape: {"qualification": ..., "institution": ...}, no "degree" key."""
    composition = _composition(education={
        "education": [{"qualification": "Chartered Accountant", "institution": "ICAI"}],
    })
    text = _generate(tmp_path, monkeypatch, composition)
    assert "### Chartered Accountant" in text
    assert "### \n" not in text


def test_board_and_entrepreneurship_entries_missing_all_name_fields_omit_heading_not_malformed(tmp_path, monkeypatch):
    composition = _composition(experience={
        "board_positions": [{"responsibilities": ["Governance"]}],
        "entrepreneurship": [{"status": "Prototype"}],
    })
    text = _generate(tmp_path, monkeypatch, composition)
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("###"):
            assert stripped not in {"###", "### |", "###  |", "### |  "}
            assert stripped != "###"


def test_technical_skills_dict_renders_as_readable_bullets_not_python_repr(tmp_path, monkeypatch):
    composition = _composition(skills={
        "technical": {"artificial_intelligence": ["OpenAI API", "Prompt Engineering"], "automation": ["Workflow Automation"]},
        "software": {"accounting": ["Xero", "MYOB"]},
    })
    text = _generate(tmp_path, monkeypatch, composition)
    assert "{'artificial_intelligence'" not in text
    assert "{'accounting'" not in text
    assert "- **artificial_intelligence:** OpenAI API, Prompt Engineering" in text
    assert "- **accounting:** Xero, MYOB" in text


def test_technical_skills_list_still_renders_as_before(tmp_path, monkeypatch):
    """Confirm the pre-existing list-handling branch is unaffected by the dict fix."""
    composition = _composition(skills={"technical": ["Python", "SQL"]})
    text = _generate(tmp_path, monkeypatch, composition)
    assert "Python, SQL" in text


def test_ats_summary_absent_by_default_from_employer_facing_output(tmp_path, monkeypatch):
    """Task 21.10C: ATS score/grade/interview-probability/keyword diagnostics are
    internal-only and must never appear in employer-facing output by default."""
    composition = _composition(ats={
        "score": 71.2, "grade": "C", "interview_probability": 68, "coverage": 0.76,
        "recommendation": "Worth Interview", "missing": ["Some missing keyword"],
        "strengthen": ["Some keyword"],
    })
    text = _generate(tmp_path, monkeypatch, composition)
    for forbidden in ("ATS Optimization Summary", "ATS Score", "Grade:", "Interview Probability",
                      "Keyword Coverage", "Missing Keywords", "Keywords to Strengthen", "71.2"):
        assert forbidden not in text


def test_ats_summary_still_available_for_explicit_internal_use(tmp_path, monkeypatch):
    """The capability is preserved for internal callers that explicitly opt in."""
    composition = _composition(ats={"score": 71.2, "grade": "C"})
    text = _generate(tmp_path, monkeypatch, composition, include_ats_summary=True)
    assert "ATS Optimization Summary" in text
    assert "71.2" in text


def test_target_position_label_is_not_rendered(tmp_path, monkeypatch):
    """'Target Position:' is an internal job-search-tool artifact, never shown
    to an employer; the designation already appears under the candidate's name."""
    composition = _composition(summary={"headline": "Tax & Business Advisory Accountant"})
    text = _generate(tmp_path, monkeypatch, composition)
    assert "Target Position" not in text
