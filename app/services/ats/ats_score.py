class ATSScore:
    """
    ATS Compatibility Scoring Engine V2

    Calculates:

    • ATS Score
    • Grade
    • Interview Probability
    • Recruiter Recommendation
    • Category Breakdown
    """

    def __init__(self):

        self.weights = {
            "critical": 30,
            "important": 20,
            "technologies": 15,
            "industry": 10,
            "responsibilities": 10,
            "soft_skills": 5,
            "education": 5,
            "leadership": 5,
        }

    # ==========================================================
    # Score One Category
    # ==========================================================

    def _category_score(self, items, matcher, weight):

        if not items:
            return {
                "score": weight,
                "coverage": 1.0,
                "matched": [],
                "missing": [],
                "confidence": 100,
            }

        matched = []

        missing = []

        confidence = 0

        weighted_matches = 0

        for item in items:
            result = matcher.evidence.evidence_score(item)

            confidence += result["confidence"]

            if result["score"] >= 8:
                weighted_matches += 1

                matched.append(item)

            elif result["score"] >= 5:
                weighted_matches += result["score"] / 10

                matched.append(item)

            else:
                missing.append(item)

        coverage = weighted_matches / len(items)

        score = round(coverage * weight, 2)

        return {
            "score": score,
            "coverage": round(coverage, 3),
            "confidence": round(confidence / len(items), 1),
            "matched": matched,
            "missing": missing,
        }

    # ==========================================================
    # ATS Grade
    # ==========================================================

    def _grade(self, score):

        if score >= 95:
            return "A+"

        if score >= 90:
            return "A"

        if score >= 80:
            return "B"

        if score >= 70:
            return "C"

        if score >= 60:
            return "D"

        return "F"

    # ==========================================================
    # Recruiter Recommendation
    # ==========================================================

    def _recommendation(self, score):

        if score >= 90:
            return "Highly Recommended"

        if score >= 80:
            return "Recommended"

        if score >= 70:
            return "Worth Interview"

        if score >= 60:
            return "Needs Improvement"

        return "Not Competitive"

    # ==========================================================
    # Interview Probability
    # ==========================================================

    def _interview_probability(self, score):

        if score >= 95:
            return 98

        if score >= 90:
            return 92

        if score >= 80:
            return 82

        if score >= 70:
            return 68

        if score >= 60:
            return 50

        return 25

    # ==========================================================
    # ATS Calculation
    # ==========================================================

    def calculate(self, keywords, matcher):

        breakdown = {}

        overall_score = 0

        for category, weight in self.weights.items():
            result = self._category_score(keywords.get(category, []), matcher, weight)

            breakdown[category] = result

            overall_score += result["score"]

        overall_score = round(overall_score, 1)

        grade = self._grade(overall_score)

        recommendation = self._recommendation(overall_score)

        interview_probability = self._interview_probability(overall_score)

        recommendations = self._recommendations(breakdown)

        if breakdown:
            average_confidence = round(
                sum(r["confidence"] for r in breakdown.values()) / len(breakdown),
                1,
            )
        else:
            average_confidence = 100

        overall_coverage = round(
            sum(r["coverage"] for r in breakdown.values()) / len(breakdown),
            3,
        )

        return {
            "overall_score": overall_score,
            "grade": grade,
            "recommendation": recommendation,
            "confidence": average_confidence,
            "coverage": overall_coverage,
            "estimated_score_after_optimization": self._estimated_score_after_optimization(
                overall_score, recommendations
            ),
            "interview_probability": interview_probability,
            "strengths": self._strengths(breakdown),
            "weaknesses": self._weaknesses(breakdown),
            "missing_keywords": self._missing_keywords(breakdown),
            "recommendations": recommendations,
            "summary": self._summary(grade),
            "breakdown": breakdown,
            "category_ranking": self._category_ranking(breakdown),
        }

    # ==========================================================
    # Estimated Optimized Score
    # ==========================================================

    def _estimated_score_after_optimization(self, score, recommendations):

        improvement = min(len(recommendations) * 2, 10)

        return min(100, round(score + improvement, 1))

    # ==========================================================
    # Strengths
    # ==========================================================

    def _strengths(self, breakdown):

        strengths = []

        for category, result in breakdown.items():
            if result["coverage"] >= 0.80:
                strengths.append(category.replace("_", " ").title())

        return strengths

    # ==========================================================
    # Weaknesses
    # ==========================================================

    def _weaknesses(self, breakdown):

        weaknesses = []

        for category, result in breakdown.items():
            if result["coverage"] < 0.60:
                weaknesses.append(category.replace("_", " ").title())

        return weaknesses

    # ==========================================================
    # Category Ranking
    # ==========================================================

    def _category_ranking(self, breakdown):

        ranking = []

        for category, result in breakdown.items():
            ranking.append(
                {
                    "category": category,
                    "coverage": result["coverage"],
                    "score": result["score"],
                }
            )

        ranking.sort(key=lambda x: x["coverage"], reverse=True)

        return ranking

    # ==========================================================
    # Missing Keywords
    # ==========================================================

    def _missing_keywords(self, breakdown):

        missing = []

        for result in breakdown.values():
            missing.extend(result.get("missing", []))

        return sorted(set(missing), key=str.lower)

    # ==========================================================
    # Improvement Recommendations
    # ==========================================================

    def _recommendations(self, breakdown):

        recommendations = []

        for category, result in breakdown.items():
            if result["coverage"] >= 0.80:
                continue

            title = category.replace("_", " ").title()

            recommendations.append(f"Strengthen {title}")

        return recommendations

    # ==========================================================
    # Executive Summary
    # ==========================================================

    def _summary(self, grade):

        if grade == "A+":
            return "Outstanding ATS compatibility with excellent keyword coverage."

        if grade == "A":
            return "Excellent ATS compatibility with minor optimisation opportunities."

        if grade == "B":
            return "Strong resume with good ATS potential."

        if grade == "C":
            return "Average ATS compatibility."

        if grade == "D":
            return "Resume requires significant optimisation."

        return "Resume is unlikely to pass ATS screening."
