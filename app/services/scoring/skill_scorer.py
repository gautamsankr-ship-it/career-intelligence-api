from app.services.skills.skill_matcher import SkillMatcher
from app.services.scoring.score_utils import create_scorecard


class SkillScorer:

    def __init__(self):

        self.matcher = SkillMatcher()

    def score(self, weight, candidate, job):

        required = []

        required.extend(job.get("required_skills", []))
        required.extend(job.get("preferred_skills", []))
        required.extend(job.get("technologies", []))

        required = sorted(

            {

                x.strip().lower()

                for x in required

                if x and x.strip()

            }

        )

        if not required:

            return create_scorecard(

                category="Skills",

                weight=weight,

                score=weight,

                confidence=100,

                matched=[],

                missing=[],

                reason="No required skills extracted."

            )

        result = self.matcher.match(required)

        score = round(

            result["coverage"] * weight,

            1

        )

        confidence = round(

            80 + result["coverage"] * 20,

            1

        )

        return create_scorecard(

            category="Skills",

            weight=weight,

            score=score,

            confidence=confidence,

            matched=[

                item["skill"]

                for item in result["matched"]

            ],

            missing=result["missing"],

            reason=(

                f"Matched "

                f"{len(result['matched'])} "

                f"weighted skills."

            )

        )