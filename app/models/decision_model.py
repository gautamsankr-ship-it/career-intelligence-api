from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class ScoreCard:

    category: str

    weight: int

    score: float

    confidence: float

    matched: List[str] = field(default_factory=list)

    missing: List[str] = field(default_factory=list)

    reason: str = ""


@dataclass
class CareerDecision:

    overall_score: float

    confidence: float

    decision: str

    priority: str

    automation_level: str

    scorecards: List[ScoreCard]

    recommendations: List[str]

    resume_strategy: Dict

    cover_letter_strategy: Dict

    application_strategy: Dict