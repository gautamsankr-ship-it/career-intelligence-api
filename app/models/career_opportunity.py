from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any


@dataclass
class TimelineEvent:
    stage: str
    timestamp: datetime
    notes: str = ""


@dataclass
class CareerOpportunity:

    # Identity
    id: str = ""
    source: str = ""
    job_url: str = ""

    # Job Details
    company: str = ""
    job_title: str = ""
    location: str = ""
    employment_type: str = ""
    salary: str = ""
    posted_date: str = ""
    job_description: str = ""

    # AI Objects
    job_analysis: Optional[Any] = None
    employer: Optional[Any] = None
    decision: Optional[Any] = None
    resume_improvement: Optional[Any] = None

    # Scores
    raw_score: float = 0.0
    optimized_score: float = 0.0
    improvement: float = 0.0
    confidence: float = 0.0

    # Documents
    resume_file: str = ""
    cover_letter_file: str = ""
    application_package: str = ""

    # Workflow
    status: str = "DISCOVERED"
    priority: str = "LOW"
    automation_level: str = "NONE"
    approved: bool = False
    submitted: bool = False

    # Timeline
    timeline: List[TimelineEvent] = field(default_factory=list)

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_apify(cls, item):

        return cls(

            # Identity
            id=item.get("id", ""),
            source="LinkedIn",

            # Job
            company=item.get("companyName", ""),
            job_title=item.get("title", ""),
            location=item.get("location", ""),
            employment_type=item.get("employmentType", ""),
            salary=item.get("salary", ""),
            posted_date=item.get("postedAt", ""),

            # IMPORTANT FIXES
            job_url=item.get("link", ""),
            job_description=item.get("descriptionText", ""),

            # Store complete raw record
            metadata=item,
        )

    def add_event(self, stage: str, notes: str = ""):

        self.timeline.append(
            TimelineEvent(
                stage=stage,
                timestamp=datetime.utcnow(),
                notes=notes
            )
        )

        self.status = stage
        self.updated_at = datetime.utcnow()

    def update_scores(
        self,
        raw: float,
        optimized: float = 0,
        confidence: float = 0
    ):

        self.raw_score = raw
        self.optimized_score = optimized
        self.improvement = round(optimized - raw, 1)
        self.confidence = confidence
        self.updated_at = datetime.utcnow()