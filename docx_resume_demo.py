"""Manual resume .docx generation demo — NOT a pytest test.

Writes a REAL .docx file into the production applications/ directory
(docx_service.OUTPUT_DIR = Path("applications")). Must be run deliberately
from the command line only; must never be imported or collected by pytest.
"""

from app.services.docx_service import generate_resume_docx


def main() -> None:
    markdown_resume = """# Candidate Name

## Professional Summary

Financial Data Analyst with experience in reporting and analytics.
"""

    filename = generate_resume_docx(
        markdown_resume,
        company="Bamboo",
        job_title="Financial Data Analyst",
    )
    print(filename)


if __name__ == "__main__":
    main()
