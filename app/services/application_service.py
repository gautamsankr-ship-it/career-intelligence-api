import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.ai_service import analyze_job
from app.services.application_email_composer import ApplicationEmailComposer
from app.services.application_quality_gate import check_cover_letter, check_custom_response, check_email, check_resume
from app.services.application_requirements import ApplicationRequirementService, ApplicationRequirements
from app.services.ats import ATSEngine
from app.services.career_engine import CareerDecisionEngine
from app.services.cover_letter_generator import CoverLetterGenerator
from app.services.custom_response_generator import CustomResponseGenerator
from app.services.docx_service import generate_resume_docx
from app.services.employer_service import EmployerService
from app.services.profile_service import ProfileService
from app.services.resume_composer import ResumeComposer
from app.services.resume_generator import ResumeGenerator
from app.services.resume_optimizer import ResumeOptimizer
from app.services.resume_relevance import extract_vacancy_keywords
from app.services.recruiter_reasoning_service import RecruiterReasoningService
from app.services.remote_work_eligibility import INELIGIBLE, MANUAL_REVIEW, RemoteWorkEligibilityClassifier
from app.services.job_intelligence_service import opportunity_shim_from_job_analysis
from app.config import GMAIL_SENDER_ADDRESS, SCREENING_AUTO_APPLY


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
class ApplicationPackagePlan:
    """Vacancy-driven application package: only the materials the employer
    actually asked for, each traceable back to the requirement evidence
    that caused it to be generated. Preparing a plan is NOT submission --
    see ApplicationPackagePlan.manifest for a human-reviewable summary, and
    no tracker/Gmail state is touched by producing one."""

    requirements: ApplicationRequirements
    resume_markdown_path: str | None
    resume_docx_path: str | None
    cover_letter_markdown_path: str | None
    cover_letter_docx_path: str | None
    custom_responses: tuple[str, ...]
    email_subject: str | None
    email_body: str | None
    email_recipient: str | None
    manifest: dict[str, Any]


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
    job_description: str = ""
    # Task 21.14A: hard (remote-work) eligibility, reusing
    # RemoteWorkEligibilityClassifier -- a RemoteEligibilityResult, always
    # populated by evaluate_job() (never left unassessed). ELIGIBLE/
    # NOT_APPLICABLE let application preparation proceed; INELIGIBLE/
    # MANUAL_REVIEW gate it in generate_application_documents()/
    # prepare_application() below.
    hard_eligibility: Any = None


_NAME_PLACEHOLDER_PATTERN = re.compile(r"\[?\byour name\b\]?|\{name\}", re.IGNORECASE)


