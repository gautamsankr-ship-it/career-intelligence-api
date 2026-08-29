from typing import Any

from app.services.candidate_evidence_service import get_enriched_profile
from app.services.resume_relevance import (
    DEFAULT_BOARD_FIELDS,
    DEFAULT_EMPLOYMENT_FIELDS,
    DEFAULT_PROJECT_FIELDS,
    DEFAULT_VENTURE_FIELDS,
    build_professional_summary_sentence,
    extract_vacancy_keywords,
    flatten_skill_groups,
    humanize_responsibilities,
    rank_flat_items,
    select_top,
)

# How many entries of each secondary/tertiary section survive vacancy
# ranking, to keep the employer-facing resume within a 2-3 page target
# rather than dumping the entire master profile. Primary employment history
# gets a more generous cap since it's the core of the resume.
MAX_EMPLOYMENT_HISTORY = 5
MAX_CONSULTING_ENGAGEMENTS = 2
MAX_BOARD_POSITIONS = 2
MAX_ENTREPRENEURSHIP = 2
MAX_PROJECTS = 2
MAX_SOFTWARE_ITEMS = 10
MAX_TECHNICAL_ITEMS = 6
# Task 21.13: a single, consolidated "Core Competencies" list replaces the
# previous overlapping Core Focus Areas / Core Skills / Technical Skills /
# Industry Expertise sections.
MAX_CORE_COMPETENCIES = 14


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

        # Drawing on the full career evidence library (Task 21.11 Addendum)
        # rather than the simplified profile alone -- only VERIFIED facts are
        # ever merged in, so unconfirmed/conflicting claims from richer
        # source documents never reach the resume.
        self.profile = get_enriched_profile(profile)
        self.job = job_analysis
        self.career = career_result
        self.ats = ats_result
        self.strategy = resume_strategy

        self._vacancy_keywords = extract_vacancy_keywords(self.job, self.ats)

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

        target_role = self.job.get(
            "job_title",
            ""
        )

        keywords = getattr(self, "_vacancy_keywords", set())

        return {

            "headline": self.strategy.get(
                "resume_title",
                target_role
            ),

            # Generic, evidence-grounded identity sentence (no hardcoded
            # professional title) -- prioritizes the domains most relevant
            # to this vacancy rather than dumping the full stored list
            # (Task 21.13 section 2). A per-application tailored override
            # still wins when the strategy engine supplies one.
            "executive_summary": self.strategy.get(
                "executive_summary"
            ) or build_professional_summary_sentence(self.profile, keywords) or "",

        }

    # ======================================================
    # Skills
    # ======================================================

    def _skills(self) -> dict:
        """
        Build resume skills.

        Task 21.13 section 4: the previous Core Focus Areas / Core Skills /
        Technical Skills / Industry Expertise sections overlapped heavily
        and read as a keyword dump. They're consolidated into ONE
        "Core Competencies" list here (deduplicated, vacancy-ranked, capped)
        so the renderer produces a single section instead of four. Software/
        systems stays a separate list (still genuinely distinct: tool names,
        not competency phrases), rendered under its own "Systems &
        Technology" heading.
        """

        keywords = getattr(self, "_vacancy_keywords", set())

        technical_flat = flatten_skill_groups(self.profile.get("technical_capabilities", []))
        software_flat = flatten_skill_groups(self.profile.get("software", []))
        industry_primary = (self.profile.get("industry_expertise") or {}).get("primary", [])

        combined_competencies: list[str] = []
        seen: set[str] = set()
        for source in (
            self.strategy.get("skills_priority", []),
            self.strategy.get("summary_focus", []),
            technical_flat,
            industry_primary,
        ):
            for item in source or []:
                if not isinstance(item, str):
                    continue
                key = item.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    combined_competencies.append(item)

        ranked_competencies = rank_flat_items(combined_competencies, keywords)[:MAX_CORE_COMPETENCIES]

        return {

            "core": ranked_competencies,

            "software":

                rank_flat_items(software_flat, keywords)[:MAX_SOFTWARE_ITEMS],

        }

    # ======================================================
    # Experience
    # ======================================================

    def _experience(self) -> dict:

        keywords = getattr(self, "_vacancy_keywords", set())

        employment_history = select_top(
            self.profile.get("employment_history", []),
            keywords, DEFAULT_EMPLOYMENT_FIELDS, MAX_EMPLOYMENT_HISTORY,
        )
        employment_history = [
            {
                **job,
                "responsibilities": humanize_responsibilities(
                    job.get("responsibilities", []),
                    job.get("company", ""),
                    job.get("position") or job.get("title") or "",
                ),
            }
            for job in employment_history
        ]

        return {

            "summary":

                self.profile.get(
                    "experience",
                    {}
                ),

            "employment_history": employment_history,

            "consulting": select_top(
                self.profile.get("consulting_engagements", []),
                keywords, ("role", "client", "description", "finance_domains", "skills"),
                MAX_CONSULTING_ENGAGEMENTS,
            ),

            "board_positions": select_top(
                self.profile.get("board_positions", []),
                keywords, DEFAULT_BOARD_FIELDS, MAX_BOARD_POSITIONS,
            ),

            "entrepreneurship": select_top(
                self.profile.get("entrepreneurship", []),
                keywords, DEFAULT_VENTURE_FIELDS, MAX_ENTREPRENEURSHIP,
            ),

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

        ordered = selected + remaining

        # Projects are secondary/tertiary content: rank by vacancy relevance
        # and compress hard rather than dumping every project onto the resume.
        keywords = getattr(self, "_vacancy_keywords", set())
        capped = select_top(ordered, keywords, DEFAULT_PROJECT_FIELDS, MAX_PROJECTS)

        return {

            "all": projects,

            "selected": capped

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