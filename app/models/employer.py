from dataclasses import dataclass, field
from typing import List


@dataclass
class Employer:

    company: str

    industry: str

    company_size: str

    remote_friendly: bool

    innovation_score: float

    culture_score: float

    career_growth_score: float

    financial_stability_score: float

    overall_score: float

    strengths: List[str] = field(default_factory=list)

    risks: List[str] = field(default_factory=list)

    recommendation: str = ""

    reason: str = ""