from app.models.decision_model import CareerDecision, ScoreCard

from app.services.scoring.skill_scorer import SkillScorer
from app.services.scoring.responsibility_scorer import ResponsibilityScorer
from app.services.scoring.experience_scorer import ExperienceScorer


class CareerDecisionEngine:

    def __init__(self):

        self.weights = {
            "skills": 25,
            "responsibilities": 20,
            "experience": 15,
            "industry": 10,
            "education": 10,
            "employer": 10,
            "career_growth": 10,
        }

        self.skill_scorer = SkillScorer()
        self.responsibility_scorer = ResponsibilityScorer()
        self.experience_scorer = ExperienceScorer()

    # ============================================================
    # MAIN DECISION ENGINE
    # ============================================================

    def evaluate(self, candidate, job, employer):

        scorecards = [

            self._score_skills(candidate, job),

            self._score_responsibilities(candidate, job),

            self._score_experience(candidate, job),

            self._score_industry(candidate, job),

            self._score_education(candidate, job),

            self._score_employer(employer),

            self._score_career_growth(candidate, employer)

        ]

        overall_score = self._calculate_total_score(scorecards)

        confidence = self._calculate_confidence(scorecards)

        if overall_score >= 90:

            decision = "APPROVE_AND_SEND"
            priority = "HIGH"
            automation = "FULL"

        elif overall_score >= 75:

            decision = "GENERATE_AND_QUEUE"
            priority = "HIGH"
            automation = "SEMI"

        else:

            decision = "REJECT"
            priority = "LOW"
            automation = "NONE"

        recommendations = []

        missing = []

        for card in scorecards:

            missing.extend(card.missing)

        if missing:

            recommendations.append(

                "Improve missing skills to increase future match score."

            )

        return CareerDecision(

            overall_score=overall_score,

            confidence=confidence,

            decision=decision,

            priority=priority,

            automation_level=automation,

            scorecards=scorecards,

            recommendations=recommendations,

            resume_strategy=self._build_resume_strategy(scorecards),

            cover_letter_strategy=self._build_cover_letter_strategy(scorecards),

            application_strategy=self._build_application_strategy(overall_score)

        )

    # ============================================================
    # CALCULATIONS
    # ============================================================

    def _calculate_total_score(self, scorecards):

        return round(

            sum(card.score for card in scorecards),

            1

        )

    def _calculate_confidence(self, scorecards):

        if not scorecards:

            return 0

        return round(

            sum(card.confidence for card in scorecards)

            / len(scorecards),

            1

        )

    # ============================================================
    # SCORERS
    # ============================================================

    def _score_skills(self, candidate, job):

        return self.skill_scorer.score(

            self.weights["skills"],

            candidate,

            job

        )

    def _score_responsibilities(self, candidate, job):

        return self.responsibility_scorer.score(

            self.weights["responsibilities"],

            candidate,

            job

        )

    def _score_experience(self, candidate, job):

        return self.experience_scorer.score(

            self.weights["experience"],

            candidate,

            job

        )
        # ============================================================
    # INDUSTRY
    # ============================================================

    def _score_industry(self, candidate, job):

        candidate_industries = [

            x.lower()

            for x in candidate.get("industries", [])

        ]

        text = (

            job.get("summary", "")

            + " "

            + " ".join(job.get("keywords", []))

        ).lower()

        matched = []

        for industry in candidate_industries:

            if industry in text:

                matched.append(industry)

        if candidate_industries:

            ratio = len(matched) / len(candidate_industries)

        else:

            ratio = 0

        score = round(

            ratio *

            self.weights["industry"],

            1

        )

        return ScoreCard(

            category="Industry",

            weight=self.weights["industry"],

            score=score,

            confidence=90,

            matched=matched,

            missing=[],

            reason=f"Matched {len(matched)} industries."

        )

    # ============================================================
    # EDUCATION
    # ============================================================

    def _score_education(self, candidate, job):

        education = candidate.get("education", [])

        degrees = [

            e.get("degree", "").lower()

            for e in education

        ]

        text = (

            job.get("summary", "")

            + " "

            + " ".join(job.get("keywords", []))

        ).lower()

        matched = []

        if "chartered accountant" in text or "ca" in text:

            if any("chartered accountant" in d for d in degrees):

                matched.append("Chartered Accountant")

        if "bachelor" in text:

            if any("bachelor" in d for d in degrees):

                matched.append("Bachelor")

        score = min(

            self.weights["education"],

            len(matched) * 5

        )

        return ScoreCard(

            category="Education",

            weight=self.weights["education"],

            score=score,

            confidence=90,

            matched=matched,

            missing=[],

            reason="Education assessment."

        )

    # ============================================================
    # EMPLOYER
    # ============================================================

    def _score_employer(self, employer):

        score = round(

            employer.overall_score / 10,

            1

        )

        score = min(

            score,

            self.weights["employer"]

        )

        return ScoreCard(

            category="Employer",

            weight=self.weights["employer"],

            score=score,

            confidence=95,

            matched=[

                employer.company

            ],

            missing=[],

            reason=employer.recommendation

        )

    # ============================================================
    # CAREER GROWTH
    # ============================================================

    def _score_career_growth(self, candidate, employer):

        score = round(

            employer.career_growth_score / 10,

            1

        )

        score = min(

            score,

            self.weights["career_growth"]

        )

        return ScoreCard(

            category="Career Growth",

            weight=self.weights["career_growth"],

            score=score,

            confidence=90,

            matched=[],

            missing=[],

            reason="Career growth evaluation."

        )

    # ============================================================
    # STRATEGIES
    # ============================================================

    def _build_resume_strategy(self, scorecards):

        highlight = []

        keywords = []

        improve = []

        for card in scorecards:

            highlight.extend(card.matched)

            keywords.extend(card.matched)

            improve.extend(card.missing)

        return {

            "highlight": sorted(set(highlight)),

            "keywords": sorted(set(keywords)),

            "improve": sorted(set(improve))

        }

    def _build_cover_letter_strategy(self, scorecards):

        strengths = []

        for card in scorecards:

            strengths.extend(card.matched)

        return {

            "strengths": sorted(set(strengths))

        }

    def _build_application_strategy(self, overall_score):

        if overall_score >= 90:

            action = "APPROVE_AND_SEND"

        elif overall_score >= 75:

            action = "GENERATE_AND_QUEUE"

        else:

            action = "REJECT"

        return {

            "action": action,

            "generate_resume": overall_score >= 75,

            "generate_cover_letter": overall_score >= 75,

            "queue": overall_score >= 75,

            "send": overall_score >= 90

        }