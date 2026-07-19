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

    # ==========================================================
    # Save Jobs
    # ==========================================================

    def save_jobs(

        self,

        jobs: list[CareerOpportunity]

    ):

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

            self.jobs_file,

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

        if not self.jobs_file.exists():

            return []

        with open(

            self.jobs_file,

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