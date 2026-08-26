from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from app.models.recruiter_decision import RecruiterDecision


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
    source_listing_url: str = ""
    application_url: str = ""
    application_url_type: str = "UNKNOWN_URL"
    application_url_source: str = ""
    application_portal: str = "UNKNOWN"
    application_route_confidence: str = "LOW"
    application_route_resolved_at: str = ""
    application_route_status: str = "APPLICATION_ROUTE_UNRESOLVED"
    market: str = ""

    # Job Details
    company: str = ""
    job_title: str = ""
    location: str = ""
    employment_type: str = ""
    salary: str = ""
    posted_date: str = ""
    job_description: str = ""
    remote_status: Optional[bool] = None
    work_arrangement: str = "UNKNOWN"
    remote_scope: str = "UNKNOWN"

    # AI Objects
    job_analysis: Optional[Any] = None
    employer: Optional[Any] = None
    decision: Optional[Any] = None
    recruiter: Optional[RecruiterDecision] = None
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

        # Kept local to avoid a model/service import cycle at module load time.
        from app.services.application_route_resolver import ApplicationRouteResolver
        source_listing_url = item.get("link", "")
        route = ApplicationRouteResolver().resolve({"job_url": source_listing_url, "application_url": item.get("applicationUrl", "")})

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
            job_url=source_listing_url,
            source_listing_url=source_listing_url,
            application_url=route.application_url,
            application_url_type=route.application_url_type,
            application_url_source="DISCOVERY_METADATA:applicationUrl" if item.get("applicationUrl") else "",
            application_portal=route.portal,
            application_route_confidence=route.route_confidence,
            application_route_resolved_at=route.resolved_at,
            application_route_status=route.resolution_status if route.resolution_status != "EXTERNAL_ROUTE_UNRESOLVED" else "SOURCE_ONLY",
            job_description=item.get("descriptionText", ""),
            remote_status=item.get("remote", None),

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
