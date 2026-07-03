import json

from app.models.application_context import ApplicationContext

from app.services.profile_service import load_candidate_profile
from app.services.ai_service import analyze_job
from app.services.employer_service import EmployerService
from app.services.career_engine import CareerDecisionEngine

from app.services.resume_optimizer import optimize_resume
from app.services.cover_letter_service import generate_cover_letter

from app.services.docx_service import (
    generate_resume_docx,
    generate_cover_letter_docx,
    save_career_report,
)


# ============================================================
# PHASE 1
# Decision Only
# ============================================================

def build_decision(job_description: str):

    candidate = load_candidate_profile()

    context = ApplicationContext(

        candidate=candidate,

        job_description=job_description

    )

    # -------------------------------------------------------
    # Candidate
    # -------------------------------------------------------

    print("\n" + "=" * 80)
    print("CANDIDATE PROFILE")
    print("=" * 80)

    print("Name :", candidate.get("name"))

    skills = candidate.get("skills", {})

    total_skills = 0

    for values in skills.values():

        total_skills += len(values)

    print("Skills :", total_skills)

    # -------------------------------------------------------
    # Job Analysis
    # -------------------------------------------------------

    print("\n========== RAW JOB DESCRIPTION ==========")
    print(context.job_description[:1000])
    print("Length:", len(context.job_description))

    context.job_analysis = analyze_job(

        context.job_description

    )

    print("\n" + "=" * 80)
    print("JOB ANALYSIS")
    print("=" * 80)

    print(json.dumps(

        context.job_analysis,

        indent=2

    ))

    # -------------------------------------------------------
    # Employer
    # -------------------------------------------------------

    context.employer = EmployerService().analyze(

        context.job_analysis

    )

    print("\n" + "=" * 80)
    print("EMPLOYER")
    print("=" * 80)

    print(context.employer)

    # -------------------------------------------------------
    # Career Decision
    # -------------------------------------------------------

    engine = CareerDecisionEngine()

    context.decision = engine.evaluate(

        context.candidate,

        context.job_analysis,

        context.employer

    )

    print("\n" + "=" * 80)
    print("CAREER DECISION")
    print("=" * 80)

    print(

        "Overall Score :",

        context.decision.overall_score

    )

    print(

        "Decision      :",

        context.decision.decision

    )

    print(

        "Priority      :",

        context.decision.priority

    )

    print()

    for card in context.decision.scorecards:

        print(card)

    return context


# ============================================================
# PHASE 2
# Generate Application Package
# ============================================================

def generate_package(

    context: ApplicationContext

):

    context.resume = optimize_resume(

        context.candidate,

        context.job_analysis,

        context.decision

    )

    context.cover_letter = generate_cover_letter(

        context.candidate,

        context.job_analysis,

        context.decision

    )

    context.resume_file = generate_resume_docx(

        context.resume,

        context.job_analysis["company"],

        context.job_analysis["job_title"]

    )

    context.cover_letter_file = generate_cover_letter_docx(

        context.cover_letter,

        context.job_analysis["company"],

        context.job_analysis["job_title"]

    )

    context.report_file = save_career_report(

        context.decision,

        context.employer,

        context.job_analysis["company"],

        context.job_analysis["job_title"]

    )

    return context


# ============================================================
# Backward Compatibility
# ============================================================

def build_application(

    job_description: str

):

    context = build_decision(

        job_description

    )

    return generate_package(

        context

    )