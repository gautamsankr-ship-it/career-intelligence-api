from app.services.evidence_engine import EvidenceEngine


class KeywordMatcher:
    """
    ATS Keyword Matcher

    Compares ATS keywords against the candidate's
    evidence and profile.

    Returns:

    - matched keywords
    - partial matches
    - missing keywords
    - coverage statistics
    """

    def __init__(self):

        self.evidence = EvidenceEngine()

    # ==========================================================
    # Match Keywords
    # ==========================================================

    def match(self, keywords):

        matched = []

        partial = []

        missing = []

        critical = keywords.get("critical", [])
        important = keywords.get("important", [])
        technologies = keywords.get("technologies", [])
        industry = keywords.get("industry", [])

        all_keywords = (

            critical
            + important
            + technologies
            + industry

        )

        seen = set()

        for keyword in all_keywords:

            keyword = keyword.strip()

            if not keyword:
                continue

            key = keyword.lower()

            if key in seen:
                continue

            seen.add(key)

            evidence = self.evidence.evidence_score(keyword)

            record = {

                "keyword": keyword,

                "matched": evidence["matched"],

                "confidence": evidence["confidence"],

                "weight": evidence["weight"]

            }

            # ---------------------------------------------
            # Strong Match
            # ---------------------------------------------

            if evidence["score"] >= 8:

                record["strength"] = "strong"

                matched.append(record)

            # ---------------------------------------------
            # Partial Match
            # ---------------------------------------------

            elif evidence["score"] >= 5:

                record["strength"] = "partial"

                partial.append(record)

            # ---------------------------------------------
            # Missing
            # ---------------------------------------------

            else:

                missing.append(keyword)

        # ---------------------------------------------
        # Coverage
        # ---------------------------------------------

        total = len(seen)

        matched_count = len(matched)

        partial_count = len(partial)

        coverage = 0

        if total:

            coverage = (

                matched_count
                + (partial_count * 0.5)

            ) / total

        return {

            "matched": matched,

            "partial": partial,

            "missing": sorted(

                list(

                    set(missing)

                )

            ),

            "coverage": round(

                coverage,

                3

            ),

            "statistics": {

                "total": total,

                "matched": matched_count,

                "partial": partial_count,

                "missing": len(missing)

            }

        }