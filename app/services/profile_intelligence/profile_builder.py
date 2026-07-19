import json
from pathlib import Path

from app.services.profile_intelligence.capability_inference import (
    CapabilityInference,
)


class ProfileBuilder:
    """
    Builds an intelligent candidate profile.

    Input
        master_candidate_profile.json

    Output
        candidate_profile_intelligence.json
    """

    def __init__(self):

        self.inference = CapabilityInference()

        self.master_profile = Path(
            "app/data/master_candidate_profile.json"
        )

        self.output_profile = Path(
            "app/data/candidate_profile_intelligence.json"
        )

    # ==========================================================
    # Load Master Profile
    # ==========================================================

    def load_profile(self):

        with open(

            self.master_profile,

            "r",

            encoding="utf-8"

        ) as f:

            return json.load(f)

    # ==========================================================
    # Collect Explicit Capabilities
    # ==========================================================

    def collect_capabilities(self, profile):

        capabilities = []

        # ------------------------------------------------------
        # Skills
        # ------------------------------------------------------

        for group in profile.get("skills", {}).values():

            capabilities.extend(group)

        # ------------------------------------------------------
        # Technology
        # ------------------------------------------------------

        for group in profile.get("technology", {}).values():

            capabilities.extend(group)

        # ------------------------------------------------------
        # Experience
        # ------------------------------------------------------

        experience = profile.get("experience", {})

        capabilities.extend(

            experience.get("finance_roles", [])

        )

        capabilities.extend(

            experience.get("leadership", [])

        )

        capabilities.extend(

            experience.get("industries", [])

        )

        # ------------------------------------------------------
        # Responsibilities
        # ------------------------------------------------------

        for group in profile.get(

            "responsibilities",

            {}

        ).values():

            capabilities.extend(group)

        # ------------------------------------------------------
        # Projects
        # ------------------------------------------------------

        for project in profile.get(

            "projects",

            []

        ):

            capabilities.append(

                project.get("name", "")

            )

            capabilities.append(

                project.get("category", "")

            )

            capabilities.extend(

                project.get("skills", [])

            )

            capabilities.extend(

                project.get("technologies", [])

            )

            capabilities.extend(

                project.get(

                    "business_domains",

                    []

                )

            )

        # ------------------------------------------------------
        # Education
        # ------------------------------------------------------

        for edu in profile.get(

            "education",

            []

        ):

            capabilities.append(

                edu.get(

                    "qualification",

                    ""

                )

            )

        # ------------------------------------------------------
        # Remove blanks and duplicates
        # ------------------------------------------------------

        cleaned = []

        for item in capabilities:

            if item and item.strip():

                cleaned.append(

                    item.strip()

                )

        return sorted(

            list(set(cleaned))

        )

    # ==========================================================
    # Build Intelligence Profile
    # ==========================================================

    def build(self):

        profile = self.load_profile()

        explicit = self.collect_capabilities(

            profile

        )

        inferred = self.inference.build_records(

            explicit

        )

        intelligence = {

            "candidate": profile.get(

                "personal_information",

                {}

            ),

            "explicit_capabilities": explicit,

            "capability_records": inferred,

            "statistics": {

                "explicit_count": len(explicit),

                "inferred_count": len(inferred)

            }

        }

        with open(

            self.output_profile,

            "w",

            encoding="utf-8"

        ) as f:

            json.dump(

                intelligence,

                f,

                indent=4,

                ensure_ascii=False

            )

        print()

        print("=" * 70)

        print("PROFILE INTELLIGENCE BUILT")

        print("=" * 70)

        print(

            f"Explicit Capabilities : {len(explicit)}"

        )

        print(

            f"Knowledge Graph Nodes : {len(inferred)}"

        )

        print(

            f"Saved : {self.output_profile}"

        )

        print("=" * 70)

        print()

        return intelligence