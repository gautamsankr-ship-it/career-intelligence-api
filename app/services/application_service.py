from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.ai_service import analyze_job
from app.services.ats import ATSEngine
from app.services.career_engine import CareerDecisionEngine
from app.services.docx_service import generate_resume_docx
from app.services.employer_service import EmployerService
from app.services.profile_service import ProfileService
from app.services.resume_composer import ResumeComposer
from app.services.resume_generator import ResumeGenerator
from app.services.resume_optimizer import ResumeOptimizer


@dataclass(frozen=True)
class ApplicationResult:
    """Artifacts produced by the layered application pipeline."""

    profile: dict[str, Any]
    job_analysis: dict[str, Any]
    employer: Any
    career_decision: Any
    ats_result: dict[str, Any]
    resume_strategy: dict[str, Any]
    resume_composition: dict[str, Any]
    markdown_path: str
    docx_path: str


class ApplicationService:
    """Orchestrates the supported profile-to-DOCX application pipeline."""

    def __init__(self) -> None:
        self.profile_loader = ProfileService()
        self.employer_analyzer = EmployerService()
        self.career_engine = CareerDecisionEngine()
        self.ats_engine = ATSEngine()
        self.resume_strategy_engine = ResumeOptimizer()
        self.resume_composer = ResumeComposer()
        self.resume_generator = ResumeGenerator()

    def generate_documents(self, job_description: str) -> ApplicationResult:
        """Generate Markdown and DOCX resume artifacts for one job description."""
        if not job_description or not job_description.strip():
            raise ValueError("job_description must not be empty")

        profile = self.profile_loader.get_profile()
        job_analysis = analyze_job(job_description)
        employer = self.employer_analyzer.analyze(job_analysis)
        career_decision = self.career_engine.evaluate(
            profile,
            job_analysis,
            employer,
        )
        ats_result = self.ats_engine.analyze(job_analysis)
        resume_strategy = self.resume_strategy_engine.optimize(
            career_decision,
            ats_result,
            job_analysis,
        )
        resume_composition = self.resume_composer.compose(
            profile,
            job_analysis,
            career_decision,
            ats_result,
            resume_strategy,
        )
        markdown_path = self.resume_generator.generate(
            resume_composition,
            job_analysis,
        )
        markdown = Path(markdown_path).read_text(encoding="utf-8")
        docx_path = generate_resume_docx(
            markdown,
            company=job_analysis.get("company", "Company"),
            job_title=job_analysis.get("job_title", "Position"),
        )

        return ApplicationResult(
            profile=profile,
            job_analysis=job_analysis,
            employer=employer,
            career_decision=career_decision,
            ats_result=ats_result,
            resume_strategy=resume_strategy,
            resume_composition=resume_composition,
            markdown_path=markdown_path,
            docx_path=docx_path,
        )
