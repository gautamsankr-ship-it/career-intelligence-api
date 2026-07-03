from pathlib import Path

from app.models.resume_optimization import ResumeOptimizationResult


class ResumeGenerator:

    def generate(
        self,
        profile,
        job,
        optimization: ResumeOptimizationResult
    ):

        candidate = profile["candidate"]

        lines = []

        # =====================================================
        # HEADER
        # =====================================================

        lines.append(f"# {candidate['full_name']}")
        lines.append("")

        lines.append(optimization.career_positioning)
        lines.append("")

        lines.append(candidate["location"])
        lines.append("")

        # =====================================================
        # EXECUTIVE SUMMARY
        # =====================================================

        lines.append("## Professional Summary")
        lines.append("")

        lines.append(optimization.executive_summary)
        lines.append("")

        # =====================================================
        # TARGET ROLE
        # =====================================================

        lines.append("## Target Position")
        lines.append("")

        lines.append(job["job_title"])
        lines.append("")

        # =====================================================
        # CORE SKILLS
        # =====================================================

        lines.append("## Core Skills")
        lines.append("")

        for skill in optimization.top_skills:
            lines.append(f"- {skill}")

        lines.append("")

        # =====================================================
        # EXPERIENCE
        # =====================================================

        exp = profile["experience"]

        lines.append("## Professional Experience")
        lines.append("")

        lines.append(f"Years of Experience: {exp['years']}")
        lines.append("")

        lines.append("### Leadership Experience")

        for role in exp["leadership"]:
            lines.append(f"- {role}")

        lines.append("")

        lines.append("### Finance Experience")

        for role in exp["finance_roles"]:
            lines.append(f"- {role}")

        lines.append("")

        # =====================================================
        # KEY RESPONSIBILITIES
        # =====================================================

        lines.append("## Key Responsibilities")
        lines.append("")

        for responsibility in optimization.top_responsibilities:
            lines.append(f"- {responsibility}")

        lines.append("")

        # =====================================================
        # SELECTED PROJECTS
        # =====================================================

        lines.append("## Selected Projects")
        lines.append("")

        for project in optimization.top_projects:

            lines.append(f"### {project['name']}")
            lines.append(project["description"])
            lines.append("")

        # =====================================================
        # ACHIEVEMENTS
        # =====================================================

        lines.append("## Key Achievements")
        lines.append("")

        for achievement in optimization.top_achievements:
            lines.append(f"- {achievement}")

        lines.append("")

        # =====================================================
        # EDUCATION
        # =====================================================

        lines.append("## Education")
        lines.append("")

        for education in profile["education"]:

            lines.append(
                f"- {education['qualification']} | {education['institution']}"
            )

        lines.append("")

        # =====================================================
        # ATS INFORMATION (Internal)
        # =====================================================

        lines.append("---")
        lines.append("")

        lines.append("### Resume Intelligence")
        lines.append("")

        lines.append(f"ATS Before : {optimization.ats_before:.1f}")
        lines.append(f"ATS After  : {optimization.ats_after:.1f}")
        lines.append(
            f"Recruiter Score : {optimization.recruiter_score:.1f}"
        )
        lines.append(
            f"Hiring Manager Score : {optimization.hiring_manager_score:.1f}"
        )

        lines.append("")

        # =====================================================
        # SAVE
        # =====================================================

        output_folder = Path("output/resumes")
        output_folder.mkdir(parents=True, exist_ok=True)

        company = job.get("company", "Company").replace("/", "-")
        title = job.get("job_title", "Job").replace("/", "-")

        filename = output_folder / f"{company}_{title}.md"

        with open(filename, "w", encoding="utf-8") as f:

            f.write("\n".join(lines))

        return str(filename)