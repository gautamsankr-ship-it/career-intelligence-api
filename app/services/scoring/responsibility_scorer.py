from app.services.scoring.score_utils import create_scorecard
from app.services.responsibility.responsibility_matcher import ResponsibilityMatcher


class ResponsibilityScorer:

    def __init__(self):

        self.matcher = ResponsibilityMatcher()

    # ==========================================================
    # Responsibility Score
    # ==========================================================

    def score(self, weight, candidate, job):

        responsibilities = job.get("responsibilities", [])

        if not responsibilities:

            return create_scorecard(

                category="Responsibilities",

                weight=weight,

                score=weight,

                confidence=100,

                matched=[],

                missing=[],

                reason="No responsibilities extracted."

            )

        result = self.matcher.match_all(

            responsibilities

        )

        matched = []

        missing = []

        # ------------------------------------------------------
        # Build consolidated evidence
        # ------------------------------------------------------

        for item in result["results"]:

            for match in item["matched"]:

                matched.append(

                    match["matched"]

                )

            missing.extend(

                item["missing"]

            )

        matched = sorted(

            list(set(matched))

        )

        missing = sorted(

            list(set(missing))

        )

        score = round(

            result["score"] * weight,

            1

        )

        return create_scorecard(

            category="Responsibilities",

            weight=weight,

            score=score,

            confidence=result["confidence"],

            matched=matched,

            missing=missing,

            reason=(

                f"Matched "

                f"{len(matched)} "

                f"professional capabilities "

                f"across "

                f"{len(responsibilities)} "

                f"responsibilities."

            )

        )