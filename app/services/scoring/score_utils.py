from app.models.decision_model import ScoreCard


def create_scorecard(
    category,
    weight,
    score,
    confidence,
    matched,
    missing,
    reason,
):

    return ScoreCard(

        category=category,

        weight=weight,

        score=score,

        confidence=confidence,

        matched=matched,

        missing=missing,

        reason=reason,

    )