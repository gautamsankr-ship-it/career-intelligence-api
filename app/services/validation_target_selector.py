"""Read-only ranking and bounded inspection of isolated ATS validation targets."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.services.application_live_validation import LiveValidationService
from app.services.discovery_route_snapshot import DiscoveryRouteSnapshotService

PROBE_FILE = Path("app/data/application_validation_probe_results.json")
SUPPORTED_PORTALS = {"GREENHOUSE", "LEVER"}
BLOCKING_STATES = {
    "CAPTCHA_REQUIRED", "AUTH_REQUIRED", "MFA_REQUIRED",
    "ACCOUNT_CREATION_REQUIRED", "FORM_NOT_FOUND", "PORTAL_LIMITED",
    "TIMEOUT", "BROWSER_ERROR",
}


class ValidationTargetSelector:
    def __init__(self, snapshot=None, probe_file=PROBE_FILE, validation_service=None):
        self.snapshot = snapshot or DiscoveryRouteSnapshotService()
        self.probe_file = Path(probe_file)
        self.validation = validation_service or LiveValidationService()

    def candidates(self, portal=None, company=None, browser_ready=False):
        records = []
        for record in self.snapshot.load():
            if record.get("application_portal") not in SUPPORTED_PORTALS:
                continue
            if not record.get("application_url") or not record.get("browser_ready"):
                continue
            if portal and record["application_portal"].lower() != portal.lower():
                continue
            if company and company.lower() not in record.get("company", "").lower():
                continue
            records.append(dict(record))
        return records

    def results(self):
        return json.loads(self.probe_file.read_text(encoding="utf-8")) if self.probe_file.exists() else {}

    def save_results(self, results):
        self.probe_file.parent.mkdir(parents=True, exist_ok=True)
        self.probe_file.write_text(json.dumps(results, indent=2), encoding="utf-8")

    def ranked(self, **filters):
        known, output = self.results(), []
        for record in self.candidates(**filters):
            result = known.get(record["application_url"], {})
            # Discovery evidence is retained if a malformed/legacy probe lacks it.
            output.append({
                **record,
                "application_portal": result.get("portal") if result.get("portal") in SUPPORTED_PORTALS else record["application_portal"],
                "route_confidence": result.get("portal_confidence") or record.get("route_confidence", "UNKNOWN"),
                "probe_state": result.get("probe_state", "UNPROBED"),
                "fields_detected": result.get("fields_detected", 0),
                "surface": result.get("surface", "-"),
            })
        return sorted(output, key=self._rank_key)

    def probe(self, limit=5, refresh=False, **filters):
        selected, known = self.ranked(**filters)[:limit], self.results()
        for record in selected:
            stable_url = record["application_url"]
            if stable_url in known and not refresh:
                continue  # Never repeatedly revisit a cached CAPTCHA/blocker route.
            try:
                session = asyncio.run(self.validation.validate_url(
                    stable_url, record.get("market") or "united_kingdom", headed=True,
                    fill=False, allow_safe_navigation=False, max_pages=1,
                ))
                known[stable_url] = self._compact_result(record, session)
            except Exception as exc:
                # This is the only selector-created BROWSER_ERROR: an actual browser
                # invocation exception, never a fallback for a known session state.
                known[stable_url] = self._compact_result(record, {
                    "state": "BROWSER_ERROR", "fields_detected": 0,
                    "failure_reason": type(exc).__name__,
                })
        self.save_results(known)
        return self.ranked(**filters)

    @classmethod
    def _compact_result(cls, record, session):
        portal = cls._strongest_portal(record, session)
        surface = session.get("application_surface")
        return {
            # Always retain the original discovery/application URL, not a resolved
            # iframe URL (which may carry a short-lived Greenhouse token).
            "application_url": record["application_url"],
            "portal": portal,
            "portal_confidence": cls._portal_confidence(record, session, portal),
            "probe_state": cls._probe_state(session, portal),
            "fields_detected": int(session.get("fields_detected") or 0),
            "surface": surface if surface else "NOT_FOUND",
            "wrapper_detected": bool(session.get("wrapper_detected", False)),
            "failure_reason": cls._safe_failure_reason(session.get("failure_reason")),
            "probed_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _strongest_portal(record, session):
        # A direct discovery route is strongest; never let GENERIC/UNKNOWN overwrite it.
        discovered = record.get("application_portal")
        if discovered in SUPPORTED_PORTALS:
            return discovered
        if session.get("portal") in SUPPORTED_PORTALS:
            return session["portal"]
        evidence = session.get("portal_evidence") or {}
        return evidence.get("portal") if evidence.get("portal") in SUPPORTED_PORTALS else (discovered or "UNKNOWN")

    @staticmethod
    def _portal_confidence(record, session, portal):
        evidence = session.get("portal_evidence") or {}
        candidates = [record.get("route_confidence"), evidence.get("confidence")]
        # Preserve the strongest positive discovery/live evidence; a low-quality
        # browser fallback must never downgrade a known direct ATS route.
        return next((value for value in ("HIGH", "MEDIUM", "LOW") if value in candidates), "UNKNOWN")

    @staticmethod
    def _safe_failure_reason(value):
        # Keep compact exception-class diagnostics only; never retain page/browser
        # content, URLs, queries, or tokens.
        return value if isinstance(value, str) and value.isidentifier() else ""

    @staticmethod
    def _probe_state(session, portal):
        state = session.get("state") or "UNKNOWN"
        if state == "GREENHOUSE_WRAPPER_FORM_NOT_FOUND":
            return "FORM_NOT_FOUND"
        if state in BLOCKING_STATES:
            return state
        if state == "INSPECTED" and portal in SUPPORTED_PORTALS and int(session.get("fields_detected") or 0) > 0:
            return "FORM_ACCESSIBLE"
        return state

    @staticmethod
    def _rank_key(record):
        state = record.get("probe_state", "UNPROBED")
        order = {"FORM_ACCESSIBLE": 0, "UNPROBED": 1, "INSPECTED": 2, "MANUAL_INPUT_REQUIRED": 3,
                 "CAPTCHA_REQUIRED": 4, "AUTH_REQUIRED": 5, "MFA_REQUIRED": 6,
                 "ACCOUNT_CREATION_REQUIRED": 7, "FORM_NOT_FOUND": 8, "PORTAL_LIMITED": 9,
                 "TIMEOUT": 10, "BROWSER_ERROR": 11, "UNKNOWN": 12}
        direct = 0 if record.get("application_url_type") == "ATS_URL" else 1
        confidence = 0 if record.get("route_confidence") == "HIGH" else 1
        return (order.get(state, 13), direct, confidence, -record.get("fields_detected", 0), record.get("company", ""))
