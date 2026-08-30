"""Conservative route resolution; it never guesses an employer application URL."""
from __future__ import annotations
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse
from app.models.application_route import ApplicationRoute

JOB_BOARDS = ("linkedin.com", "indeed.com")
ATS_HOSTS = {"greenhouse.io": "GREENHOUSE", "lever.co": "LEVER", "workday": "WORKDAY", "smartrecruiters": "SMARTRECRUITERS", "successfactors": "SUCCESSFACTORS", "oraclecloud": "ORACLE", "ashbyhq.com": "ASHBY"}
_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)


def _visible_text(html: str) -> str:
    """Genuinely visible page text -- see application_browser_service._visible_text.

    Duplicated locally (not imported) to avoid a circular import:
    application_browser_service already imports this module.
    """
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _SCRIPT_STYLE.sub(" ", html))).lower()

class _Links(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]; self.current=None; self.text=[]
    def handle_starttag(self, tag, attrs):
        if tag == "a": self.current=dict(attrs); self.text=[]
    def handle_data(self, text):
        if self.current is not None: self.text.append(text)
    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.links.append((self.current.get("href", ""), " ".join(self.text).strip(), self.current)); self.current=None

class ApplicationRouteResolver:
    def classify_url(self, url: str) -> str:
        host=urlparse(url).netloc.lower(); path=urlparse(url).path.lower()
        if any(board in host for board in JOB_BOARDS): return "JOB_LISTING_URL"
        if any(token in host for token in ATS_HOSTS): return "ATS_URL"
        if re.search(r"/(apply|application|careers?|jobs?)/", path): return "EMPLOYER_CAREER_URL"
        return "UNKNOWN_URL"

    def portal_for(self, url: str) -> str:
        host=urlparse(url).netloc.lower()
        return next((portal for token, portal in ATS_HOSTS.items() if token in host), "GENERIC" if self.classify_url(url) == "EMPLOYER_CAREER_URL" else "UNKNOWN")

    def resolve(self, vacancy: dict | object, listing_html: str | None = None, final_url: str | None = None) -> ApplicationRoute:
        get=lambda key: vacancy.get(key) if isinstance(vacancy, dict) else getattr(vacancy, key, None)
        source_listing=get("source_listing_url") or get("job_url") or ""
        # Official/direct fields always precede source-listing URLs.
        candidates=[("OFFICIAL_APPLICATION_URL", get("official_application_url")), ("EMPLOYER_APPLICATION_URL", get("employer_application_url")), ("ATS_APPLICATION_URL", get("ats_application_url")), ("EXISTING_APPLICATION_URL", get("application_url"))]
        for provenance, candidate in candidates:
            if candidate and self._valid(candidate) and self.classify_url(candidate) != "JOB_LISTING_URL":
                return self._resolved(source_listing, self._lever_apply_url(candidate), provenance, "HIGH")
        if source_listing and self.classify_url(source_listing) == "JOB_LISTING_URL":
            if listing_html:
                route=self.extract_external(source_listing, listing_html)
                if route: return route
                visible=_visible_text(listing_html)
                auth="YES" if re.search(r"sign in|log in|login", visible) else "NO"
                captcha="YES" if re.search(r"captcha|recaptcha|hcaptcha", visible) else "NO"
                return ApplicationRoute(source_listing, resolution_status="SOURCE_CAPTCHA_REQUIRED" if captcha == "YES" else "SOURCE_AUTH_REQUIRED" if auth == "YES" else "EXTERNAL_ROUTE_UNRESOLVED", source_authentication=auth, source_captcha=captcha, resolved_at=self._now())
            return ApplicationRoute(source_listing, resolution_status="EXTERNAL_ROUTE_UNRESOLVED", resolved_at=self._now())
        # A source listing alone is provenance, not evidence that it is the
        # employer application destination. Keep it source-only.
        return ApplicationRoute(source_listing, resolution_status="EXTERNAL_ROUTE_UNRESOLVED", resolved_at=self._now())

    def extract_external(self, listing_url: str, html: str) -> ApplicationRoute | None:
        links=_Links(); links.feed(html); source_host=urlparse(listing_url).netloc.lower()
        for href, text, attrs in links.links:
            candidate=urljoin(listing_url, href); host=urlparse(candidate).netloc.lower()
            intent=f"{text} {attrs.get('aria-label','')} {attrs.get('title','')}".lower()
            if self._valid(candidate) and host != source_host and re.search(r"apply|external|employer|career", intent):
                return self._resolved(listing_url, candidate, "JOB_BOARD_EXTERNAL_APPLY", "HIGH")
        return None

    @staticmethod
    def _lever_apply_url(url: str) -> str:
        """A Lever job-detail URL's real application form lives at .../apply.

        Deterministic, Lever-specific, same-job-id suffix normalization only
        -- never touches any other host, never fabricates an unrelated URL.
        A URL that already points at the apply form (or any non-Lever host)
        is returned unchanged, so this is safe to apply unconditionally to
        every resolved candidate.
        """
        parsed=urlparse(url)
        if "lever.co" not in parsed.netloc.lower(): return url
        path=parsed.path.rstrip("/")
        if path.endswith("/apply"): return url
        return urlunparse((parsed.scheme, parsed.netloc, path + "/apply", "", "", ""))

    def _resolved(self, source, destination, provenance, confidence):
        return ApplicationRoute(source, destination, self.classify_url(destination), provenance, confidence, "RESOLVED", [source, destination] if source and source != destination else [destination], self.portal_for(destination), resolved_at=self._now())
    @staticmethod
    def _valid(url): return urlparse(url).scheme in {"http", "https"} and bool(urlparse(url).netloc)
    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat()
