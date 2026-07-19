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

    def discover_jobs(self) -> list[CareerOpportunity]:

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

        return jobs[:MAX_JOBS]

    # ==========================================================
    # Duplicate Removal
    # ==========================================================

    def remove_duplicates(

        self,

        opportunities: list[CareerOpportunity]

    ) -> list[CareerOpportunity]:

        unique = {}

        for job in opportunities:

            key = (

                job.company.lower().strip(),

                job.job_title.lower().strip(),

                job.location.lower().strip()

            )

            if key not in unique:

                unique[key] = job

        return list(unique.values())

    # ==========================================================
    # Remote Filter
    # ==========================================================

    def filter_remote_jobs(

        self,

        opportunities: list[CareerOpportunity]

    ) -> list[CareerOpportunity]:

        remote = []

        banned = [

            "on-site",

            "onsite",

            "hybrid",

            "office",

            "work authorization",

            "visa required",

            "citizenship",

            "security clearance",

            "must reside",

            "must live"

        ]

        preferred = [

            "remote",

            "work from home",

            "worldwide",

            "distributed",

            "global",

            "anywhere"

        ]

        for job in opportunities:

            text = (

                f"{job.location} "

                f"{job.job_description}"

            ).lower()

            if any(x in text for x in banned):

                continue

            if any(x in text for x in preferred):

                remote.append(job)

        return remote