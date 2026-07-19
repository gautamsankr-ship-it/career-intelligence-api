from dataclasses import dataclass
from typing import Any, Optional

from app.models.recruiter_decision import RecruiterDecision


@dataclass
class ApplicationContext:
    """
    Carries all information related to one job application
    through the processing pipeline.
    """

    # --------------------------------------------------
    # Input
    # --------------------------------------------------

    candidate: dict
    job_description: str

    # --------------------------------------------------
    # AI Analysis
    # --------------------------------------------------

    job_analysis: Optional[dict] = None

    employer: Optional[Any] = None

    decision: Optional[Any] = None

    recruiter: Optional[RecruiterDecision] = None

    # --------------------------------------------------
    # Generated Documents
    # --------------------------------------------------

    resume: Optional[Any] = None

    cover_letter: Optional[Any] = None

    # --------------------------------------------------
    # Generated Files
    # --------------------------------------------------

    resume_file: str = ""

    cover_letter_file: str = ""

    report_file: str = ""