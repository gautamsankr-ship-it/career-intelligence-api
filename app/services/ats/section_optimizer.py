class SectionOptimizer:
    """
    Resume Section Optimizer

    Determines which resume sections should receive
    the greatest emphasis based on ATS analysis.

    This module NEVER rewrites content.
    It only recommends priorities.
    """

    def __init__(self):

        self.sections = [

            "Professional Summary",

            "Core Skills",

            "Professional Experience",

            "Projects",

            "Leadership",

            "Education",

            "Certifications"

        ]

    # ==========================================================
    # Score Resume Sections
    # ==========================================================

    def optimize(

        self,

        ats_result,

        keyword_recommendations

    ):

        scores = {

            section: 0

            for section in self.sections

        }

        # ------------------------------------------------------
        # ATS Category Weighting
        # ------------------------------------------------------

        breakdown = ats_result.get(

            "breakdown",

            {}

        )

        if breakdown.get("critical", {}).get("coverage", 0) < 0.80:

            scores["Professional Summary"] += 4
            scores["Professional Experience"] += 5

        if breakdown.get("technologies", {}).get("coverage", 0) < 0.80:

            scores["Core Skills"] += 5
            scores["Projects"] += 3

        if breakdown.get("industry", {}).get("coverage", 0) < 0.80:

            scores["Professional Experience"] += 4
            scores["Projects"] += 3

        if breakdown.get("responsibilities", {}).get("coverage", 0) < 0.80:

            scores["Professional Experience"] += 5

        if breakdown.get("soft_skills", {}).get("coverage", 0) < 0.80:

            scores["Leadership"] += 3
            scores["Professional Summary"] += 2

        # ------------------------------------------------------
        # Keyword Recommendations
        # ------------------------------------------------------

        priority_weight = {

            "High": 3,

            "Medium": 2,

            "Low": 1

        }

        for recommendation in keyword_recommendations:

            section = recommendation["section"]

            priority = recommendation["priority"]

            if section in scores:

                scores[section] += priority_weight.get(

                    priority,

                    1

                )

        # ------------------------------------------------------
        # Rank Sections
        # ------------------------------------------------------

        ranked = sorted(

            scores.items(),

            key=lambda x: x[1],

            reverse=True

        )

        # ------------------------------------------------------
        # Output
        # ------------------------------------------------------

        recommendations = []

        for rank, (section, score) in enumerate(

            ranked,

            start=1

        ):

            if score >= 8:

                emphasis = "★★★★★"

            elif score >= 6:

                emphasis = "★★★★☆"

            elif score >= 4:

                emphasis = "★★★☆☆"

            elif score >= 2:

                emphasis = "★★☆☆☆"

            else:

                emphasis = "★☆☆☆☆"

            # ------------------------------------------------------
            # Explanation
            # ------------------------------------------------------

            reason = {

                "Professional Summary":
                    "Highlight your strongest finance and leadership capabilities.",

                "Core Skills":
                    "Increase ATS keyword density using verified technical and finance skills.",

                "Professional Experience":
                    "Emphasize achievements and responsibilities that directly align with the job.",

                "Projects":
                    "Showcase relevant projects that demonstrate practical experience.",

                "Leadership":
                    "Highlight leadership, stakeholder management and team achievements.",

                "Education":
                    "Include qualifications only if relevant to the position.",

                "Certifications":
                    "Prominently display relevant certifications where applicable."

            }.get(

                section,

                ""

            )

            recommendations.append(

                {

                    "rank": rank,

                    "section": section,

                    "score": score,

                    "emphasis": emphasis,

                    "reason": reason

                }

            )

        return recommendations