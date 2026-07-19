from collections import OrderedDict


class KeywordExtractor:
    """
    ATS Keyword Extractor

    Converts AI job analysis into prioritized ATS keywords.

    Output Categories

    - critical
    - important
    - optional
    - technologies
    - responsibilities
    - industry
    - soft_skills
    """

    def __init__(self):
        pass

    # ==========================================================
    # Remove duplicates while preserving order
    # ==========================================================

    def _unique(self, items):

        return list(

            OrderedDict.fromkeys(

                [

                    x.strip()

                    for x in items

                    if x and x.strip()

                ]

            )

        )

    # ==========================================================
    # Extract ATS Keywords
    # ==========================================================

    def extract(self, job):

        critical = []

        important = []

        optional = []

        technologies = []

        responsibilities = []

        industry = []

        soft_skills = []

        # ------------------------------------------------------
        # Critical
        # ------------------------------------------------------

        critical.extend(

            job.get("required_skills", [])

        )

        critical.extend(

            job.get("finance_domains", [])

        )

        # ------------------------------------------------------
        # Important
        # ------------------------------------------------------

        important.extend(

            job.get("preferred_skills", [])

        )

        # ------------------------------------------------------
        # Technologies
        # ------------------------------------------------------

        technologies.extend(

            job.get("technologies", [])

        )

        # ------------------------------------------------------
        # Responsibilities
        # ------------------------------------------------------

        responsibilities.extend(

            job.get("responsibilities", [])

        )

        # ------------------------------------------------------
        # Industry
        # ------------------------------------------------------

        if job.get("industry"):

            industry.append(

                job["industry"]

            )

        if job.get("department"):

            industry.append(

                job["department"]

            )

        # ------------------------------------------------------
        # Soft Skills
        # ------------------------------------------------------

        soft_skills.extend(

            job.get("soft_skills", [])

        )

        # ------------------------------------------------------
        # Optional
        # ------------------------------------------------------

        optional.extend(

            job.get("education", [])

        )

        if job.get("employment_type"):

            optional.append(

                job["employment_type"]

            )

        if job.get("seniority"):

            optional.append(

                job["seniority"]

            )

        # ------------------------------------------------------
        # Return
        # ------------------------------------------------------

        return {

            "critical": self._unique(critical),

            "important": self._unique(important),

            "optional": self._unique(optional),

            "technologies": self._unique(technologies),

            "responsibilities": self._unique(responsibilities),

            "industry": self._unique(industry),

            "soft_skills": self._unique(soft_skills),

            "all_keywords": self._unique(

                critical
                + important
                + technologies
                + responsibilities
                + industry
                + soft_skills
                + optional

            )

        }