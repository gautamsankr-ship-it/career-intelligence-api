"""Regression for Task 21.10A: generate_resume_docx/generate_cover_letter_docx used to
dump Markdown text verbatim into Word paragraphs (literal #, ##, **), and any
non-list skill/software value was stringified with str(dict). These tests exercise
the fix (_write_markdown_to_docx / _add_inline_runs) directly and end-to-end,
never touching the production applications/ directory."""

import docx

from app.services import docx_service
from app.services.docx_service import _write_markdown_to_docx, generate_resume_docx


SAMPLE_MARKDOWN = """# Jane Candidate

**Senior Accountant**

jane@example.test | +1 5551234

---

## Professional Summary

**Target Position:** Senior Accountant

**Core Focus Areas**
- Tax planning
- Financial reporting

## Core Competencies

**Software**
- **accounting:** Xero, MYOB
- **analytics:** Excel, Power BI
"""


def _render(markdown_text):
    document = docx.Document()
    _write_markdown_to_docx(document, markdown_text)
    return document


def _paragraph_texts(document):
    return [p.text for p in document.paragraphs]


def test_headings_become_real_word_headings_not_literal_hashes():
    document = _render(SAMPLE_MARKDOWN)
    heading_paragraphs = [p for p in document.paragraphs if p.style.name.startswith("Heading")]
    heading_texts = [p.text for p in heading_paragraphs]
    assert "Jane Candidate" in heading_texts
    assert "Professional Summary" in heading_texts
    assert "Core Competencies" in heading_texts
    for text in _paragraph_texts(document):
        assert not text.lstrip().startswith("#")


def test_bold_markers_become_real_bold_runs_not_literal_asterisks():
    document = _render(SAMPLE_MARKDOWN)
    texts = _paragraph_texts(document)
    assert not any("**" in text for text in texts)
    bold_runs = [run for p in document.paragraphs for run in p.runs if run.bold]
    assert any(run.text == "Senior Accountant" for run in bold_runs)
    assert any(run.text == "Target Position:" for run in bold_runs)


def test_bullets_render_as_list_paragraphs_without_leading_dash():
    document = _render(SAMPLE_MARKDOWN)
    bullets = [p for p in document.paragraphs if p.style.name == "List Bullet"]
    bullet_texts = [p.text for p in bullets]
    assert "Tax planning" in bullet_texts
    assert "Financial reporting" in bullet_texts
    assert not any(text.startswith("-") for text in bullet_texts)


def test_horizontal_rule_is_dropped_not_rendered_literally():
    document = _render(SAMPLE_MARKDOWN)
    assert "---" not in _paragraph_texts(document)


def test_structured_dict_bullets_render_readably_not_as_python_repr():
    """The composer-side dict->bullet formatting (already fixed in resume_generator.py)
    produces "- **category:** a, b" lines; confirm the DOCX renderer turns the bold
    category prefix into a real run rather than ever needing str(dict) fallback."""
    document = _render(SAMPLE_MARKDOWN)
    texts = _paragraph_texts(document)
    assert "accounting: Xero, MYOB" in texts
    assert not any(text.startswith("{") or "':" in text for text in texts)


def test_generate_resume_docx_has_no_internal_optimized_resume_label(tmp_path, monkeypatch):
    """Task 21.10C: 'Optimized Resume' is an internal job-search-tool artifact
    and must never appear on an employer-facing document."""
    monkeypatch.setattr(docx_service, "OUTPUT_DIR", tmp_path)
    path = generate_resume_docx(SAMPLE_MARKDOWN, company="Acme Co", job_title="Senior Accountant")
    document = docx.Document(path)
    assert "Optimized Resume" not in _paragraph_texts(document)


def test_generate_resume_docx_end_to_end_is_isolated_and_clean(tmp_path, monkeypatch):
    """Full public entry point, but redirected away from the real applications/
    directory so this test never touches production data (Task 21.8D.3 lesson)."""
    monkeypatch.setattr(docx_service, "OUTPUT_DIR", tmp_path)
    path = generate_resume_docx(SAMPLE_MARKDOWN, company="Acme Co", job_title="Senior Accountant")
    assert str(tmp_path) in path
    document = docx.Document(path)
    texts = _paragraph_texts(document)
    assert not any("**" in t or t.lstrip().startswith("#") for t in texts)
    assert "Jane Candidate" in texts + [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]


def test_whole_line_bold_has_no_literal_asterisks():
    document = _render("**Only Bold Line**")
    assert document.paragraphs[0].text == "Only Bold Line"
    assert document.paragraphs[0].runs[0].bold is True


def test_generate_resume_docx_uses_compact_paragraph_spacing(tmp_path, monkeypatch):
    """Task 21.13 section 5: Word's blank-document default (~10pt space-after,
    ~1.15 line spacing on every paragraph, 1in/1.25in margins) turns a dense,
    mostly single-line-bullet resume into several pages of whitespace rather
    than content. generate_resume_docx must apply tighter, professional-
    resume-appropriate spacing/margins instead of the raw template default."""
    monkeypatch.setattr(docx_service, "OUTPUT_DIR", tmp_path)
    path = generate_resume_docx(SAMPLE_MARKDOWN, company="Acme Co", job_title="Senior Accountant")
    document = docx.Document(path)

    normal_format = document.styles["Normal"].paragraph_format
    assert normal_format.line_spacing == 1.0
    assert normal_format.space_after is not None
    assert normal_format.space_after.pt < 10

    bullet_format = document.styles["List Bullet"].paragraph_format
    assert bullet_format.line_spacing == 1.0

    section = document.sections[0]
    assert section.left_margin.inches <= 1.0
    assert section.right_margin.inches <= 1.0


def test_heading_only_line_has_no_literal_hashes():
    document = _render("### Heading Only")
    assert document.paragraphs[0].text == "Heading Only"
    assert document.paragraphs[0].style.name == "Heading 3"
