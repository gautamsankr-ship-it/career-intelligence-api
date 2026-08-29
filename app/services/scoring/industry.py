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
        # Classification-Coverage Safeguard (Task 21.15I)
        # ---------------------------------------------

        # `coverage` above only measures fit AMONG families the taxonomy
        # actually recognized. It is not proof of overall domain fit when
        # much of the vacancy's own capability list was never classified
        # into any real family at all (e.g. a niche specialty -- crypto/AML
        # investigations, blockchain forensics -- the taxonomy doesn't yet
        # cover). classification_coverage is the fraction of the vacancy's
        # raw requested capabilities that DID classify into a real family;
        # multiplying it in prevents "every classified family matched" from
        # reading as a false-perfect score when the taxonomy only understood
        # a small generic subset of what the role actually asked for.
        classification_coverage = result.get(
            "classification_coverage",
            1.0
        )

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

            coverage * classification_coverage * weight,

            1

        )

        reason = (

            f"Covered "

            f"{matched_families} of "

            f"{total_families} "

            f"classified industry capability families."

        )

        unclassified = result.get("unclassified_capabilities", [])

        if unclassified:

            reason += (

                f" {len(unclassified)} requested capabilit"

                f"{'y was' if len(unclassified) == 1 else 'ies were'} "

                f"not recognized by the taxonomy."

            )

        return {

            "score": score,

            "confidence": round(confidence, 1),

            "matched": matched,

            "missing": missing,

            "reason": reason

        }