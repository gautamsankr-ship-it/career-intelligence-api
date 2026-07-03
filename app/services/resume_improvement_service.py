from app.models.resume_improvement import (
    ResumeImprovement,
    ImprovementItem
)


class ResumeImprovementService:

    """
    Measures how much value
    AI optimization added.
    """

    def evaluate(

        self,

        raw_score: float,

        optimized_score: float

    ):

        improvement = round(

            optimized_score - raw_score,

            1

        )

        confidence = min(

            99,

            round(

                75 + improvement * 2,

                1

            )

        )

        improvements = []

        if improvement > 0:

            improvements.append(

                ImprovementItem(

                    category="ATS Keywords",

                    points=round(improvement * 0.28,1),

                    explanation="Improved keyword alignment."

                )

            )

            improvements.append(

                ImprovementItem(

                    category="Professional Summary",

                    points=round(improvement * 0.18,1),

                    explanation="Tailored summary for employer."

                )

            )

            improvements.append(

                ImprovementItem(

                    category="Relevant Experience",

                    points=round(improvement * 0.24,1),

                    explanation="Highlighted matching experience."

                )

            )

            improvements.append(

                ImprovementItem(

                    category="Achievements",

                    points=round(improvement * 0.18,1),

                    explanation="Prioritized measurable achievements."

                )

            )

            improvements.append(

                ImprovementItem(

                    category="Cover Letter",

                    points=round(improvement * 0.12,1),

                    explanation="Aligned with employer."

                )

            )

        return ResumeImprovement(

            raw_score=raw_score,

            optimized_score=optimized_score,

            improvement=improvement,

            confidence=confidence,

            improvements=improvements,

            summary=(

                f"AI improved the application "

                f"by {improvement} points."

            )

        )