from difflib import SequenceMatcher

from app.services.profile_intelligence_service import (
    ProfileIntelligenceService,
)


class EvidenceEngine:

    def __init__(self):

        self.profile = ProfileIntelligenceService()

    def _similarity(self, a, b):

        return SequenceMatcher(
            None,
            a.lower(),
            b.lower()
        ).ratio()

    def _search_list(self, keyword, items):

        keyword = keyword.lower()

        matches = []

        for item in items:

            text = str(item).lower()

            if keyword in text:

                matches.append(item)

                continue

            if text in keyword:

                matches.append(item)

                continue

            if self._similarity(keyword, text) >= 0.75:

                matches.append(item)

        return matches

    def search(self, keyword):

        return {

            "skills": self._search_list(

                keyword,

                self.profile.get_all_skills()

            ),

            "projects": self.profile.search_projects(

                keyword

            ),

            "responsibilities":

                self.profile.search_responsibilities(

                    keyword

                ),

            "achievements":

                self.profile.search_achievements(

                    keyword

                )

        }

    def evidence_score(self, keyword):

        evidence = self.search(keyword)

        score = 0

        score += min(len(evidence["skills"]) * 2, 4)

        score += min(len(evidence["projects"]) * 3, 9)

        score += min(len(evidence["responsibilities"]), 4)

        score += min(len(evidence["achievements"]) * 2, 4)

        return {

            "keyword": keyword,

            "score": score,

            "evidence": evidence

        }