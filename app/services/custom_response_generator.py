"""Generate a grounded, word-limited response to an employer-specific written
prompt (e.g. "Tell us what you can bring to X and why you'd be a great
fit"). Deterministic template composition over verified profile evidence,
ranked by vacancy relevance -- no vacancy/employer name is ever hardcoded
here, and nothing is asserted that isn't traceable to the stored profile.

Task 21.12 section 16: when the employer's word allowance is generous
(>=150 words) and there is enough verified, vacancy-relevant evidence, the
response should normally use more of that allowance -- a second relevant
detail sentence for the strongest-matching role, plus 1-2 relevant
achievements -- rather than always stopping at a minimal identity + one
generic sentence + closing. This is a quality target, not a padding rule:
thin evidence still produces a short, honest response.

Word-limit enforcement drops whole, lower-priority sentences from the
candidate list rather than truncating mid-sentence, so the output is always
grammatically complete.
"""

from __future__ import annotations

import re

from app.services.candidate_evidence_service import get_enriched_profile
from app.services.resume_relevance import (
    DEFAULT_EMPLOYMENT_FIELDS,
    build_professional_summary_sentence,
    humanize_responsibilities,
    select_top,
)


_HAS_DIGIT = re.compile(r"\d")

# Employer word allowances at or above this are treated as "generous enough"
# to normally warrant a substantive response rather than a minimal one.
_RICH_RESPONSE_WORD_THRESHOLD = 150


def _word_count(text: str) -> int:
    return len(text.split())


def _first_person_identity_sentence(profile: dict, vacancy_keywords: set[str]) -> str | None:
    """A natural, first-person opening ("I am a Chartered Accountant with
    over 15 years of experience in ...") built from the same generic,
    vacancy-trimmed identity sentence used on the resume -- never a raw
    profile headline dumped and mangled with ad-hoc casing/plus-sign fixes."""
    sentence = build_professional_summary_sentence(profile, vacancy_keywords)
    if not sentence:
        return None
    article = "an" if sentence[:1].lower() in "aeiou" else "a"
    return f"I am {article} {sentence}"


def _rank_by_relevance(sentences: list[str], vacancy_keywords: set[str]) -> list[str]:
    if not vacancy_keywords:
        return sentences

    def score(sentence: str) -> int:
        lowered = sentence.lower()
        return sum(1 for keyword in vacancy_keywords if keyword.lower() in lowered)

    # Stable sort: equal-scoring sentences keep their original relative order.
    return sorted(sentences, key=score, reverse=True)


def _evidence_sentences_for_entry(entry: dict, vacancy_keywords: set[str], max_count: int) -> list[str]:
    """The lead (role/company context) sentence, plus -- when max_count > 1
    and enough detail exists -- the single most vacancy-relevant additional
    sentence already present in the entry's (evidence-library-enriched)
    responsibilities. Never invents a sentence that isn't already there."""
    company = entry.get("company") or ""
    position = entry.get("position") or entry.get("title") or ""
    responsibilities = entry.get("responsibilities") or []
    humanized = humanize_responsibilities(list(responsibilities), company, position)

    if not humanized:
        summary = entry.get("summary")
        if summary and company:
            return [f"{summary} ({position + ' at ' if position else ''}{company})."]
        return []

    sentences = [humanized[0]]
    if max_count > 1 and len(humanized) > 1:
        ranked_details = _rank_by_relevance(humanized[1:], vacancy_keywords)
        sentences.extend(ranked_details[: max_count - 1])
    return sentences


def _achievement_sentences_for_entry(entry: dict, max_count: int) -> list[str]:
    """Verified achievements for this entry, quantified ones first (Task
    21.12 section 3: quantified achievements generally outrank generic
    ones), capped to max_count and never fabricated -- sourced entirely from
    the entry's own (evidence-library-enriched) achievements list."""
    achievements = [a for a in (entry.get("achievements") or []) if a]
    if not achievements:
        return []
    quantified = [a for a in achievements if _HAS_DIGIT.search(a)]
    others = [a for a in achievements if a not in quantified]
    ordered = quantified + others
    return ordered[:max_count]


class CustomResponseGenerator:
    """Composes a <=max_words response to an employer's specific written
    prompt from verified, vacancy-ranked candidate evidence only."""

    def generate(
        self,
        profile: dict,
        max_words: int,
        employer_name: str,
        vacancy_keywords: set | None = None,
        max_evidence_entries: int = 2,
    ) -> str:
        vacancy_keywords = vacancy_keywords or set()
        profile = get_enriched_profile(profile)
        employment_history = profile.get("employment_history") or []
        ranked = select_top(
            employment_history, vacancy_keywords, DEFAULT_EMPLOYMENT_FIELDS, max_evidence_entries
        )

        identity = _first_person_identity_sentence(profile, vacancy_keywords)

        rich = max_words >= _RICH_RESPONSE_WORD_THRESHOLD

        evidence_sentences: list[str] = []
        for index, entry in enumerate(ranked):
            count = 2 if (rich and index == 0) else 1
            evidence_sentences.extend(_evidence_sentences_for_entry(entry, vacancy_keywords, count))

        achievement_sentences: list[str] = []
        if rich:
            budget = 2
            for entry in ranked:
                if budget <= 0:
                    break
                picked = _achievement_sentences_for_entry(entry, budget)
                achievement_sentences.extend(picked)
                budget -= len(picked)

        closing = (
            f"I would welcome the opportunity to bring this experience to {employer_name} "
            "and contribute to your team." if employer_name else
            "I would welcome the opportunity to bring this experience to your team."
        )

        # Priority order (most essential first, since the trim loop below
        # drops from the tail first): identity, the single most relevant
        # evidence sentence, any additional evidence/achievement sentences
        # (dropped first if over budget), then the closing -- kept as long
        # as possible, since it's what actually answers "why you'd fit".
        optional = evidence_sentences[1:] + achievement_sentences
        ordered = [s for s in (identity, evidence_sentences[0] if evidence_sentences else None) if s]
        ordered += optional
        if closing:
            ordered.append(closing)

        while _word_count(" ".join(ordered)) > max_words and len(ordered) > 1:
            # Drop the least-essential sentence that isn't the closing or
            # the identity sentence, if any such sentence remains.
            drop_index = None
            for index in range(len(ordered) - 1, -1, -1):
                if ordered[index] not in (identity, closing):
                    drop_index = index
                    break
            if drop_index is None:
                drop_index = len(ordered) - 1  # last resort: drop the closing
            ordered.pop(drop_index)

        response = " ".join(ordered)

        # Extremely tight limits (well below what any real vacancy asks for)
        # may still leave a single sentence over budget; never truncate
        # mid-sentence -- return the shortest complete sentence available.
        if _word_count(response) > max_words and ordered:
            response = ordered[0]

        return response
