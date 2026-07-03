from app.services.scoring.score_utils import create_scorecard


class ExperienceScorer:

    def score(self, weight, candidate, job):

        experience = candidate.get("experience", {})

        # Support both old and new profile formats
        candidate_years = experience.get(
            "years",
            experience.get("total_years", 0)
        )

        text = (
            job.get("summary", "")
            + " "
            + " ".join(job.get("keywords", []))
        ).lower()

        required = 0

        for n in range(1, 31):

            if f"{n} year" in text or f"{n}+ year" in text:

                required = n

        if required == 0:

            score = weight

        elif candidate_years >= required:

            score = weight

        else:

            score = round(
                candidate_years / required * weight,
                1
            )

        return create_scorecard(
            category="Experience",
            weight=weight,
            score=score,
            confidence=95,
            matched=[f"{candidate_years} years"],
            missing=[],
            reason=f"{candidate_years} years experience."
        )