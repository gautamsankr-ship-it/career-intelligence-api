import json
from urllib.parse import quote


class LinkedInURLBuilder:

    def load_searches(self):

        with open(
            "app/data/job_searches.json",
            encoding="utf-8"
        ) as f:

            return json.load(f)["searches"]

    def build_urls(self):

        urls = []

        for search in self.load_searches():

            keyword = quote(search["keyword"])

            geo = search["geoId"]

            url = (
                "https://www.linkedin.com/jobs/search/"
                f"?keywords={keyword}"
                f"&geoId={geo}"
                "&f_WT=2"
                "&f_TPR=r86400"
            )

            urls.append({

                "name": search["name"],

                "url": url

            })

        return urls