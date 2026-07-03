import os
from typing import List

from dotenv import load_dotenv
from apify_client import ApifyClient

from app.models.career_opportunity import CareerOpportunity

load_dotenv()


class ApifyJobService:

    def __init__(self):

        self.client = ApifyClient(
            os.getenv("APIFY_TOKEN")
        )

        # LinkedIn Jobs Scraper Actor
        self.actor_id = "hKByXkMQaC5Qt9UMN"

    def scrape_jobs(
        self,
        linkedin_urls: List[str],
        count: int = 100,
    ) -> List[CareerOpportunity]:

        run_input = {

            "count": count,

            "scrapeCompany": True,

            "splitByLocation": False,

            "urls": linkedin_urls,

        }

        run = self.client.actor(
            self.actor_id
        ).call(
            run_input=run_input
        )

        dataset_id = run.default_dataset_id

        opportunities: List[CareerOpportunity] = []

        dataset = self.client.dataset(dataset_id)

        for item in dataset.iterate_items():

            try:

                opportunities.append(

                    CareerOpportunity.from_apify(item)

                )

            except Exception as ex:

                print(
                    f"Failed to map job: {ex}"
                )

        print()
        print("=" * 80)
        print(f"DISCOVERED JOBS : {len(opportunities)}")
        print("=" * 80)

        return opportunities