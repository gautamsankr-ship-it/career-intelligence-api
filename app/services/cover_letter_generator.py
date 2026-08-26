from pathlib import Path
from app.services.cover_letter_service import generate_cover_letter
from app.services.docx_service import generate_cover_letter_docx

class CoverLetterGenerator:
    """Orchestrates Cover Letter generation."""

    def generate(
        self,
        profile: dict,
        job_analysis: dict,
        career_decision: any,
        resume_strategy: dict
    ) -> tuple[str, str]:
        """
        Generates Markdown and DOCX cover letter.
        """

        # 1. Generate text using AI
        cover_letter_text = generate_cover_letter(
            profile,
            job_analysis,
            career_decision
        )

        # 2. Setup path
        company = job_analysis.get("company", "Unknown Company")
        job_title = job_analysis.get("job_title", "Unknown Position")

        # 3. Save Markdown
        folder = Path("applications") / f"{company.replace('/', '-').strip()}_{job_title.replace('/', '-').strip()}"
        if not company or company == "Unknown Company":
             folder = Path("applications") / f"Unknown Company_{job_title.replace('/', '-').strip()}"
        
        folder.mkdir(parents=True, exist_ok=True)
        markdown_path = folder / "CoverLetter.md"
        with open(markdown_path, "w", encoding="utf-8") as f:
            f.write(cover_letter_text)

        # 4. Save DOCX
        docx_path = generate_cover_letter_docx(
            cover_letter_text,
            company=company,
            job_title=job_title
        )

        return str(markdown_path), str(docx_path)
