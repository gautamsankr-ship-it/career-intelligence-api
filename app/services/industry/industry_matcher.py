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

        classified_families = set()

        unclassified_capabilities = set()

        total_confidence = 0

        confidence_count = 0

        total_requested = 0

        classified_count = 0

        # ------------------------------------------------------
        # Task 21.15I: classify each requested capability into a genuine
        # CAPABILITY_FAMILIES family, or preserve it as an unclassified/
        # orphan capability. An unrecognized capability must never become a
        # fake, permanently-unmatchable pseudo-family (IndustryNormalizer's
        # own last-resort fallback returns a made-up title-cased name for
        # anything it can't classify) -- doing so silently inflated the
        # coverage denominator below with families that had zero items to
        # test evidence against, structurally capping the achievable score
        # regardless of true domain fit (Task 21.15H root cause).
        # ------------------------------------------------------

        for capability in requested_capabilities:

            total_requested += 1

            family = self.normalizer.normalize(

                capability

            )

            if family in CAPABILITY_FAMILIES:

                classified_families.add(family)

                classified_count += 1

            else:

                unclassified_capabilities.add(capability.strip())

        # ------------------------------------------------------
        # Evaluate each classified family once
        # ------------------------------------------------------

        for family in classified_families:

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

        # Task 21.15I: the denominator now contains ONLY genuine
        # CAPABILITY_FAMILIES families (orphan capabilities were already
        # excluded above) -- a genuinely requested-but-unmatched real family
        # (e.g. Insolvency & Restructuring with no candidate evidence) still
        # counts fully against coverage; only orphan noise is excluded. When
        # NO real family could be identified at all, coverage is 0 (never
        # treated as perfect fit by default).

        if classified_families:

            coverage = (

                len(matched_families)

                /

                len(classified_families)

            )

        else:

            coverage = 0

        unmatched_families = classified_families - matched_families

        # Task 21.15I: classification_coverage guards against reading "every
        # classified family matched" as proof of overall domain fit when a
        # large share of the vacancy's own capability list was never
        # classified into any family at all (e.g. a niche specialty the
        # taxonomy doesn't yet cover) -- see IndustryScorer.score(), which
        # applies this as a confidence-safeguard multiplier on the final
        # score rather than folding it into `coverage` itself, so `coverage`
        # keeps its existing, narrower meaning (fit among classified
        # families only).

        classification_coverage = (

            classified_count / total_requested

        ) if total_requested else 1.0

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

            "classification_coverage": round(

                classification_coverage,

                3

            ),

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

            "unmatched_families": sorted(

                unmatched_families

            ),

            "requested_families": sorted(

                classified_families

            ),

            "unclassified_capabilities": sorted(

                unclassified_capabilities

            )

        }