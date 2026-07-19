from app.services.profile_intelligence.profile_cache import (
    ProfileCache,
)


class ProfileMatcher:
    """
    High-level interface to the intelligent candidate profile.

    This class hides the implementation details of the
    profile intelligence layer.

    All future modules should use this class instead of
    reading master_candidate_profile.json directly.
    """

    def __init__(self):

        self.profile = ProfileCache().load()

    # ==========================================================
    # Explicit Capabilities
    # ==========================================================

    def explicit_capabilities(self):

        return self.profile.get(

            "explicit_capabilities",

            []

        )

    # ==========================================================
    # Capability Records
    # ==========================================================

    def capability_records(self):

        return self.profile.get(

            "capability_records",

            []

        )

    # ==========================================================
    # Capability Exists
    # ==========================================================

    def has_capability(

        self,

        capability,

        minimum_confidence=70

    ):

        capability = capability.lower().strip()

        for record in self.capability_records():

            if (

                record["capability"].lower()

                == capability

                and

                record["confidence"]

                >= minimum_confidence

            ):

                return True

        return False

    # ==========================================================
    # Capability Confidence
    # ==========================================================

    def confidence(

        self,

        capability

    ):

        capability = capability.lower().strip()

        for record in self.capability_records():

            if (

                record["capability"].lower()

                == capability

            ):

                return record["confidence"]

        return 0

    # ==========================================================
    # Matching Capabilities
    # ==========================================================

    def matching_capabilities(

        self,

        requested,

        minimum_confidence=70

    ):

        matched = []

        for capability in requested:

            if self.has_capability(

                capability,

                minimum_confidence

            ):

                matched.append(

                    {

                        "capability": capability,

                        "confidence": self.confidence(

                            capability

                        )

                    }

                )

        matched.sort(

            key=lambda x: (

                -x["confidence"],

                x["capability"]

            )

        )

        return matched

    # ==========================================================
    # Missing Capabilities
    # ==========================================================

    def missing_capabilities(

        self,

        requested,

        minimum_confidence=70

    ):

        missing = []

        for capability in requested:

            if not self.has_capability(

                capability,

                minimum_confidence

            ):

                missing.append(

                    capability

                )

        return sorted(

            list(set(missing))

        )

    # ==========================================================
    # Statistics
    # ==========================================================

    def statistics(self):

        stats = self.profile.get(

            "statistics",

            {}

        )

        return {

            "explicit": stats.get(

                "explicit_count",

                0

            ),

            "intelligence": stats.get(

                "inferred_count",

                0

            )

        }