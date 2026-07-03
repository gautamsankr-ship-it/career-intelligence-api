import json


class ProfileService:

    def __init__(self):

        with open(
            "app/data/master_candidate_profile.json",
            "r",
            encoding="utf-8"
        ) as f:

            self.profile = json.load(f)

    def get_profile(self):

        return self.profile

    def get_candidate(self):

        return self.profile["candidate"]

    def get_summary(self):

        return self.profile["professional_summary"]

    def get_skills(self):

        skills = []

        for category in self.profile["skills"].values():

            skills.extend(category)

        return sorted(set(skills))

    def get_technology(self):

        tech = []

        for category in self.profile["technology"].values():

            tech.extend(category)

        return sorted(set(tech))

    def get_projects(self):

        return self.profile["projects"]

    def get_achievements(self):

        return self.profile["achievements"]

    def get_responsibilities(self):

        responsibilities = []

        for category in self.profile["responsibilities"].values():

            responsibilities.extend(category)

        return responsibilities

    def get_keywords(self):

        keywords = []

        for category in self.profile["ats_keywords"].values():

            keywords.extend(category)

        return sorted(set(keywords))

    def get_all_capabilities(self):

        capabilities = []

        capabilities.extend(self.get_skills())

        capabilities.extend(self.get_technology())

        capabilities.extend(self.get_keywords())

        return sorted(set(capabilities))


# ----------------------------------------------------------
# Backward Compatibility
# ----------------------------------------------------------

def load_candidate_profile():

    return ProfileService().get_profile()