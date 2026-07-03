from app.services.linkedin_url_builder import LinkedInURLBuilder
from app.services.apify_service import ApifyJobService
from app.models.career_opportunity import CareerOpportunity


class JobDiscoveryService:

    def __init__(self):

        self.builder = LinkedInURLBuilder()

        self.scraper = ApifyJobService()

    def discover_jobs(self) -> list[CareerOpportunity]:

        searches = self.builder.build_urls()

        urls = [

            search["url"]

            for search in searches

        ]

        opportunities = self.scraper.scrape_jobs(

            urls,

            count=100

        )

        return self.remove_duplicates(opportunities)

    def remove_duplicates(

        self,

        opportunities: list[CareerOpportunity]

    ) -> list[CareerOpportunity]:

        unique = {}

        for opportunity in opportunities:

            key = (

                opportunity.company.lower().strip(),

                opportunity.job_title.lower().strip(),

                opportunity.location.lower().strip()

            )

            if key not in unique:

                unique[key] = opportunity

        return list(unique.values())