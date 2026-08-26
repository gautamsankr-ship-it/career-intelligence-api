"""Bounded diagnostic snapshot of discovery-time application routes.

This deliberately sits outside the production cache and tracker.  It exists so
public ATS routes remain inspectable even when the associated listing is later
excluded by the strict remote-only gate.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SNAPSHOT_FILE = Path("app/data/discovery_application_routes.json")
RETENTION_LIMIT = 200


class DiscoveryRouteSnapshotService:
    def __init__(self, path: str | Path = SNAPSHOT_FILE, retention_limit: int = RETENTION_LIMIT):
        self.path = Path(path); self.retention_limit = retention_limit

    def load(self) -> list[dict]:
        if not self.path.exists(): return []
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save_routes(self, jobs) -> list[dict]:
        return self._save(jobs, validation_only=False)

    def save_validation_routes(self, jobs) -> list[dict]:
        """Persist test-only ATS candidates without touching production stores."""
        return self._save(jobs, validation_only=True)

    def _save(self, jobs, validation_only: bool) -> list[dict]:
        existing = {self._key(record): record for record in self.load()}
        now = datetime.now(timezone.utc).isoformat()
        for job in jobs:
            if not getattr(job, "application_url", ""): continue
            record = {
                "company": job.company, "job_title": job.job_title, "market": job.market,
                "career_track": (job.metadata or {}).get("career_track", "UNKNOWN"),
                "work_arrangement": job.work_arrangement, "source_listing_url": job.source_listing_url or job.job_url,
                "application_url": job.application_url, "application_url_type": job.application_url_type,
                "application_url_source": job.application_url_source, "application_portal": job.application_portal,
                "route_confidence": job.application_route_confidence,
                "browser_ready": bool(job.application_url and job.application_route_confidence in {"HIGH", "MEDIUM"}),
                "discovered_at": now,
            }
            if validation_only:
                record["validation_only"] = True
            existing[self._key(record)] = record
        records = sorted(existing.values(), key=lambda x: x.get("discovered_at", ""), reverse=True)[:self.retention_limit]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        return records

    @staticmethod
    def _key(record: dict) -> tuple:
        url = record.get("application_url", "")
        if url:
            parsed = urlsplit(url)
            return ("url", urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")))
        return ("identity", record.get("company", "").lower().strip(), record.get("job_title", "").lower().strip(), record.get("market", "").lower().strip())
