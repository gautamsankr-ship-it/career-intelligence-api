from typing import Any


class ResumeComposer:
    """
    Resume Composition Engine

    Responsibilities
    ----------------
    • Consume all upstream intelligence
    • Build recruiter-ready resume composition
    • Produce a normalized structure for ResumeGenerator

    This class DOES NOT render Markdown, DOCX or PDF.

    Rendering is handled by ResumeGenerator.
    """

    def compose(
        self,
        profile: dict,
        job_analysis: dict,
        career_result: Any,
        ats_result: dict,
        resume_strategy: dict,
    ) -> dict:

        self.profile = profile
        self.job = job_analysis
        self.career = career_result
        self.ats = ats_result
        self.strategy = resume_strategy

        return {

            "branding": self._branding(),

            "summary": self._summary(),

            "skills": self._skills(),

            "experience": self._experience(),

            "projects": self._projects(),

            "achievements": self._achievements(),

            "education": self._education(),

            "ats": self._ats()

        }

    # ======================================================
    # Branding
    # ======================================================

    def _branding(self) -> dict:

        candidate = self.profile.get("candidate", {})

        return {

            "name": candidate.get("full_name", ""),

            "designation": self.strategy.get(
                "resume_title",
                self.job.get("job_title", "")
            ),

            "email": candidate.get("email", ""),

            "phone": (
                candidate.get("phone")
                or candidate.get("mobile")
                or ""
            ),

            "location": candidate.get("location", ""),

            "country": candidate.get("country", ""),

            "linkedin": candidate.get("linkedin", ""),

            "website": candidate.get("website", ""),

            "github": candidate.get("github", ""),

            "portfolio": candidate.get("portfolio", "")

        }

    # ======================================================
    # Summary
    # ======================================================

    def _summary(self) -> dict:
        """
        Build resume summary structure.
        """

        experience = self.profile.get("experience", {})

        years = experience.get("years", 0)

        summary_focus = self.strategy.get(
            "summary_focus",
            []
        )

        target_role = self.job.get(
            "job_title",
            ""
        )

        return {

            "headline": self.strategy.get(
                "resume_title",
                target_role
            ),

            "executive_summary": (

                f"Chartered Accountant with over "
                f"{years}+ years of experience."

            ),

            "focus_areas": summary_focus

        }

    # ======================================================
    # Skills
    # ======================================================

    def _skills(self) -> dict:
        """
        Build resume skills.
        """

        return {

            "core":

                self.strategy.get(
                    "skills_priority",
                    []
                ),

            "technical":

                self.profile.get(
                    "technical_capabilities",
                    []
                ),

            "software":

                self.profile.get(
                    "software",
                    []
                ),

            "industry":

                self.profile.get(
                    "industry_expertise",
                    {}
                )

        }

    # ======================================================
    # Experience
    # ======================================================

    def _experience(self) -> dict:

        return {

            "summary":

                self.profile.get(
                    "experience",
                    {}
                ),

            "employment_history":

                self.profile.get(
                    "employment_history",
                    []
                ),

            "consulting":

                self.profile.get(
                    "consulting_engagements",
                    []
                ),

            "board_positions":

                self.profile.get(
                    "board_positions",
                    []
                ),

            "entrepreneurship":

                self.profile.get(
                    "entrepreneurship",
                    []
                )

        }

    # ======================================================
    # Projects
    # ======================================================

    def _projects(self) -> dict:

        projects = self.profile.get(
            "projects",
            []
        )

        priorities = set(

            self.strategy.get(
                "projects_priority",
                []
            )

        )

        selected = []

        remaining = []

        for project in projects:

            if project.get("name") in priorities:

                selected.append(project)

            else:

                remaining.append(project)

        return {

            "all": projects,

            "selected": selected + remaining

        }

    # ======================================================
    # Achievements
    # ======================================================

    def _achievements(self) -> list:

        achievements = []

        achievements.extend(

            self.profile.get(
                "career_highlights",
                []
            )

        )

        for employment in self.profile.get(
            "employment_history",
            []
        ):

            achievements.extend(

                employment.get(
                    "achievements",
                    []
                )

            )

        for project in self.profile.get(
            "projects",
            []
        ):

            achievements.extend(

                project.get(
                    "achievements",
                    []
                )

            )

        unique_achievements = []

        seen = set()

        for achievement in achievements:

            if not achievement:
                continue

            key = achievement.lower()

            if key in seen:
                continue

            seen.add(key)

            unique_achievements.append(achievement)

        return unique_achievements

    # ======================================================
    # Education
    # ======================================================

    def _education(self) -> dict:

        return {

            "education":

                self.profile.get(
                    "education",
                    []
                ),

            "certifications":

                self.profile.get(
                    "certifications",
                    []
                ),

            "memberships":

                self.profile.get(
                    "professional_memberships",
                    []
                )

        }

    # ======================================================
    # ATS
    # ======================================================

    def _ats(self) -> dict:

        return {

            "score":

                self.ats.get(
                    "ats_score",
                    {}
                ).get(
                    "overall_score",
                    0
                ),

            "grade":

                self.ats.get(
                    "ats_score",
                    {}
                ).get(
                    "grade",
                    ""
                ),

            "recommendation":

                self.ats.get(
                    "ats_score",
                    {}
                ).get(
                    "recommendation",
                    ""
                ),

            "interview_probability":

                self.ats.get(
                    "ats_score",
                    {}
                ).get(
                    "interview_probability",
                    0
                ),

            "coverage":

                self.ats.get(
                    "keyword_summary",
                    {}
                ).get(
                    "coverage",
                    0
                ),

            "missing":

                self.strategy.get(
                    "keywords_missing",
                    []
                ),

            "strengthen":

                self.strategy.get(
                    "keywords_to_strengthen",
                    []
                )

        }