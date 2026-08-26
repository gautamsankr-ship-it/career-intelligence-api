"""Bounded public ATS discovery for explicitly selected target employers."""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

from app.models.career_opportunity import CareerOpportunity
from app.services.discovery_quality_gate import DISCOVERY_RELEVANT, DiscoveryQualityGate
from app.services.job_sources import _remote_scope, _work_arrangement_evidence
from app.services.target_employer_registry import TARGET_EMPLOYERS


DEFAULT_CATALOGUE_SCAN_LIMIT = 100
VALIDATION_CATALOGUE_SCAN_LIMIT = 25


class TargetEmployerDiscovery:
    """Select relevant public-ATS vacancies before applying the result cap."""

    def __init__(self, requester=None):
        self.requester = requester or self._request
        self.failures: dict[str, str] = {}
        self.diagnostics: dict[str, dict[str, int | str]] = {}
        self.relevance_gate = DiscoveryQualityGate()

    @staticmethod
    def _request(url):
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "CareerIntelligence/1.0"})
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def discover(self, count, employer_ids=(), market=None, tier=None, max_employers=3, scan_limit=DEFAULT_CATALOGUE_SCAN_LIMIT):
        if scan_limit < 1:
            raise ValueError("scan_limit must be at least 1")
        selected = [e for e in TARGET_EMPLOYERS if e.enabled and e.discovery_method == "PUBLIC_STRUCTURED_ENDPOINT"]
        if employer_ids:
            selected = [e for e in selected if e.employer_id in set(employer_ids)]
        if tier:
            selected = [e for e in selected if e.tier == tier]
        jobs = []
        for employer in selected[:max_employers]:
            try:
                jobs.extend(self._discover_employer(employer, count, market, scan_limit))
            except Exception as exc:
                self.failures[employer.employer_id] = str(exc)
        return jobs

    def discover_validation_targets(self, count=20, *, portal=None, market=None,
                                    max_employers=3, scan_limit=VALIDATION_CATALOGUE_SCAN_LIMIT):
        """Return bounded public Greenhouse/Lever routes for adapter testing only.

        This deliberately does not invoke the discovery relevance gate, remote
        filtering, cache, tracker, or application-history workflows.
        """
        if count < 1 or max_employers < 1 or scan_limit < 1:
            raise ValueError("count, max_employers, and scan_limit must be at least 1")
        requested = portal.upper() if portal else None
        if requested and requested not in {"GREENHOUSE", "LEVER"}:
            raise ValueError("validation targets support GREENHOUSE or LEVER only")
        selected = [e for e in TARGET_EMPLOYERS if e.enabled and e.discovery_method == "PUBLIC_STRUCTURED_ENDPOINT"
                    and e.ats_platform in {"GREENHOUSE", "LEVER"}]
        if requested:
            selected = [e for e in selected if e.ats_platform == requested]
        jobs = []
        for employer in selected[:max_employers]:
            try:
                remaining = max(0, count - len(jobs))
                platform, identifier = employer.ats_for_market(market)
                endpoint_is_market_specific = any(entry[0] == market for entry in employer.ats_by_market)
                normalized_market = market if endpoint_is_market_specific else ""
                if platform == "LEVER":
                    raw_items = self.requester(f"https://api.lever.co/v0/postings/{identifier}?mode=json&limit={scan_limit}")
                    catalogue = [self._normalize_lever(employer, item, normalized_market) for item in raw_items[:scan_limit]]
                else:
                    data = self.requester(f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs?content=true")
                    catalogue = [self._normalize_greenhouse(employer, item, normalized_market) for item in data.get("jobs", [])[:scan_limit]]
                for job in catalogue:
                    # Explicit marker prevents this data from being mistaken for a
                    # career-relevant opportunity by downstream diagnostics.
                    job.metadata["validation_only"] = True
                jobs.extend(catalogue[:remaining])
                self.diagnostics[employer.employer_id] = {
                    "ats": platform, "catalogue_inspected": len(catalogue),
                    "returned": min(len(catalogue), remaining),
                    "validation_only": True,
                }
            except Exception as exc:
                self.failures[employer.employer_id] = type(exc).__name__
            if len(jobs) >= count:
                break
        return jobs[:count]

    def _discover_employer(self, employer, count, market, scan_limit):
        platform, identifier = employer.ats_for_market(market)
        endpoint_is_market_specific = any(entry[0] == market for entry in employer.ats_by_market)
        normalized_market = market if endpoint_is_market_specific else ""
        if platform == "LEVER":
            raw_items = self.requester(f"https://api.lever.co/v0/postings/{identifier}?mode=json&limit={scan_limit}")
            catalogue = [self._normalize_lever(employer, item, normalized_market) for item in raw_items[:scan_limit]]
        elif platform == "GREENHOUSE":
            data = self.requester(f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs?content=true")
            catalogue = [self._normalize_greenhouse(employer, item, normalized_market) for item in data.get("jobs", [])[:scan_limit]]
        else:
            return []

        candidates, irrelevant, ambiguous = [], 0, 0
        for job in catalogue:
            relevance = self.relevance_gate.classify_relevance(job)
            job.metadata["target_prefilter"] = relevance.classification
            job.metadata["career_track"] = relevance.career_track
            job.metadata["opportunity_themes"] = list(relevance.opportunity_themes)
            if relevance.classification == DISCOVERY_RELEVANT:
                candidates.append(job)
            elif relevance.classification == "DISCOVERY_AMBIGUOUS":
                ambiguous += 1
            else:
                irrelevant += 1

        returned = candidates[:count]
        self.diagnostics[employer.employer_id] = {
            "ats": platform, "catalogue_inspected": len(catalogue),
            "strong_relevant_candidates": len(candidates), "ambiguous_candidates": ambiguous,
            "irrelevant_skipped": irrelevant, "returned": len(returned),
        }
        return returned

    def _opportunity(self, employer, market, job_id, title, location, description, job_url, application_url):
        arrangement, evidence = _work_arrangement_evidence({"title": title, "location": location, "description": description})
        platform, _ = employer.ats_for_market(market)
        metadata = {"target_employer_id": employer.employer_id, "employer_category": employer.category, "employer_tier": employer.tier, "industry_tags": list(employer.industry_tags), "ats_platform": platform, "ats_job_id": job_id, "careers_site": employer.careers_url, "discovery_method": employer.discovery_method, "work_arrangement_evidence": evidence}
        direct = application_url or job_url or ""
        return CareerOpportunity(id=str(job_id), source="EmployerCareerSite", company=employer.name, job_title=title or "", location=location or "", market=market or "", job_description=description or "", job_url=job_url or "", source_listing_url=job_url or "", application_url=direct, application_url_type="ATS_URL", application_url_source="TARGET_EMPLOYER_ATS", application_portal=platform, application_route_confidence="HIGH", application_route_status="DIRECT_ROUTE", work_arrangement=arrangement, remote_status=True if arrangement == "REMOTE" else False if arrangement == "ON_SITE" else None, remote_scope=_remote_scope({"description": description}, arrangement), metadata=metadata)

    def _normalize_lever(self, employer, item, market):
        categories = item.get("categories") or {}
        return self._opportunity(employer, market, item.get("id"), item.get("text"), categories.get("location"), item.get("descriptionPlain") or item.get("description"), item.get("hostedUrl"), item.get("applyUrl") or item.get("hostedUrl"))

    def _normalize_greenhouse(self, employer, item, market):
        location = (item.get("location") or {}).get("name", "")
        return self._opportunity(employer, market, item.get("id"), item.get("title"), location, item.get("content", ""), item.get("absolute_url"), item.get("absolute_url"))
