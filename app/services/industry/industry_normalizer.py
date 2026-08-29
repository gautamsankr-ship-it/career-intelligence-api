import re
from difflib import SequenceMatcher

from app.services.industry.capability_dictionary import CAPABILITY_FAMILIES


class IndustryNormalizer:
    """
    Converts capabilities into industry capability families.

    Matching Strategy

    1. Business abbreviation expansion
    2. Exact lookup
    3. Substring lookup
    4. Token overlap
    5. Fuzzy similarity
    """

    def __init__(self):

        self.lookup = {}

        # --------------------------------------------------
        # Reverse lookup
        # --------------------------------------------------

        for family, capabilities in CAPABILITY_FAMILIES.items():

            self.lookup[family.lower()] = family

            for capability in capabilities:

                self.lookup[
                    capability.lower()
                ] = family

    # =====================================================
    # Normalize One Capability
    # =====================================================

    def normalize(self, capability):

        if not capability:
            return ""

        capability = capability.strip().lower()

        # --------------------------------------------------
        # Exact Match (before alias expansion)
        # --------------------------------------------------

        # Task 21.15I: alias expansion below is a destructive rewrite (e.g.
        # "m&a" -> "mergers and acquisitions") that can destroy an
        # already-exact match. "M&A Integration" is a literal, correctly
        # mapped CAPABILITY_FAMILIES item (self.lookup["m&a integration"] ==
        # "Corporate Finance"), but alias substitution used to rewrite it to
        # "mergers and acquisitions integration" *before* the exact-lookup
        # check ever ran, so it fell through to the unmatchable orphan
        # fallback below instead. Checking the raw (pre-alias) string first
        # preserves every dictionary entry that already spells out its own
        # abbreviation exactly as written (M&A Integration, FP&A, ERP, ...).
        if capability in self.lookup:

            return self.lookup[capability]

        # --------------------------------------------------
        # Business Abbreviations / Aliases
        # --------------------------------------------------

        aliases = {

            "fp&a": "financial planning and analysis",
            "fpa": "financial planning and analysis",

            "m&a": "mergers and acquisitions",

            "gl": "general ledger",
            "ap": "accounts payable",
            "ar": "accounts receivable",

            "bi": "business intelligence",

            "erp": "enterprise resource planning",

            "ai": "artificial intelligence",
            "ml": "machine learning",

            "etl": "extract transform load",

            "kpi": "key performance indicator",
            "okr": "objectives and key results",

            "p&l": "profit and loss",

            "bs": "balance sheet",

            "cf": "cash flow",

            "capex": "capital expenditure",
            "opex": "operating expenditure",

            "wc": "working capital",

            "coa": "chart of accounts",

            "far": "fixed asset register",

            "brs": "bank reconciliation",

            "dda": "financial due diligence",
            "fdd": "financial due diligence",
            "cdd": "commercial due diligence",

            "powerbi": "power bi",
            "powerquery": "power query",
            "powerautomate": "power automate",

            "advanced excel": "excel",
            "ms excel": "excel",

            "financial modelling": "financial modeling",

        }

        # Task 21.15E: word-boundary-safe replacement -- a naive substring
        # .replace() corrupted any longer word that merely CONTAINS a short
        # alias, e.g. "ai" inside "detail" ("attention to detail" ->
        # "attention to detartificial intelligencel"), "ar" inside "GAAP"
        # ("GAAP" -> "gaaccounts payable"), "market research", "variance
        # analysis", "board reporting", "capital markets" and 140+ other
        # real requirement phrases across the frozen benchmark -- silently
        # destroying capabilities that would otherwise have matched a known
        # family. \b anchors each alias to a whole token/phrase boundary.
        for short, full in aliases.items():

            pattern = r"\b" + re.escape(short) + r"\b"

            capability = re.sub(
                pattern,
                full,
                capability,
            )

        # --------------------------------------------------
        # Exact Match (after alias expansion)
        # --------------------------------------------------

        if capability in self.lookup:

            return self.lookup[capability]

        # --------------------------------------------------
        # Clean punctuation
        # --------------------------------------------------

        cleaned = (
            capability
            .replace("&", "and")
            .replace("-", " ")
            .replace("/", " ")
        )

        # --------------------------------------------------
        # Substring Matching
        # --------------------------------------------------

        for known, family in self.lookup.items():

            if known in cleaned:

                return family

            if cleaned in known:

                return family

        # --------------------------------------------------
        # Token Overlap
        # --------------------------------------------------

        cleaned_words = set(cleaned.split())

        best_family = None
        best_overlap = 0

        for known, family in self.lookup.items():

            known_words = set(known.split())

            if not known_words:
                continue

            overlap = len(
                cleaned_words & known_words
            ) / len(
                cleaned_words | known_words
            )

            if overlap > best_overlap:

                best_overlap = overlap

                best_family = family

        if best_overlap >= 0.50:

            return best_family

        # --------------------------------------------------
        # Fuzzy Similarity
        # --------------------------------------------------

        best_family = None
        best_similarity = 0

        for known, family in self.lookup.items():

            similarity = SequenceMatcher(

                None,

                cleaned,

                known

            ).ratio()

            if similarity > best_similarity:

                best_similarity = similarity

                best_family = family

        if best_similarity >= 0.75:

            return best_family

        # --------------------------------------------------
        # Unknown Capability
        # --------------------------------------------------

        return cleaned.title()

    # =====================================================
    # Normalize Multiple
    # =====================================================

    def normalize_all(self, capabilities):

        normalized = []

        for capability in capabilities:

            family = self.normalize(capability)

            if family:

                normalized.append(family)

        return sorted(

            list(set(normalized))

        )

    # =====================================================
    # Family
    # =====================================================

    def family(self, capability):

        return self.normalize(capability)

    # =====================================================
    # Expand Family
    # =====================================================

    def expand_family(self, family):

        return CAPABILITY_FAMILIES.get(

            family,

            []

        )

    # =====================================================
    # Same Family
    # =====================================================

    def same_family(

        self,

        capability_a,

        capability_b

    ):

        return (

            self.normalize(capability_a)

            ==

            self.normalize(capability_b)

        )