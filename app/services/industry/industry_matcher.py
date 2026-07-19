from app.services.evidence_engine import EvidenceEngine
from app.services.industry.capability_dictionary import (
    CAPABILITY_FAMILIES,
)
from app.services.industry.industry_normalizer import (
    IndustryNormalizer,
)


class IndustryMatcher:
    """
    Industry Intelligence V5

    Matches INDUSTRY FAMILIES instead of
    individual keywords.

    Recruiters evaluate capability families,
    not keyword counts.
    """

    def __init__(self):

        self.evidence = EvidenceEngine()

        self.normalizer = IndustryNormalizer()

    # ==========================================================
    # Match Industry Families
    # ==========================================================

    def match_all(self, requested_capabilities):

        matched_capabilities = []

        missing_capabilities = []

        matched_families = set()

        requested_families = set()

        total_confidence = 0

        confidence_count = 0

        # ------------------------------------------------------
        # Determine requested families
        # ------------------------------------------------------

        for capability in requested_capabilities:

            family = self.normalizer.normalize(

                capability

            )

            requested_families.add(

                family

            )

        # ------------------------------------------------------
        # Evaluate each family once
        # ------------------------------------------------------

        for family in requested_families:

            family_items = CAPABILITY_FAMILIES.get(

                family,

                []

            )

            family_matched = False

            best_confidence = 0

            best_match = None

            for capability in family_items:

                evidence = self.evidence.evidence_score(

                    capability

                )

                if evidence["score"] >= 6:

                    family_matched = True

                    matched_families.add(

                        family

                    )

                    if evidence["confidence"] > best_confidence:

                        best_confidence = evidence["confidence"]

                        best_match = evidence["matched"]

            if family_matched:

                if best_match:

                    matched_capabilities.append(

                        best_match

                    )

                total_confidence += best_confidence

                confidence_count += 1

            else:

                missing_capabilities.extend(

                    family_items

                )

        # ------------------------------------------------------
        # Coverage
        # ------------------------------------------------------

        if requested_families:

            coverage = (

                len(matched_families)

                /

                len(requested_families)

            )

        else:

            coverage = 1

        if confidence_count:

            confidence = (

                total_confidence

                /

                confidence_count

            )

        else:

            confidence = 0

        return {

            "coverage": coverage,

            "confidence": round(

                confidence,

                1

            ),

            "matched": sorted(

                list(

                    set(

                        matched_capabilities

                    )

                )

            ),

            "missing": sorted(

                list(

                    set(

                        missing_capabilities

                    )

                )

            ),

            "families": sorted(

                matched_families

            ),

            "requested_families": sorted(

                requested_families

            )

        }