import json
import re
from difflib import SequenceMatcher
from app.services.profile_intelligence import ProfileMatcher


class EvidenceEngine:

    def __init__(self):

        with open(
            "app/data/master_candidate_profile.json",
            "r",
            encoding="utf-8"
        ) as f:

            self.profile = json.load(f)

        with open(
            "app/data/knowledge_base.json",
            "r",
            encoding="utf-8"
        ) as f:

            self.knowledge = json.load(f)

        # ------------------------------------------------------
        # Explicit evidence
        # ------------------------------------------------------

        self.candidate_terms = self._build_candidate_terms()

        # ------------------------------------------------------
        # Intelligent profile
        # ------------------------------------------------------

        self.profile_matcher = ProfileMatcher()
        print(f"Evidence Engine Loaded : {len(self.candidate_terms)} candidate terms")

    # =====================================================
    # Candidate Knowledge
    # =====================================================

    # Task 21.15J: bounded, deterministic clause splitter for long
    # multi-clause prose fields ONLY (professional_summary.headline/
    # career_direction/value_proposition) -- these are the sole "long
    # narrative" fields in the profile that pack several distinct facts into
    # one sentence (e.g. "...accounting, taxation, audit, ..., Australian
    # public practice, ..."). Every other profile field (skills, technology,
    # experience.*, responsibilities.*, education, project fields) is
    # already a short label or a single-fact-per-item list and is
    # deliberately left untouched. Splitting only on comma/semicolon/" and "/
    # " & " -- structural list-joiners, not arbitrary substring generation --
    # keeps every derived segment an exact, traceable substring of the
    # original verified sentence; no new fact is invented.
    _CLAUSE_SPLIT_PATTERN = re.compile(r",|;| and | & ")

    def _segment_long_text(self, text):
        segments = []

        for raw in self._CLAUSE_SPLIT_PATTERN.split(text):

            clause = raw.strip().strip(".").strip()

            # Bare single- or two-word clauses (e.g. "taxation", "Data
            # Analytics") are dropped. Two words is still short enough for a
            # single shared generic word (e.g. "analytics") to dominate the
            # word-overlap ratio against an unrelated specialized query
            # (e.g. "blockchain analytics") -- confirmed empirically during
            # Task 21.15J's false-positive testing, where a 2-word "Data
            # Analytics" segment gave "blockchain analytics" partial credit
            # it should not have. Requiring 3+ words keeps the target fix
            # ("Australian public practice") while removing that risk, and
            # further limits false-positive surface area generally.
            if clause and len(clause.split()) >= 3:

                segments.append(clause)

        return segments

    def _build_candidate_terms(self):

        evidence = {}

        # Task 21.15J: provenance for every long-text-derived segment --
        # keyed by the segment's own normalized form, so evidence_score()
        # can report exactly which source field/sentence a match came from.
        # Short atomic terms (the overwhelming majority) carry no entry here
        # and are unaffected.
        self._term_provenance = {}

        def add(term, weight):

            if not term:
                return

            term = self._normalize(term)

            if not term:
                return

            evidence[term] = evidence.get(term, 0) + weight

        def add_long_text(text, weight, source_field):
            """Adds the field's full original text as a candidate term
            (unchanged existing behaviour), PLUS its clause-level segments
            as additional, separately retrievable candidate terms. Purely
            additive -- the original atomic term is never removed."""

            add(text, weight)

            if not text:
                return

            for segment in self._segment_long_text(text):

                add(segment, weight)

                normalized_segment = self._normalize(segment)

                if normalized_segment:

                    self._term_provenance[normalized_segment] = {
                        "source_field": source_field,
                        "source_text": text,
                        "derived_segment": segment,
                    }

        # -------------------------------------------------
        # Skills
        # -------------------------------------------------

        for group in self.profile.get("skills", {}).values():

            for item in group:

                add(item, 10)

        # -------------------------------------------------
        # Technology
        # -------------------------------------------------

        for group in self.profile.get("technology", {}).values():

            for item in group:

                add(item, 8)

        # -------------------------------------------------
        # Experience
        # -------------------------------------------------

        experience = self.profile.get("experience", {})

        for item in experience.get("finance_roles", []):

            add(item, 9)

        for item in experience.get("leadership", []):

            add(item, 8)

        for item in experience.get("industries", []):

            add(item, 7)

        # -------------------------------------------------
        # Projects
        # -------------------------------------------------

        for project in self.profile.get("projects", []):

            add(project.get("name", ""), 9)

            add(project.get("category", ""), 8)

            description = project.get("description", "")

            for sentence in description.split("."):

                add(sentence, 6)
            
            add(project.get("status", ""), 2)

            for item in project.get("skills", []):

                add(item, 9)

            for item in project.get("technologies", []):

                add(item, 8)

            for item in project.get("business_domains", []):

                add(item, 8)

        # -------------------------------------------------
        # Responsibilities
        # -------------------------------------------------

        for group in self.profile.get("responsibilities", {}).values():

            for item in group:

                add(item, 10)

        # -------------------------------------------------
        # Education
        # -------------------------------------------------

        for edu in self.profile.get("education", []):

            add(edu.get("qualification", ""), 6)

        # -------------------------------------------------
        # Professional Summary
        # -------------------------------------------------

        summary = self.profile.get("professional_summary", {})

        add_long_text(summary.get("headline", ""), 5, "professional_summary.headline")

        add_long_text(summary.get("career_direction", ""), 4, "professional_summary.career_direction")

        add_long_text(summary.get("value_proposition", ""), 4, "professional_summary.value_proposition")

        return evidence

    # =====================================================
    # Normalization
    # =====================================================

    def _normalize(self, text):

        text = text.lower()

        text = re.sub(
            r"[^a-z0-9 ]",
            " ",
            text
        )

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        return text.strip()

    # =====================================================
    # Synonyms
    # =====================================================

    def _expand(self, keyword):
        keyword = self._normalize(keyword)

        expanded = {keyword}

        # ----------------------------------------------------------
        # Built-in business abbreviations
        # ----------------------------------------------------------

        abbreviations = {
            "fp a": [
                "financial planning",
                "financial planning analysis",
                "forecasting",
                "budgeting",
                "variance analysis",
            ],
            "fpa": [
                "financial planning",
                "forecasting",
                "budgeting",
                "variance analysis",
            ],
            "dda": [
                "due diligence",
                "financial due diligence",
                "commercial due diligence",
            ],
            "m a": [
                "mergers acquisitions",
                "deal advisory",
                "transaction advisory",
            ],
            "gl": ["general ledger"],
            "ap": ["accounts payable"],
            "ar": ["accounts receivable"],
            "bi": ["business intelligence", "power bi"],
            "erp": [
                "sap",
                "oracle erp",
                "netsuite",
                "odoo",
                "enterprise resource planning",
            ],
            "ai": ["artificial intelligence", "machine learning", "automation"],
        }

        if keyword in abbreviations:
            for item in abbreviations[keyword]:
                expanded.add(self._normalize(item))

        # ----------------------------------------------------------
        # Knowledge base synonyms
        # ----------------------------------------------------------

        synonyms = self.knowledge.get("skill_synonyms", {})

        if keyword in synonyms:
            for item in synonyms[keyword]:
                expanded.add(self._normalize(item))

        return expanded

    # =====================================================
    # Similarity
    # =====================================================

    def _similarity(self, a, b):

        if a == b:

            return 1.0

        if f" {a} " in f" {b} ":
            return 0.95

        if f" {b} " in f" {a} ":
            return 0.95

        a_words = set(a.split())
        b_words = set(b.split())

        overlap = 0.0
        if a_words and b_words:
            overlap = len(a_words & b_words) / len(a_words | b_words)

            if overlap >= 0.75:
                return max(overlap, 0.90)

            if overlap >= 0.50:
                return max(overlap, 0.80)

        return SequenceMatcher(None, a, b).ratio()

    # =====================================================
    # Evidence Score
    # =====================================================

        
    def evidence_score(self, keyword):

        search_terms = self._expand(keyword)

        best_similarity = 0.0
        best_match = None
        best_weight = 0

        for search in search_terms:

            # --------------------------------------------------
            # Intelligent profile lookup
            # --------------------------------------------------

            if self.profile_matcher.has_capability(

                search,

                minimum_confidence=80

            ):

                confidence = self.profile_matcher.confidence(

                    search

                )

                if confidence > best_similarity * 100:

                    best_similarity = confidence / 100

                    best_match = search

                    best_weight = 30

            # --------------------------------------------------
            # Explicit evidence lookup
            # --------------------------------------------------

            for candidate, weight in self.candidate_terms.items():

                similarity = self._similarity(

                    search,

                    candidate

                )

                if similarity > best_similarity:

                    best_similarity = similarity

                    best_match = candidate

                    best_weight = weight

        # Final weighted score
        if best_similarity >= 0.95:
            score = 10
        elif best_similarity >= 0.90:
            score = 9
        elif best_similarity >= 0.80:
            score = 8
        elif best_similarity >= 0.70:
            score = 6
        elif best_similarity >= 0.60:
            score = 4
        else:
            score = 0

        # Boost score if we have strong evidence
        if score > 0:
            if best_weight >= 30:
                score = min(10, score + 2)
            elif best_weight >= 20:
                score = min(10, score + 1)

        return {
            "score": score,
            "confidence": round(best_similarity * 100, 1),
            "matched": best_match,
            "weight": best_weight,
            # Task 21.15J: present only when `matched` is a long-text-derived
            # segment (e.g. professional_summary.headline); None for every
            # pre-existing short atomic term.
            "provenance": self._term_provenance.get(best_match),
        }