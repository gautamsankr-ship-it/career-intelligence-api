from app.services.evidence_engine import EvidenceEngine
from app.services.scoring.score_utils import create_scorecard


class ResponsibilityScorer:

    def __init__(self):

        self.evidence = EvidenceEngine()

    def score(self, weight, candidate, job):

        summary = job.get("summary", "")

        keywords = job.get("keywords", [])

        search_terms = []

        if summary:
            search_terms.extend(summary.split())

        search_terms.extend(keywords)

        matched = []
        missing = []

        total = 0

        seen = set()

        for term in search_terms:

            term = term.strip()

            if len(term) < 4:
                continue

            if term.lower() in seen:
                continue

            seen.add(term.lower())

            result = self.evidence.evidence_score(term)

            if result["score"] > 0:

                matched.append(term)

                total += min(result["score"], 10)

            else:

                missing.append(term)

        if not search_terms:

            score = weight

        else:

            normalized = total / (len(seen) * 10)

            score = round(weight * normalized, 1)

        return create_scorecard(

            category="Responsibilities",

            weight=weight,

            score=score,

            confidence=95,

            matched=matched,

            missing=missing,

            reason=f"Evidence found for {len(matched)} responsibility keywords."

        )