def _resolve_subject_instruction(subject_instruction: str, resolved_name: str) -> str:
    """An employer's stated subject-line instruction (e.g. "EnVision - Your
    Name") is a template, not literal text to send verbatim -- substitute the
    generic "your name" placeholder with the candidate's actual resolved
    name. Left untouched if the instruction contains no such placeholder."""
    return _NAME_PLACEHOLDER_PATTERN.sub(resolved_name, subject_instruction)


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
        self.requirement_service = ApplicationRequirementService()
        self.custom_response_generator = CustomResponseGenerator()
        self.email_composer = ApplicationEmailComposer()
        self.eligibility_classifier = RemoteWorkEligibilityClassifier()

    def evaluate_job(
        self, job_description: str, opportunity: Any = None, hard_eligibility: Any = None,
    ) -> JobEvaluation:
        """Analyze and score a job without generating application documents.

        `opportunity` is the discovery-layer object (work_arrangement/
        remote_status/etc.) when the caller has one; hard eligibility is
        always assessed -- when no opportunity is supplied, a minimal shim
        built from job_analysis is used instead, which the classifier's own
        conservative design resolves to NOT_APPLICABLE unless job_analysis
        itself carries remote-arrangement evidence (Task 21.14A).

        `hard_eligibility` lets a caller that already holds an authoritative
        RemoteEligibilityResult (e.g. the tracker's own already-recorded
        remote_eligibility, reconciled in Task 21.14B) supply it directly
        instead of having it silently recomputed -- and possibly
        contradicted -- from a weaker, job_analysis-derived shim."""
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
        if hard_eligibility is None:
            eligibility_subject = opportunity or opportunity_shim_from_job_analysis(job_analysis, job_description)
            hard_eligibility = self.eligibility_classifier.classify(eligibility_subject)

        return JobEvaluation(
            profile=profile,
            job_analysis=job_analysis,
            employer=employer,
            career_decision=career_decision,
            ats_result=ats_result,
            screening_decision=career_decision.decision,
            recruiter=recruiter,
            job_description=job_description,
            hard_eligibility=hard_eligibility,
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

        # Task 21.14A: hard eligibility gates document generation, always,
        # regardless of `manual` -- screening/manual overrides are about
        # *why* generation was requested, never about *whether the candidate
        # may work there at all*.
        eligibility = evaluation.hard_eligibility
        if eligibility is not None and eligibility.decision == INELIGIBLE:
            raise ValueError(
                "Cannot generate application documents: hard eligibility check failed "
                f"({eligibility.reason or 'no reason recorded'})."
            )
        if eligibility is not None and eligibility.decision == MANUAL_REVIEW:
            raise ValueError(
                "Cannot generate application documents: hard eligibility is uncertain "
                "and requires human review before any document is prepared."
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

    def prepare_application(
        self,
        evaluation: JobEvaluation,
        requirements: ApplicationRequirements | None = None,
        application_method: str | None = None,
        recipient_email: str | None = None,
        candidate_name: str | None = None,
    ) -> ApplicationPackagePlan:
        """Vacancy-driven application preparation: generate exactly the
        materials the employer's own instructions call for -- never more.

        This does not require SCREENING_AUTO_APPLY (unlike
        generate_application_documents): the requirement/plan itself is
        read-only preparation, safe to run for review purposes regardless
        of screening decision. It never touches the tracker or Gmail --
        callers own the human-review/submission boundary.

        LEGACY / NON-PRODUCTION (Task 21.17C audit + 21.17C wiring fix):
        confirmed zero production callers -- only referenced by
        tests/test_application_service_prepare_application.py and
        tests/test_job_eligibility_gate.py, which exercise this method
        directly as a unit, not as part of any live pipeline. Unlike the
        real production paths (CareerAgent, ApplicationPackageOrchestrator /
        ApplicationExecutionOrchestrator via application_eligibility_policy),
        this method applies NO Job Intelligence priority gate of its own --
        by original design, per the docstring above, the caller owns that
        boundary. Do not wire this into an automated production path without
        first adding an equivalent intelligence_priority check at the call
        site; treat it as a manual/diagnostic preparation utility only.
        """
        profile = evaluation.profile
        job_analysis = evaluation.job_analysis
        career_decision = evaluation.career_decision
        ats_result = evaluation.ats_result
        employer_name = job_analysis.get("company") or "the employer"
        role_title = job_analysis.get("job_title") or ""

        if requirements is None:
            requirements = self.requirement_service.analyze(
                evaluation.job_description,
                application_method=application_method,
                recipient_email=recipient_email,
            )

        manifest: dict[str, Any] = {
            "requirements": {
                "resume": requirements.resume_required,
                "cover_letter": requirements.cover_letter_required,
                "supporting_statement": requirements.supporting_statement_required,
                "custom_response": bool(requirements.custom_responses),
                "custom_response_max_words": (
                    requirements.custom_responses[0].max_words if requirements.custom_responses else None
                ),
                "email": requirements.email_required,
                "confidence": requirements.confidence,
            },
            "evidence": list(requirements.evidence),
            "generated_artifacts": {},
        }

        def _empty_plan() -> ApplicationPackagePlan:
            return ApplicationPackagePlan(
                requirements=requirements,
                resume_markdown_path=None,
                resume_docx_path=None,
                cover_letter_markdown_path=None,
                cover_letter_docx_path=None,
                custom_responses=(),
                email_subject=None,
                email_body=None,
                email_recipient=None,
                manifest=manifest,
            )

        # Task 21.14A: hard eligibility is checked before any document is
        # prepared -- ahead of even the requirements-ambiguity check below,
        # since "may this candidate apply at all" is a more fundamental gate
        # than "which documents does this employer want".
        eligibility = evaluation.hard_eligibility
        if eligibility is not None:
            manifest["hard_eligibility"] = {
                "decision": eligibility.decision,
                "reason": eligibility.reason,
                "evidence": eligibility.evidence,
            }
            if eligibility.decision == INELIGIBLE:
                manifest["blocked"] = True
                manifest["blocked_reason"] = "HARD_INELIGIBLE"
                return _empty_plan()
            if eligibility.decision == MANUAL_REVIEW:
                manifest["human_review_required"] = True
                manifest["human_review_reason"] = "HARD_ELIGIBILITY_UNCERTAIN"
                return _empty_plan()

        if requirements.needs_human_review:
            manifest["human_review_required"] = True
            return _empty_plan()

        resume_strategy = self.resume_strategy_engine.optimize(career_decision, ats_result, job_analysis)
        quality_gate: dict[str, Any] = {}

        resume_markdown_path = None
        resume_docx_path = None
        if requirements.resume_required:
            resume_composition = self.resume_composer.compose(
                profile, job_analysis, career_decision, ats_result, resume_strategy,
            )
            resume_markdown_path = self.resume_generator.generate(
                resume_composition, job_analysis, include_ats_summary=False,
            )
            markdown = Path(resume_markdown_path).read_text(encoding="utf-8")
            resume_docx_path = generate_resume_docx(
                markdown,
                company=job_analysis.get("company", "Company"),
                job_title=job_analysis.get("job_title", "Position"),
            )
            manifest["generated_artifacts"]["resume"] = resume_docx_path
            resume_check = check_resume(resume_docx_path)
            quality_gate["resume"] = {"passed": resume_check.passed, "issues": list(resume_check.issues)}

        cover_letter_markdown_path = None
        cover_letter_docx_path = None
        if requirements.cover_letter_required:
            cover_letter_markdown_path, cover_letter_docx_path = self.cover_letter_generator.generate(
                profile, job_analysis, career_decision, resume_strategy,
            )
            manifest["generated_artifacts"]["cover_letter"] = cover_letter_docx_path
            cover_letter_text = Path(cover_letter_markdown_path).read_text(encoding="utf-8")
            cover_letter_check = check_cover_letter(cover_letter_text, employer_name, role_title)
            quality_gate["cover_letter"] = {"passed": cover_letter_check.passed, "issues": list(cover_letter_check.issues)}

        vacancy_keywords = extract_vacancy_keywords(job_analysis, ats_result)

        custom_response_texts: list[str] = []
        if requirements.custom_responses:
            for prompt in requirements.custom_responses:
                text = self.custom_response_generator.generate(
                    profile,
                    max_words=prompt.max_words or 200,
                    employer_name=employer_name,
                    vacancy_keywords=vacancy_keywords,
                )
                custom_response_texts.append(text)
            manifest["generated_artifacts"]["custom_response"] = custom_response_texts[0]
            quality_gate["custom_response"] = {
                "passed": all(
                    check_custom_response(text, prompt.max_words or 200).passed
                    for text, prompt in zip(custom_response_texts, requirements.custom_responses)
                ),
                "issues": [
                    issue
                    for text, prompt in zip(custom_response_texts, requirements.custom_responses)
                    for issue in check_custom_response(text, prompt.max_words or 200).issues
                ],
            }

        email_subject = None
        email_body = None
        email_recipient = None
        if requirements.email_required:
            candidate = (profile.get("candidate") or {})
            resolved_name = candidate_name or candidate.get("full_name") or "Candidate"
            email_recipient = requirements.recipient_email or recipient_email
            email_subject = (
                _resolve_subject_instruction(requirements.subject_instruction, resolved_name)
                if requirements.subject_instruction
                else f"Application: {role_title} - {resolved_name}"
            )
            # No employer-specified custom response to embed: fall back to a
            # short, evidence-grounded fit summary (Task 21.11 Addendum
            # section 13) rather than leaving the email a bare greeting.
            fit_summary = None
            if not custom_response_texts:
                fit_summary = self.custom_response_generator.generate(
                    profile,
                    max_words=70,
                    employer_name=employer_name,
                    vacancy_keywords=vacancy_keywords,
                    max_evidence_entries=1,
                )
            composed = self.email_composer.compose(
                recipient=email_recipient or "",
                subject=email_subject,
                candidate_name=resolved_name,
                role_title=role_title,
                employer_name=employer_name,
                contact_name=requirements.recipient_contact_name,
                custom_response=custom_response_texts[0] if custom_response_texts else fit_summary,
            )
            email_body = composed.body
            manifest["generated_artifacts"]["email"] = {
                "to": email_recipient, "subject": email_subject,
            }
            email_check = check_email(
                body=email_body, recipient=email_recipient, subject=email_subject,
                sender=GMAIL_SENDER_ADDRESS, expected_sender=GMAIL_SENDER_ADDRESS,
            )
            quality_gate["email"] = {"passed": email_check.passed, "issues": list(email_check.issues)}

        manifest["quality_gate"] = quality_gate
        # Transparency over ATS-score maximization (Task 21.11 Addendum
        # section 18): vacancy keywords with no matching verified candidate
        # evidence are surfaced for human awareness, never silently claimed.
        manifest["evidence_gaps"] = list((ats_result.get("keyword_summary") or {}).get("missing") or [])

        return ApplicationPackagePlan(
            requirements=requirements,
            resume_markdown_path=resume_markdown_path,
            resume_docx_path=resume_docx_path,
            cover_letter_markdown_path=cover_letter_markdown_path,
            cover_letter_docx_path=cover_letter_docx_path,
            custom_responses=tuple(custom_response_texts),
            email_subject=email_subject,
            email_body=email_body,
            email_recipient=email_recipient,
            manifest=manifest,
        )
