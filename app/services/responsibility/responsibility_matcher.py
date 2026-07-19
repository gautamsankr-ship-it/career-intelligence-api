from app.services.evidence_engine import EvidenceEngine
from app.services.responsibility.capability_extractor import CapabilityExtractor
from app.services.responsibility.capability_normalizer import CapabilityNormalizer


class ResponsibilityMatcher:
    """
    Matches job responsibilities against candidate evidence.
    """

    def __init__(self):

        self.extractor = CapabilityExtractor()

        self.normalizer = CapabilityNormalizer()

        self.evidence = EvidenceEngine()

    # ==========================================================
    # Match One Responsibility
    # ==========================================================

    def match(self, responsibility):

        capabilities = self.extractor.extract(

            responsibility

        )

        capabilities = self.normalizer.normalize_all(

            capabilities

        )

        matched = []

        missing = []

        total_score = 0

        total_confidence = 0

        for capability in capabilities:

            result = self.evidence.evidence_score(

                capability

            )

            if result["score"] >= 6:

                matched.append({

                    "requested": capability,

                    "matched": result["matched"],

                    "score": result["score"],

                    "confidence": result["confidence"],

                    "weight": result.get("weight", 0)

                })

                total_score += result["score"]

                total_confidence += result["confidence"]

            else:

                missing.append(capability)

        if capabilities:

            normalized_score = total_score / (len(capabilities) * 10)

            confidence = total_confidence / len(capabilities)

        else:

            normalized_score = 0

            confidence = 0

        return {

            "responsibility": responsibility,

            "capabilities": capabilities,

            "matched": matched,

            "missing": missing,

            "normalized_score": normalized_score,

            "confidence": round(confidence, 1)

        }

    # ==========================================================
    # Match All Responsibilities
    # ==========================================================

    def match_all(self, responsibilities):

        results = []

        overall = 0

        confidence = 0

        matched = []

        missing = []

        capability_count = 0

        for responsibility in responsibilities:

            result = self.match(

                responsibility

            )

            results.append(result)

            overall += result["normalized_score"]

            confidence += result["confidence"]

            capability_count += len(

                result["capabilities"]

            )

            for item in result["matched"]:

                matched.append(

                    item["matched"]

                )

            missing.extend(

                result["missing"]

            )

        if responsibilities:

            overall /= len(responsibilities)

            confidence /= len(responsibilities)

        return {

            "score": round(overall, 3),

            "confidence": round(confidence, 1),

            "matched": sorted(

                list(set(matched))

            ),

            "missing": sorted(

                list(set(missing))

            ),

            "capabilities": capability_count,

            "results": results

        }