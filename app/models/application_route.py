from __future__ import annotations
from dataclasses import asdict, dataclass, field

@dataclass
class ApplicationRoute:
    source_listing_url: str = ""
    application_url: str = ""
    application_url_type: str = "UNKNOWN_URL"
    application_url_source: str = ""
    route_confidence: str = "LOW"
    resolution_status: str = "EXTERNAL_ROUTE_UNRESOLVED"
    redirect_chain: list[str] = field(default_factory=list)
    portal: str = "UNKNOWN"
    source_authentication: str = "NO"
    source_captcha: str = "NO"
    resolved_at: str = ""
    def to_dict(self): return asdict(self)
