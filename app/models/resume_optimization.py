from dataclasses import dataclass, field
from typing import List


@dataclass
class ResumeOptimizationResult:

    # Career Positioning
    career_positioning: str = ""
    executive_summary: str = ""

    # Resume Content
    top_skills: List[str] = field(default_factory=list)
    top_projects: List[dict] = field(default_factory=list)
    top_achievements: List[str] = field(default_factory=list)
    top_responsibilities: List[str] = field(default_factory=list)

    # ATS Intelligence
    ats_keywords: List[str] = field(default_factory=list)
    missing_keywords: List[str] = field(default_factory=list)

    # Scores
    ats_before: float = 0
    ats_after: float = 0
    recruiter_score: float = 0
    hiring_manager_score: float = 0

    # Career Intelligence
    strengths: List[str] = field(default_factory=list)
    concerns: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    confidence: float = 0