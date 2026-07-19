from app.services.evidence_engine import EvidenceEngine


class KeywordOptimizer:
    """
    ATS Resume Optimizer

    Uses the candidate's evidence to recommend
    where existing experience should be emphasized.

    Never fabricates experience.
    """

    def __init__(self):

        self.evidence = EvidenceEngine()

    # ==========================================================
    # Recommend Resume Section
    # ==========================================================

    def _section(self, keyword):

        keyword = keyword.lower()

        leadership = [

            "leadership",
            "management",
            "stakeholder",
            "director",
            "board",
            "executive"

        ]

        technology = [

            "python",
            "sql",
            "power bi",
            "excel",
            "sap",
            "oracle",
            "xero",
            "odoo",
            "automation",
            "ai"

        ]

        finance = [

            "valuation",
            "financial modelling",
            "financial modeling",
            "budgeting",
            "forecasting",
            "due diligence",
            "audit",
            "tax",
            "ifrs",
            "cash flow",
            "project finance"

        ]

        if any(x in keyword for x in leadership):

            return "Professional Experience"

        if any(x in keyword for x in technology):

            return "Technical Skills"

        if any(x in keyword for x in finance):

            return "Professional Experience"

        return "Professional Summary"

    # ==========================================================
    # Optimize
    # ==========================================================

    def optimize(self, match_result):

        recommendations = []

        # ------------------------------------------------------
        # Partial Matches
        # ------------------------------------------------------

        for item in match_result.get(

            "partial",

            []

        ):

            recommendations.append(

                {

                    "keyword": item["keyword"],

                    "priority": "Medium",

                    "section": self._section(

                        item["keyword"]

                    ),

                    "action": (

                        "Strengthen existing evidence "

                        "for this capability."

                    )

                }

            )

        # ------------------------------------------------------
        # Missing Keywords
        # ------------------------------------------------------

        for keyword in match_result.get(

            "missing",

            []

        ):

            evidence = self.evidence.evidence_score(

                keyword

            )

            if evidence["score"] >= 4:

                action = (

                    "Mention related experience "

                    "already in your profile."

                )

                priority = "Medium"

            else:

                action = (

                    "Do not fabricate experience. "

                    "Only include if genuinely supported."

                )

                priority = "High"

            recommendations.append(

                {

                    "keyword": keyword,

                    "priority": priority,

                    "section": self._section(

                        keyword

                    ),

                    "action": action

                }

            )

        # ------------------------------------------------------
        # Strong Matches
        # ------------------------------------------------------

        for item in match_result.get(

            "matched",

            []

        ):

            if item["confidence"] >= 95:

                recommendations.append(

                    {

                        "keyword": item["keyword"],

                        "priority": "Low",

                        "section": self._section(

                            item["keyword"]

                        ),

                        "action": (

                            "Highlight this capability "

                            "prominently."

                        )

                    }

                )

        return sorted(

            recommendations,

            key=lambda x: {

                "High": 0,

                "Medium": 1,

                "Low": 2

            }[x["priority"]]

        )