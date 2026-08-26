from urllib.parse import quote

from app.services.job_search_config import LINKEDIN_FRESHNESS_SECONDS, linkedin_searches


class LinkedInURLBuilder:

    def load_searches(self):
        return linkedin_searches()

    def build_urls(self, searches=None):

        urls = []

        for search in searches or self.load_searches():

            keyword = quote(search["keyword"])
            geo = search["market"].linkedin_geo_id

            url = (
                "https://www.linkedin.com/jobs/search/"
                f"?keywords={keyword}"
                f"&geoId={geo}"
                "&f_WT=2"
                f"&f_TPR={LINKEDIN_FRESHNESS_SECONDS}"
            )

            urls.append({

                "name": f"{search['market'].label} {search['family'].label}",

                "url": url,
                "market": search["market"].key,
                "family": search["family"].key,

            })

        return urls
