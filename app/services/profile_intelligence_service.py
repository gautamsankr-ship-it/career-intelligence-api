from app.services.profile_service import ProfileService


class ProfileIntelligenceService:

    def __init__(self):

        self.profile = ProfileService()

    def get_candidate(self):

        return self.profile.get_candidate()

    def get_all_skills(self):

        skills = []

        skills.extend(self.profile.get_skills())

        skills.extend(self.profile.get_technology())

        skills.extend(self.profile.get_keywords())

        return list(set([s.lower() for s in skills]))

    def get_projects(self):

        return self.profile.get_projects()

    def get_responsibilities(self):

        return self.profile.get_responsibilities()

    def get_achievements(self):

        return self.profile.get_achievements()

    def get_capabilities(self):

        return self.profile.get_all_capabilities()

    def has_skill(self, skill):

        return skill.lower() in self.get_all_skills()

    def search_projects(self, keyword):

        keyword = keyword.lower()

        matches = []

        for project in self.get_projects():

            text = " ".join([

                project.get("name", ""),

                project.get("description", ""),

                " ".join(project.get("technologies", [])),

                " ".join(project.get("skills", []))

            ]).lower()

            if keyword in text:

                matches.append(project)

        return matches

    def search_responsibilities(self, keyword):

        keyword = keyword.lower()

        return [

            r

            for r in self.get_responsibilities()

            if keyword in r.lower()

        ]

    def search_achievements(self, keyword):

        keyword = keyword.lower()

        return [

            a

            for a in self.get_achievements()

            if keyword in a.lower()

        ]