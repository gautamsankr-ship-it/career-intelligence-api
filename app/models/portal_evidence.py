from dataclasses import asdict, dataclass, field


@dataclass
class PortalEvidence:
    portal: str = "UNKNOWN"
    confidence: str = "LOW"
    signals: list[str] = field(default_factory=list)
    wrapper_detected: bool = False

    def to_dict(self): return asdict(self)
