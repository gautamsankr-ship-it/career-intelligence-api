from typing import List, Dict


class JobScraper:

    """
    Placeholder scraper.

    Today:
        - Accepts jobs from any source.

    Next:
        - Apify
        - LinkedIn
        - Seek
        - Indeed
        - Company career sites
    """

    def normalize(self, jobs: List[Dict]) -> List[Dict]:

        normalized = []

        for job in jobs:

            normalized.append({

                "company": job.get("company", ""),

                "job_title": job.get("job_title", ""),

                "location": job.get("location", ""),

                "employment_type": job.get("employment_type", ""),

                "job_url": job.get("job_url", ""),

                "job_description": job.get("job_description", ""),

                "source": job.get("source", "")

            })

        return normalized