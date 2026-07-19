"""Education scoring helpers."""
from app.models.decision_model import ScoreCard


class EducationScorer:

    def score(self, weight, candidate, job):

        education = candidate.get("education", [])

        qualifications = []

        for item in education:

            qualifications.append(
                item.get("qualification", "").lower()
            )

        job_text = " ".join(

            [

                job.get("summary", ""),

                " ".join(job.get("keywords", [])),

                " ".join(job.get("education", []))

            ]

        ).lower()

        matched = []

        score = 0

        # --------------------------------------------------
        # Chartered Accountant
        # --------------------------------------------------

        ca_keywords = [

            "chartered accountant",

            "ca",

            "cpa",

            "acca",

            "professional accounting"

        ]

        if any(

            x in job_text

            for x in ca_keywords

        ):

            if any(

                "chartered accountant" in q

                for q in qualifications

            ):

                matched.append(

                    "Chartered Accountant"

                )

                score += 6

        # --------------------------------------------------
        # Bachelor
        # --------------------------------------------------

        bachelor_keywords = [

            "bachelor",

            "business",

            "commerce",

            "finance",

            "accounting"

        ]

        if any(

            x in job_text

            for x in bachelor_keywords

        ):

            if any(

                "bachelor"

                in q

                for q in qualifications

            ):

                matched.append("Bachelor")

                score += 4

        score = min(

            score,

            weight

        )

        return ScoreCard(

            category="Education",

            weight=weight,

            score=score,

            confidence=95,

            matched=matched,

            missing=[],

            reason=f"Matched {len(matched)} education requirements."

        )