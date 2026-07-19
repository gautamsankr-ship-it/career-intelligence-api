class ResumeOptimizer:
    """
    Resume Strategy Generator

    Uses Career Intelligence and ATS Intelligence
    to produce a resume optimization strategy.

    It NEVER generates resume text.
    """

    def optimize(

        self,

        career_result,

        ats_result,

        job_analysis

    ):

        keyword_summary = ats_result["keyword_summary"]

        matched = [

            item["keyword"]

            for item in keyword_summary["matched"]

        ]

        partial = [

            item["keyword"]

            for item in keyword_summary["partial"]

        ]

        missing = keyword_summary["missing"]

        # ------------------------------------------------------
        # Summary Focus
        # ------------------------------------------------------

        summary_focus = []

        summary_focus.extend(

            job_analysis.get(

                "finance_domains",

                []

            )

        )

        summary_focus.extend(

            job_analysis.get(

                "required_skills",

                []

            )[:5]

        )

        # ------------------------------------------------------
        # Experience Priority
        # ------------------------------------------------------

        experience_priority = []

        if any(

            "finance" in x.lower()

            for x in summary_focus

        ):

            experience_priority.append(

                "Corporate Finance Experience"

            )

        if any(

            "audit" in x.lower()

            for x in summary_focus

        ):

            experience_priority.append(

                "Audit Experience"

            )

        if any(

            "project" in x.lower()

            for x in summary_focus

        ):

            experience_priority.append(

                "Project Experience"

            )

        # ------------------------------------------------------
        # Projects
        # ------------------------------------------------------

        projects = []

        if "Financial Due Diligence" in matched:

            projects.append(

                "Hydropower Financial Due Diligence"

            )

        if "Automation" in matched:

            projects.append(

                "AI Career Intelligence Platform"

            )

        if "ERP" in matched:

            projects.append(

                "Fleet ERP Project"

            )

        # ------------------------------------------------------
        # Resume Title
        # ------------------------------------------------------

        title = job_analysis.get(

            "job_title",

            "Professional Resume"

        )

        # ------------------------------------------------------
        # Strategy
        # ------------------------------------------------------

        return {

            "resume_title": title,

            "summary_focus":

                sorted(

                    list(

                        set(summary_focus)

                    )

                ),

            "experience_priority":

                experience_priority,

            "skills_priority":

                matched,

            "projects_priority":

                projects,

            "keywords_to_repeat":

                matched,

            "keywords_to_strengthen":

                partial,

            "keywords_missing":

                missing,

            "recommended_length":

                "2 pages"

        }