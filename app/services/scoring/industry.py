from app.services.industry.industry_matcher import IndustryMatcher


class IndustryScorer:

    def __init__(self):

        self.matcher = IndustryMatcher()

    # ==========================================================
    # Industry Intelligence V4
    # ==========================================================

    def score(self, weight, candidate, job):

        capabilities = []

        capabilities.extend(
            job.get("finance_domains", [])
        )

        capabilities.extend(
            job.get("required_skills", [])
        )

        capabilities.extend(
            job.get("preferred_skills", [])
        )

        capabilities.extend(
            job.get("technologies", [])
        )

        capabilities = sorted(

            {

                x.strip()

                for x in capabilities

                if x and x.strip()

            }

        )

        if not capabilities:

            return {

                "score": weight,

                "confidence": 100,

                "matched": [],

                "missing": [],

                "reason": "No industry capabilities extracted."

            }

        result = self.matcher.match_all(

            capabilities

        )

        # ---------------------------------------------
        # Family Coverage
        # ---------------------------------------------

        families = result["families"]

        matched = result["matched"]

        missing = result["missing"]

        matched_families = len(

            result["families"]

        )

        total_families = max(

            len(

                result["requested_families"]

            ),

            1

        )

        coverage = matched_families / total_families

        # ---------------------------------------------
        # Confidence Boost
        # ---------------------------------------------

        confidence = max(

            result["confidence"],

            coverage * 100

        )

        # ---------------------------------------------
        # Final Score
        # ---------------------------------------------

        score = round(

            coverage * weight,

            1

        )

        return {

            "score": score,

            "confidence": round(confidence, 1),

            "matched": matched,

            "missing": missing,

            "reason": (

                f"Covered "

                f"{matched_families} of "

                f"{total_families} "

                f"industry capability families."

            )

        }