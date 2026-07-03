import json


class MasterProfileService:

    def load(self):

        with open(
            "app/data/master_candidate_profile.json",
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)