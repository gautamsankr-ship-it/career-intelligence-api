from dataclasses import dataclass, field
from typing import Dict, Optional

from app.models.decision_model import CareerDecision


@dataclass
class ApplicationContext:

    # Input

    candidate: Dict

    job_description: str

    # Generated

    job_analysis: Optional[Dict] = None

    employer: Optional[Dict] = None

    decision: Optional[CareerDecision] = None

    resume: Optional[str] = None

    cover_letter: Optional[str] = None

    resume_file: Optional[str] = None

    cover_letter_file: Optional[str] = None

    metadata: Dict = field(default_factory=dict)