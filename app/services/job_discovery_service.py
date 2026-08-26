import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from app.config import (
    USE_CACHE,
    MAX_JOBS,
    REMOTE_ONLY,
)

from app.services.linkedin_url_builder import LinkedInURLBuilder
from app.services.apify_service import ApifyJobService
from app.services.cache_service import CacheService
from app.models.career_opportunity import CareerOpportunity


class JobDiscoveryService:

    def __init__(self):

        self.builder = LinkedInURLBuilder()

        self.scraper = ApifyJobService()

        self.cache = CacheService()

    # ==========================================================
    # Discover Jobs
    # ==========================================================

    def discover_jobs(self, limit: int | None = MAX_JOBS) -> list[CareerOpportunity]:

        if USE_CACHE:

            if not self.cache.exists():

                raise FileNotFoundError(

                    "\nNo cached jobs found.\n"
                    "Run:\n"
                    "python refresh_jobs.py\n"
                    "to download fresh jobs."

                )

            print("\nLoading jobs from cache...")

            jobs = self.cache.load_jobs()

            print(f"Loaded {len(jobs)} cached jobs.")

        else:

            raise RuntimeError(

                "USE_CACHE=False.\n"
                "Use refresh_jobs.py to refresh jobs."

            )

        jobs = self.remove_duplicates(jobs)

        if REMOTE_ONLY:

            jobs = self.filter_remote_jobs(jobs)

        jobs.sort(

            key=lambda x: x.posted_date,

            reverse=True

        )

        return jobs if limit is None else jobs[:limit]

    # ==========================================================
    # Duplicate Removal
    # ==========================================================

    def remove_duplicates(

        self,

        opportunities: list[CareerOpportunity]

    ) -> list[CareerOpportunity]:

        unique = {}

        for job in opportunities:

            key = self._cross_source_duplicate_key(job)

            if key not in unique:

                unique[key] = job
                continue

            # When a confidently matched vacancy is also available on the
            # employer's public career site, retain its official application
            # route while recording the discovery provenance.  The identity
            # key still has to match first; employer names alone never cause
            # this replacement.
            existing = unique[key]
            existing_sources = set((existing.metadata or {}).get("discovered_sources", [existing.source]))
            candidate_sources = set((job.metadata or {}).get("discovered_sources", [job.source]))
            sources = sorted(existing_sources | candidate_sources)
            if job.source == "EmployerCareerSite" and existing.source != "EmployerCareerSite":
                job.metadata["discovered_sources"] = sources
                unique[key] = job
            else:
                existing.metadata["discovered_sources"] = sources

        # A source-only board listing and an ATS representation often have
        # different URL identities. Conservatively enrich only when the stable
        # employer/title/location/date evidence agrees.
        enriched = {}
        for job in unique.values():
            key = (job.company.lower().strip(), job.job_title.lower().strip(), job.location.lower().strip(), (job.posted_date or "").strip())
            if not all(key[:2]) or not key[3]:
                enriched[("unique", id(job))] = job; continue
            if key not in enriched:
                enriched[key] = job; continue
            current = enriched[key]
            winner, other = (job, current) if self._route_rank(job) > self._route_rank(current) else (current, job)
            self._merge_route(winner, other)
            enriched[key] = winner
        return list(enriched.values())

    @staticmethod
    def _route_rank(job):
        quality = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(job.application_route_confidence, 0)
        kind = {"ATS_URL": 4, "EMPLOYER_CAREER_URL": 3, "DIRECT_APPLICATION_URL": 2}.get(job.application_url_type, 0)
        return quality, kind, bool(job.application_url)

    @staticmethod
    def _merge_route(winner, other):
        sources = set((winner.metadata or {}).get("discovered_sources", [winner.source])) | set((other.metadata or {}).get("discovered_sources", [other.source]))
        winner.metadata["discovered_sources"] = sorted(sources)
        listings = set(filter(None, (winner.metadata or {}).get("source_listing_urls", [winner.source_listing_url or winner.job_url]))) | set(filter(None, (other.metadata or {}).get("source_listing_urls", [other.source_listing_url or other.job_url])))
        winner.metadata["source_listing_urls"] = sorted(listings)

    @staticmethod
    def _cross_source_duplicate_key(job: CareerOpportunity):
        """Prefer an application URL before source-local IDs or content identity."""
        application_url = (job.application_url or "").strip()
        if application_url:
            parsed = urlsplit(application_url)
            normalized_url = urlunsplit(
                (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")
            )
            return ("application_url", normalized_url)
        if job.id:
            return ("source_id", job.source.lower().strip(), job.id.strip().lower())
        description = re.sub(r"\s+", " ", (job.job_description or "").strip().lower())
        description_hash = hashlib.sha256(description.encode("utf-8")).hexdigest()
        return (
            "content",
            job.company.lower().strip(),
            job.job_title.lower().strip(),
            job.location.lower().strip(),
            description_hash,
        )

    # ==========================================================
    # Remote Filter
    # ==========================================================

    def filter_remote_jobs(

        self,

        opportunities: list[CareerOpportunity]

    ) -> list[CareerOpportunity]:

        return [job for job in opportunities if self.work_arrangement(job) == "REMOTE"]

    @staticmethod
    def work_arrangement(job: CareerOpportunity) -> str:
        """Strict policy: only confirmed remote roles enter the active cache.

        Structured source metadata wins. Location is a narrow fallback only;
        descriptions are deliberately not used to infer remote eligibility.
        """
        stated = (job.work_arrangement or "").upper()
        if stated in {"REMOTE", "HYBRID", "ON_SITE"}:
            return stated
        if job.remote_status is True:
            return "REMOTE"
        if job.remote_status is False:
            return "ON_SITE"
        location = (job.location or "").lower()
        if "hybrid" in location:
            return "HYBRID"
        if any(term in location for term in ("on-site", "onsite", "on site")):
            return "ON_SITE"
        if any(term in location for term in ("remote", "work from home")):
            return "REMOTE"
        return "UNKNOWN"

    def work_arrangement_counts(self, opportunities: list[CareerOpportunity]) -> dict[str, int]:
        counts = {"REMOTE": 0, "HYBRID": 0, "ON_SITE": 0, "UNKNOWN": 0}
        for job in opportunities:
            counts[self.work_arrangement(job)] += 1
        return counts
