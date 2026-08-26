import json
from pathlib import Path
from dataclasses import asdict

from app.models.career_opportunity import CareerOpportunity


class CacheService:

    def __init__(self):

        self.cache_dir = Path("app/data/cache")

        self.cache_dir.mkdir(

            parents=True,

            exist_ok=True

        )

        self.jobs_file = self.cache_dir / "raw_jobs.json"
        self.arrangement_review_file = self.cache_dir / "arrangement_review_jobs.json"

    # ==========================================================
    # Save Jobs
    # ==========================================================

    def save_jobs(

        self,

        jobs: list[CareerOpportunity]

    ):

        self._save_to_file(self.jobs_file, jobs)

    def save_arrangement_review_jobs(self, jobs: list[CareerOpportunity]):
        """Persist diagnostic UNKNOWN listings without adding tracker records."""
        self._save_to_file(self.arrangement_review_file, jobs)

    def _save_to_file(self, target: Path, jobs: list[CareerOpportunity]):
        data = []

        for job in jobs:

            if hasattr(job, "__dict__"):

                record = {}

                for key, value in job.__dict__.items():

                    try:

                        json.dumps(value)

                        record[key] = value

                    except Exception:

                        record[key] = str(value)

                data.append(record)

            else:

                data.append(job)

        with open(

            target,

            "w",

            encoding="utf-8"

        ) as f:

            json.dump(

                data,

                f,

                indent=2,

                ensure_ascii=False,

                default=str

            )

    # ==========================================================
    # Load Jobs
    # ==========================================================

    def load_jobs(self):

        return self._load_from_file(self.jobs_file)

    def load_arrangement_review_jobs(self):
        return self._load_from_file(self.arrangement_review_file)

    def _load_from_file(self, target: Path):

        if not target.exists():

            return []

        with open(

            target,

            "r",

            encoding="utf-8"

        ) as f:

            data = json.load(f)

        jobs = []

        for item in data:

            jobs.append(

                CareerOpportunity(

                    **item

                )

            )

        return jobs

    def merge_refreshed_jobs(self, refreshed_jobs, refreshed_sources, deduplicate, refreshed_scopes=None):
        """Merge source refreshes; optionally replace only successful source/markets."""
        refreshed_source_names = {source.lower() for source in refreshed_sources}
        use_scopes = refreshed_scopes is not None
        refreshed_scopes = {
            (source.lower(), market.lower()) for source, market in (refreshed_scopes or ())
        }
        retained = [
            job
            for job in self.load_jobs()
            if (
                (job.source.lower(), (job.market or "").lower()) not in refreshed_scopes
                if use_scopes
                else job.source.lower() not in refreshed_source_names
            )
        ]
        merged = deduplicate([*retained, *refreshed_jobs])
        self.save_jobs(merged)
        return merged

    # ==========================================================
    # Cache Exists
    # ==========================================================

    def exists(self):

        return self.jobs_file.exists()

    # ==========================================================
    # Clear Cache
    # ==========================================================

    def clear(self):

        if self.jobs_file.exists():

            self.jobs_file.unlink()
