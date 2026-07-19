from app.models.recruiter_decision import RecruiterDecision


class RecruiterReasoningService:

    def evaluate(
        self,
        candidate,
        job,
        employer,
        decision
    ):

        recruiter = RecruiterDecision()

        scorecards = {

            c.category: c

            for c in decision.scorecards

        }

        # =====================================================
        # Technical Fit
        # =====================================================

        skills = scorecards.get("Skills")

        responsibilities = scorecards.get("Responsibilities")

        technical = 0

        if skills:

            technical += (

                skills.score / skills.weight

            ) * 60

        if responsibilities:

            technical += (

                responsibilities.score

                / responsibilities.weight

            ) * 40

        recruiter.technical_fit = round(

            technical,

            1

        )

        # =====================================================
        # Business Fit
        # =====================================================

        experience = scorecards.get("Experience")

        industry = scorecards.get("Industry")

        business = 0

        if experience:

            business += (

                experience.score

                / experience.weight

            ) * 70

        if industry:

            business += (

                industry.score

                / industry.weight

            ) * 30

        recruiter.business_fit = round(

            business,

            1

        )

        # =====================================================
        # Leadership
        # =====================================================

        recruiter.leadership_fit = 90

        candidate_title = (

            candidate

            .get("candidate", {})

            .get("title", "")

            .lower()

        )

        if "manager" in candidate_title:

            recruiter.leadership_fit += 5

        if "partner" in candidate_title:

            recruiter.leadership_fit += 5

        recruiter.leadership_fit = min(

            recruiter.leadership_fit,

            100

        )

        # =====================================================
        # Transferability
        # =====================================================

        recruiter.transferability = 80

        if recruiter.business_fit < 40:

            recruiter.transferability = 60

        if recruiter.technical_fit < 40:

            recruiter.transferability -= 20

        # =====================================================
        # Career Alignment
        # =====================================================

        recruiter.career_alignment = employer.career_growth_score * 10

        recruiter.career_alignment = min(

            recruiter.career_alignment,

            100

        )

        # =====================================================
        # Final Score
        # =====================================================

        recruiter.final_score = round(

            recruiter.technical_fit * 0.40 +

            recruiter.business_fit * 0.20 +

            recruiter.leadership_fit * 0.10 +

            recruiter.transferability * 0.15 +

            recruiter.career_alignment * 0.15,

            1

        )

        recruiter.interview_probability = recruiter.final_score

        # =====================================================
        # Decision
        # =====================================================

        if recruiter.final_score >= 85:

            recruiter.recommendation = "APPLY"

            recruiter.risk_level = "LOW"

        elif recruiter.final_score >= 70:

            recruiter.recommendation = "REVIEW"

            recruiter.risk_level = "MEDIUM"

        else:

            recruiter.recommendation = "SKIP"

            recruiter.risk_level = "HIGH"

        # =====================================================
        # Strengths
        # =====================================================

        recruiter.strengths = [

            "Finance Leadership",

            "Australian Accounting",

            "Data Analytics",

            "Business Automation"

        ]

        recruiter.transferable_skills = [

            "Financial Reporting",

            "Forecasting",

            "Business Partnering",

            "Stakeholder Management"

        ]

        recruiter.critical_gaps = [

            x

            for x in decision.resume_strategy["improve"]

        ]

        recruiter.recommendations = [

            "Tailor resume to emphasize matching responsibilities.",

            "Move relevant projects to the first page.",

            "Highlight measurable achievements."

        ]

        recruiter.confidence = decision.confidence

        return recruiter