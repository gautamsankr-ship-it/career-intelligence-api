from app.services.evidence_engine import EvidenceEngine
from app.services.scoring.score_utils import create_scorecard


class SkillScorer:

    def __init__(self):

        self.evidence = EvidenceEngine()

    def score(self, weight, candidate, job):

        required_skills = job.get("required_skills", [])

        if not required_skills:

            return create_scorecard(

                "Skills",

                weight,

                weight,

                100,

                [],

                [],

                "No required skills.",

            )

        matched = []
        missing = []

        total = 0

        for skill in required_skills:

            result = self.evidence.evidence_score(skill)

            if result["score"] > 0:

                matched.append(skill)

                total += min(result["score"], 10)

            else:

                missing.append(skill)

        normalized = total / (len(required_skills) * 10)

        score = round(weight * normalized, 1)

        confidence = round(normalized * 100, 1)

        return create_scorecard(

            "Skills",

            weight,

            score,

            confidence,

            matched,

            missing,

            f"Evidence found for {len(matched)} skills.",

        )