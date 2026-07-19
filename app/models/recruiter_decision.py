from dataclasses import dataclass, field


@dataclass
class RecruiterDecision:

    # =====================================================
    # Overall Recommendation
    # =====================================================

    final_score: float = 0.0

    interview_probability: float = 0.0

    recommendation: str = ""

    risk_level: str = ""

    confidence: float = 0.0

    # =====================================================
    # Recruiter Assessment
    # =====================================================

    technical_fit: float = 0.0

    business_fit: float = 0.0

    leadership_fit: float = 0.0

    transferability: float = 0.0

    career_alignment: float = 0.0

    # =====================================================
    # Explanation
    # =====================================================

    strengths: list = field(default_factory=list)

    transferable_skills: list = field(default_factory=list)

    critical_gaps: list = field(default_factory=list)

    recommendations: list = field(default_factory=list)