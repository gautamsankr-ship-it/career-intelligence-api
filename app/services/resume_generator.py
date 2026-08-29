from pathlib import Path


class ResumeGenerator:
    """
    Resume Generator V3

    Responsibilities
    ----------------
    1. Render a resume composition
    2. Save Markdown

    IMPORTANT
    ---------
    This class performs no AI reasoning.

    The ResumeComposer owns composition. All intelligence and the
    normalized composition must be available before calling this class.

    ResumeGenerator only converts a composition into Markdown.
    """

    # ==========================================================
    # Generate Resume
    # ==========================================================

    def generate(
        self,
        composition: dict,
        job_analysis: dict,
        include_ats_summary: bool = False,
    ) -> str:

        """
        Main entry point.

        `include_ats_summary` is opt-in and False by default: ATS score,
        grade, interview probability, keyword coverage and keyword
        diagnostics are internal recruiter/screening information and must
        never appear in employer-facing output. Pass True only for an
        explicitly internal-use rendering.
        """

        self.lines = []

        self.job = job_analysis

        self.composition = composition

        # ------------------------------------------------------
        # Render Resume
        # ------------------------------------------------------

        self._render_header()
        self._render_summary()
        self._render_skills()
        self._render_experience()
        self._render_projects()
        self._render_education()

        if include_ats_summary:
            self._render_ats_summary()

        # ------------------------------------------------------
        # Save Markdown
        # ------------------------------------------------------

        return self._save()

    # ==========================================================
    # Header
    # ==========================================================

    def _render_header(self) -> None:
        """
        Render candidate header.
        """

        branding = self.composition.get(
            "branding",
            {}
        )

        self.lines.append(
            f"# {branding.get('name', '')}"
        )

        designation = branding.get(
            "designation",
            ""
        )

        if designation:
            self.lines.append("")
            self.lines.append(f"**{designation}**")

        contact = []

        if branding.get("email"):
            contact.append(branding["email"])

        if branding.get("phone"):
            contact.append(branding["phone"])

        location = ", ".join(
            filter(
                None,
                [
                    branding.get("location"),
                    branding.get("country")
                ]
            )
        )

        if location:
            contact.append(location)

        if branding.get("linkedin"):
            contact.append(
                branding["linkedin"]
            )

        if branding.get("website"):
            contact.append(
                branding["website"]
            )

        if branding.get("github"):
            contact.append(
                branding["github"]
            )

        if branding.get("portfolio"):
            contact.append(
                branding["portfolio"]
            )

        if contact:

            self.lines.append("")
            self.lines.append(
                " | ".join(contact)
            )

        self.lines.append("")
        self.lines.append("---")
        self.lines.append("")

    # ==========================================================
    # Professional Summary
    # ==========================================================

    def _render_summary(self) -> None:
        """
        Render professional summary.
        """

        summary = self.composition.get(
            "summary",
            {}
        )

        self.lines.append("## Professional Profile")
        self.lines.append("")

        executive_summary = summary.get(
            "executive_summary",
            ""
        )

        if executive_summary:
            self.lines.append(executive_summary)
            self.lines.append("")

    # ==========================================================
    # Skills
    # ==========================================================

    def _render_skills(self) -> None:
        """
        Render professional skills.
        """

        skills = self.composition.get(
            "skills",
            {}
        )

        self.lines.append("## Core Competencies")
        self.lines.append("")

        core = skills.get("core", [])
        if isinstance(core, list) and core:
            self.lines.append(", ".join(core))
            self.lines.append("")
        elif core:
            self.lines.append(str(core))
            self.lines.append("")

        # "Technical Skills" is kept as a defensive fallback for any caller
        # that still supplies a separate technical-skills value; the normal
        # ResumeComposer path folds this into the single "core" list above
        # (Task 21.13) so it stays empty and renders nothing in practice.
        sections = [

            ("Technical Skills", skills.get("technical", [])),

            ("Systems & Technology", skills.get("software", []))

        ]

        for title, values in sections:

            if not values:
                continue

            self.lines.append(f"**{title}**")

            if isinstance(values, list):

                self.lines.append(
                    ", ".join(values)
                )

            elif isinstance(values, dict):

                for category, items in values.items():

                    if isinstance(items, list):

                        self.lines.append(
                            f"- **{category}:** "
                            + ", ".join(items)
                        )

                    else:

                        self.lines.append(
                            f"- **{category}:** {items}"
                        )

            else:

                self.lines.append(str(values))

            self.lines.append("")

        industry = skills.get(
            "industry",
            {}
        )

        if industry:

            self.lines.append(
                "**Industry Expertise**"
            )

            if isinstance(industry, dict):

                for category, values in industry.items():

                    if isinstance(values, list):

                        self.lines.append(
                            f"- **{category}:** "
                            + ", ".join(values)
                        )

                    else:

                        self.lines.append(
                            f"- **{category}:** {values}"
                        )

            elif isinstance(industry, list):

                for value in industry:

                    self.lines.append(f"- {value}")

            else:

                self.lines.append(str(industry))

            self.lines.append("")

    # ==========================================================
    # Professional Experience
    # ==========================================================

    def _render_experience(self) -> None:
        """
        Render professional experience.
        """

        experience = self.composition.get(
            "experience",
            {}
        )

        self.lines.append("## Professional Experience")
        self.lines.append("")

        # ------------------------------------------------------
        # Employment History
        # ------------------------------------------------------

        for job in experience.get(
            "employment_history",
            []
        ):

            title = job.get("title") or job.get("position") or ""
            company = job.get("company", "")
            period = job.get("period", "")
            location = job.get("location", "")

            heading = " | ".join(
                x for x in [title, company] if x
            )

            if heading:
                self.lines.append(f"### {heading}")

            meta = " | ".join(
                x for x in [period, location] if x
            )

            if meta:
                self.lines.append(meta)

            description = job.get(
                "company_description",
                ""
            )

            if description:
                self.lines.append("")
                self.lines.append(description)

            responsibilities = job.get(
                "responsibilities",
                []
            )

            if responsibilities:

                self.lines.append("")
                self.lines.append("**Key Responsibilities**")

                for item in responsibilities:
                    self.lines.append(f"- {item}")

            achievements = job.get(
                "achievements",
                []
            )

            if achievements:

                self.lines.append("")
                self.lines.append("**Key Achievements**")

                for item in achievements:
                    self.lines.append(f"- {item}")

            technologies = job.get(
                "technologies",
                []
            )

            if technologies:

                self.lines.append("")
                self.lines.append(
                    "**Technologies:** "
                    + ", ".join(technologies)
                )

            domains = job.get(
                "domains",
                []
            )

            if domains:

                self.lines.append(
                    "**Domains:** "
                    + ", ".join(domains)
                )

            self.lines.append("")

        # ------------------------------------------------------
        # Consulting Engagements
        # ------------------------------------------------------

        consulting = experience.get(
            "consulting",
            []
        )

        if consulting:

            self.lines.append("## Consulting Engagements")
            self.lines.append("")

            for engagement in consulting:

                client = engagement.get(
                    "client",
                    ""
                )

                role = engagement.get(
                    "role",
                    ""
                )

                period = engagement.get(
                    "period",
                    ""
                )

                heading = " | ".join(
                    x for x in [role, client] if x
                )

                if heading:
                    self.lines.append(f"### {heading}")

                if period:
                    self.lines.append(period)

                description = engagement.get(
                    "description",
                    ""
                )

                if description:
                    self.lines.append("")
                    self.lines.append(description)

                for item in engagement.get(
                    "responsibilities",
                    []
                ):
                    self.lines.append(f"- {item}")

                for item in engagement.get(
                    "achievements",
                    []
                ):
                    self.lines.append(f"- {item}")

                self.lines.append("")

        # ------------------------------------------------------
        # Board Positions
        # ------------------------------------------------------

        board = experience.get(
            "board_positions",
            []
        )

        if board:

            self.lines.append("## Board Positions")
            self.lines.append("")

            for role in board:

                designation = (
                    role.get("designation")
                    or role.get("role")
                    or ""
                )

                organization = role.get(
                    "organization",
                    ""
                )

                period = role.get(
                    "period",
                    ""
                )

                heading = " | ".join(
                    x for x in [designation, organization] if x
                )

                if heading:
                    self.lines.append(f"### {heading}")

                if period:
                    self.lines.append(period)

                for item in role.get(
                    "responsibilities",
                    []
                ):
                    self.lines.append(f"- {item}")

                for item in role.get(
                    "achievements",
                    []
                ):
                    self.lines.append(f"- {item}")

                self.lines.append("")

        # ------------------------------------------------------
        # Entrepreneurship
        # ------------------------------------------------------

        ventures = experience.get(
            "entrepreneurship",
            []
        )

        if ventures:

            self.lines.append("## Entrepreneurship")
            self.lines.append("")

            for venture in ventures:

                company = (
                    venture.get("company")
                    or venture.get("venture")
                    or ""
                )

                role = venture.get(
                    "role",
                    ""
                )

                period = venture.get(
                    "period",
                    ""
                )

                status = venture.get(
                    "status",
                    ""
                )

                heading = " | ".join(
                    x for x in [role, company] if x
                )

                if heading:
                    self.lines.append(f"### {heading}")

                meta = " | ".join(
                    x for x in [period, status] if x
                )

                if meta:
                    self.lines.append(meta)

                description = venture.get(
                    "description",
                    ""
                )

                if description:
                    self.lines.append("")
                    self.lines.append(description)

                for item in venture.get(
                    "achievements",
                    []
                ):
                    self.lines.append(f"- {item}")

                self.lines.append("")
    # ==========================================================
    # Projects
    # ==========================================================

    def _render_projects(self) -> None:
        """
        Render selected projects.
        """

        projects = self.composition.get(
            "projects",
            {}
        ).get(
            "selected",
            []
        )

        if not projects:
            return

        self.lines.append("## Key Projects")
        self.lines.append("")

        for project in projects:

            name = project.get(
                "name",
                ""
            )

            category = project.get(
                "category",
                ""
            )

            status = project.get(
                "status",
                ""
            )

            duration = project.get(
                "duration",
                ""
            )

            self.lines.append(f"### {name}")

            meta = " | ".join(
                x for x in [
                    category,
                    duration,
                    status
                ]
                if x
            )

            if meta:
                self.lines.append(meta)

            description = project.get(
                "description",
                ""
            )

            if description:
                self.lines.append("")
                self.lines.append(description)

            business_domains = project.get(
                "business_domains",
                []
            )

            if business_domains:

                self.lines.append("")
                self.lines.append(
                    "**Business Domains:** "
                    + ", ".join(business_domains)
                )

            responsibilities = project.get(
                "responsibilities",
                []
            )

            if responsibilities:

                self.lines.append("")
                self.lines.append("**Responsibilities**")

                for item in responsibilities:
                    self.lines.append(f"- {item}")

            technologies = project.get(
                "technologies",
                []
            )

            if technologies:

                self.lines.append("")
                self.lines.append(
                    "**Technologies:** "
                    + ", ".join(technologies)
                )

            skills = project.get(
                "skills",
                []
            )

            if skills:

                self.lines.append(
                    "**Skills:** "
                    + ", ".join(skills)
                )

            achievements = project.get(
                "achievements",
                []
            )

            if achievements:

                self.lines.append("")
                self.lines.append("**Achievements**")

                for item in achievements:
                    self.lines.append(f"- {item}")

            roadmap = project.get(
                "future_roadmap",
                ""
            )

            if roadmap:

                self.lines.append("")
                self.lines.append(
                    f"**Future Roadmap:** {roadmap}"
                )

            self.lines.append("")
    # ==========================================================
    # Education
    # ==========================================================

    def _render_education(self) -> None:
        """
        Render education, certifications and memberships.
        """

        education = self.composition.get(
            "education",
            {}
        )

        self.lines.append("## Education")
        self.lines.append("")

        # ------------------------------------------------------
        # Academic Qualifications
        # ------------------------------------------------------

        for qualification in education.get(
            "education",
            []
        ):

            degree = (
                qualification.get("degree")
                or qualification.get("qualification")
                or ""
            )

            institution = qualification.get(
                "institution",
                ""
            )

            year = qualification.get(
                "year",
                ""
            )

            grade = qualification.get(
                "grade",
                ""
            )

            if degree:
                self.lines.append(
                    f"### {degree}"
                )

            meta = " | ".join(
                x for x in [
                    institution,
                    year,
                    grade
                ]
                if x
            )

            if meta:
                self.lines.append(meta)

            description = qualification.get(
                "description",
                ""
            )

            if description:
                self.lines.append("")
                self.lines.append(description)

            achievements = qualification.get(
                "achievements",
                []
            )

            if achievements:

                self.lines.append("")
                self.lines.append("**Achievements**")

                for item in achievements:
                    self.lines.append(f"- {item}")

            self.lines.append("")

        # ------------------------------------------------------
        # Certifications
        # ------------------------------------------------------

        certifications = education.get(
            "certifications",
            []
        )

        if certifications:

            self.lines.append("## Professional Certifications")
            self.lines.append("")

            for certification in certifications:

                if isinstance(certification, str):

                    self.lines.append(
                        f"- {certification}"
                    )

                elif isinstance(certification, dict):

                    name = certification.get(
                        "name",
                        ""
                    )

                    issuer = certification.get(
                        "issuer",
                        ""
                    )

                    year = certification.get(
                        "year",
                        ""
                    )

                    text = " | ".join(
                        x for x in [
                            name,
                            issuer,
                            year
                        ]
                        if x
                    )

                    self.lines.append(
                        f"- {text}"
                    )

            self.lines.append("")

        # ------------------------------------------------------
        # Professional Memberships
        # ------------------------------------------------------

        memberships = education.get(
            "memberships",
            []
        )

        if memberships:

            self.lines.append("## Professional Memberships")
            self.lines.append("")

            for membership in memberships:

                if isinstance(membership, str):

                    self.lines.append(
                        f"- {membership}"
                    )

                elif isinstance(membership, dict):

                    organization = membership.get(
                        "organization",
                        ""
                    )

                    designation = membership.get(
                        "designation",
                        ""
                    )

                    text = " | ".join(
                        x for x in [
                            designation,
                            organization
                        ]
                        if x
                    )

                    self.lines.append(
                        f"- {text}"
                    )

            self.lines.append("")
    # ==========================================================
    # ATS Summary
    # ==========================================================

    def _render_ats_summary(self) -> None:
        """
        Render ATS optimization summary.
        """

        ats = self.composition.get(
            "ats",
            {}
        )

        if not ats:
            return

        self.lines.append("## ATS Optimization Summary")
        self.lines.append("")

        score = ats.get("score")

        if score is not None:
            self.lines.append(
                f"**ATS Score:** {score}"
            )

        grade = ats.get("grade")

        if grade:
            self.lines.append(
                f"**Grade:** {grade}"
            )

        probability = ats.get(
            "interview_probability"
        )

        if probability is not None:
            self.lines.append(
                f"**Interview Probability:** {probability}%"
            )

        coverage = ats.get("coverage")

        if coverage is not None:
            self.lines.append(
                f"**Keyword Coverage:** {coverage}%"
            )

        recommendation = ats.get(
            "recommendation",
            ""
        )

        if recommendation:

            self.lines.append("")
            self.lines.append(
                f"**Recommendation:** {recommendation}"
            )

        missing = ats.get(
            "missing",
            []
        )

        if missing:

            self.lines.append("")
            self.lines.append(
                "**Missing Keywords**"
            )

            for keyword in missing:
                self.lines.append(f"- {keyword}")

        strengthen = ats.get(
            "strengthen",
            []
        )

        if strengthen:

            self.lines.append("")
            self.lines.append(
                "**Keywords to Strengthen**"
            )

            for keyword in strengthen:
                self.lines.append(f"- {keyword}")

        self.lines.append("")

    # ==========================================================
    # Save Resume
    # ==========================================================

    def _save(self) -> str:
        """
        Save the generated resume as a Markdown file.

        Returns
        -------
        str
            Path to generated resume.
        """

        output_dir = Path("output") / "resumes"

        output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        branding = self.composition.get(
            "branding",
            {}
        )

        candidate_name = branding.get(
            "name",
            "Candidate"
        )

        job_title = self.job.get(
            "job_title",
            "Resume"
        )

        company = self.job.get(
            "company",
            ""
        )

        def clean(text: str) -> str:
            """
            Make filename safe.
            """

            if not text:
                return ""

            invalid = '<>:"/\\|?*'

            for ch in invalid:
                text = text.replace(ch, "")

            text = text.replace("&", "and")
            text = text.replace(",", "")
            text = text.strip()

            return "_".join(text.split())

        filename_parts = [

            clean(candidate_name),

            clean(job_title)

        ]

        if company:
            filename_parts.append(
                clean(company)
            )

        filename = "_".join(

            part
            for part in filename_parts
            if part

        )

        if not filename:
            filename = "Resume"

        filepath = output_dir / f"{filename}.md"

        with open(
            filepath,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                "\n".join(self.lines)
            )

        return str(filepath)
