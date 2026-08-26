from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.ai_service import analyze_job
from app.services.ats import ATSEngine
from app.services.career_engine import CareerDecisionEngine
from app.services.cover_letter_generator import CoverLetterGenerator
from app.services.docx_service import generate_resume_docx
from app.services.employer_service import EmployerService
from app.services.profile_service import ProfileService
from app.services.resume_composer import ResumeComposer
from app.services.resume_generator import ResumeGenerator
from app.services.resume_optimizer import ResumeOptimizer
from app.services.recruiter_reasoning_service import RecruiterReasoningService
from app.config import SCREENING_AUTO_APPLY


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
    cover_letter_markdown_path: str
    cover_letter_docx_path: str


@dataclass(frozen=True)
class JobEvaluation:
    """Evaluation output retained before any application documents are made."""

    profile: dict[str, Any]
    job_analysis: dict[str, Any]
    employer: Any
    career_decision: Any
    ats_result: dict[str, Any]
    screening_decision: str
    recruiter: Any = None


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
        self.cover_letter_generator = CoverLetterGenerator()
        self.recruiter_reasoning = RecruiterReasoningService()

    def evaluate_job(self, job_description: str) -> JobEvaluation:
        """Analyze and score a job without generating application documents."""
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
        recruiter = self.recruiter_reasoning.evaluate(
            profile,
            job_analysis,
            employer,
            career_decision,
        )

        return JobEvaluation(
            profile=profile,
            job_analysis=job_analysis,
            employer=employer,
            career_decision=career_decision,
            ats_result=ats_result,
            screening_decision=career_decision.decision,
            recruiter=recruiter,
        )

    def generate_application_documents(
        self,
        evaluation: JobEvaluation,
        manual: bool = False,
    ) -> ApplicationResult:
        """Generate the application package for an eligible evaluation."""
        if evaluation.screening_decision != SCREENING_AUTO_APPLY and not manual:
            raise ValueError(
                "Application documents require an AUTO_APPLY evaluation "
                "unless manual generation is explicitly requested."
            )

        profile = evaluation.profile
        job_analysis = evaluation.job_analysis
        career_decision = evaluation.career_decision
        ats_result = evaluation.ats_result
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
        
        cover_letter_markdown_path, cover_letter_docx_path = self.cover_letter_generator.generate(
            profile,
            job_analysis,
            career_decision,
            resume_strategy
        )

        return ApplicationResult(
            profile=profile,
            job_analysis=job_analysis,
            employer=evaluation.employer,
            career_decision=evaluation.career_decision,
            ats_result=ats_result,
            resume_strategy=resume_strategy,
            resume_composition=resume_composition,
            markdown_path=markdown_path,
            docx_path=docx_path,
            cover_letter_markdown_path=cover_letter_markdown_path,
            cover_letter_docx_path=cover_letter_docx_path,
        )
