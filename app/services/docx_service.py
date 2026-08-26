from pathlib import Path
from datetime import datetime
import json

from docx import Document


OUTPUT_DIR = Path("applications")
OUTPUT_DIR.mkdir(exist_ok=True)


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

    document.add_heading(
        "Optimized Resume",
        level=1
    )

    for line in resume_text.split("\n"):

        if line.strip():

            document.add_paragraph(line)

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

    document.add_heading(
        "Cover Letter",
        level=1
    )

    for line in cover_letter.split("\n"):

        if line.strip():

            document.add_paragraph(line)

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