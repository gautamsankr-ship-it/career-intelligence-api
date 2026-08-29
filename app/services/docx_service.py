import re
from pathlib import Path
from datetime import datetime
import json

from docx import Document
from docx.shared import Pt


OUTPUT_DIR = Path("applications")
OUTPUT_DIR.mkdir(exist_ok=True)

_BOLD_SPLIT = re.compile(r"(\*\*.+?\*\*)")


def _apply_compact_paragraph_spacing(document):
    """python-docx's built-in blank-document template carries Word's default
    ~10pt space-after + ~1.15 line spacing on every paragraph, plus 1in/
    1.25in page margins -- reasonable for essay prose, but it turns a
    dense, mostly single-line-bullet resume into several pages of
    whitespace rather than content (Task 21.13 section 5). Tightening
    spacing/margins to common professional-resume conventions lets the
    rendered page count reflect actual content density instead of template
    padding, without removing or shortening any actual evidence."""
    for style_name in ("Normal", "List Bullet", "List Bullet 2", "List Bullet 3"):
        try:
            style = document.styles[style_name]
        except KeyError:
            continue
        paragraph_format = style.paragraph_format
        paragraph_format.space_before = Pt(0)
        paragraph_format.space_after = Pt(2)
        paragraph_format.line_spacing = 1.0

    for level in (1, 2, 3):
        try:
            style = document.styles[f"Heading {level}"]
        except KeyError:
            continue
        paragraph_format = style.paragraph_format
        paragraph_format.space_before = Pt(10 if level == 1 else 6)
        paragraph_format.space_after = Pt(2)
        paragraph_format.line_spacing = 1.0

    for section in document.sections:
        section.top_margin = Pt(54)     # 0.75"
        section.bottom_margin = Pt(54)
        section.left_margin = Pt(54)
        section.right_margin = Pt(54)


def _add_inline_runs(paragraph, text):
    """Split `**bold**` markers out of a line and add real bold/plain runs."""

    for segment in _BOLD_SPLIT.split(text):

        if not segment:
            continue

        if segment.startswith("**") and segment.endswith("**") and len(segment) > 4:
            paragraph.add_run(segment[2:-2]).bold = True
        else:
            paragraph.add_run(segment)


def _write_markdown_to_docx(document, markdown_text):
    """Render simple resume/cover-letter Markdown as real Word formatting.

    Supports the subset actually produced by ResumeGenerator/CoverLetterGenerator:
    #/##/### headings, **bold** (inline or whole-line), "- " bullet lists, a
    "---" horizontal rule (dropped, no literal Word equivalent), and plain
    paragraphs. Never writes literal Markdown syntax or raw Python
    dict/list reprs into the document.
    """

    for line in markdown_text.split("\n"):

        stripped = line.strip()

        if not stripped:
            continue

        if stripped == "---":
            continue

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", stripped)

        if heading_match:
            level = len(heading_match.group(1))
            document.add_heading(heading_match.group(2), level=level)
            continue

        if stripped.startswith("- "):
            paragraph = document.add_paragraph(style="List Bullet")
            _add_inline_runs(paragraph, stripped[2:])
            continue

        paragraph = document.add_paragraph()
        _add_inline_runs(paragraph, stripped)


def _create_application_folder(company, job_title):

    company = (company or "").replace("/", "-").strip()

    job_title = (job_title or "Unknown Position").replace("/", "-").strip()

    if not company:
        company = "Unknown Company"

    folder = OUTPUT_DIR / f"{company}_{job_title}"

    folder.mkdir(parents=True, exist_ok=True)

    return folder


def generate_resume_docx(
    resume_text,
    company="Company",
    job_title="Position"
):

    folder = _create_application_folder(
        company,
        job_title
    )

    filename = folder / "Resume.docx"

    document = Document()
    _apply_compact_paragraph_spacing(document)

    _write_markdown_to_docx(document, resume_text)

    document.save(filename)

    return str(filename)


def generate_cover_letter_docx(
    cover_letter,
    company="Company",
    job_title="Position"
):

    folder = _create_application_folder(
        company,
        job_title
    )

    filename = folder / "CoverLetter.docx"

    document = Document()
    _apply_compact_paragraph_spacing(document)

    document.add_heading(
        "Cover Letter",
        level=1
    )

    _write_markdown_to_docx(document, cover_letter)

    document.save(filename)

    return str(filename)


def save_career_report(
    decision,
    employer,
    company,
    job_title
):

    folder = _create_application_folder(
        company,
        job_title
    )

    report = {

        "generated_on":
            datetime.now().isoformat(),

        "company":
            company,

        "job_title":
            job_title,

        "overall_score":
            decision.overall_score,

        "confidence":
            decision.confidence,

        "decision":
            decision.decision,

        "priority":
            decision.priority,

        "automation":
            decision.automation_level,

        "resume_strategy":
            decision.resume_strategy,

        "cover_letter_strategy":
            decision.cover_letter_strategy,

        "application_strategy":
            decision.application_strategy,

        "recommendations":
            decision.recommendations,

        "employer": {

            "company":
                employer.company,

            "industry":
                employer.industry,

            "overall_score":
                employer.overall_score,

            "strengths":
                employer.strengths,

            "risks":
                employer.risks

        }

    }

    filename = folder / "career_report.json"

    with open(filename, "w", encoding="utf-8") as f:

        json.dump(
            report,
            f,
            indent=4,
            ensure_ascii=False
        )

    return str(filename)