from dataclasses import dataclass, field
from typing import List


@dataclass
class ImprovementItem:

    category: str

    points: float

    explanation: str


@dataclass
class ResumeImprovement:

    raw_score: float

    optimized_score: float

    improvement: float

    confidence: float

    improvements: List[ImprovementItem] = field(default_factory=list)

    summary: str = ""