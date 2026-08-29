"""Vacancy-driven relevance ranking for resume content.

Generic, deterministic keyword-overlap scoring -- no vacancy name, employer,
or domain is ever hardcoded here. What ranks highly for one vacancy (e.g.
Australian accounting/Xero/MYOB) is purely a function of that vacancy's own
extracted keywords; a different vacancy (e.g. fintech/automation) would rank
different profile entries highly, using the exact same code path.
"""

from __future__ import annotations

import re


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "of", "on", "or", "our", "that", "the", "to",
    "with", "you", "your", "will", "we", "this",
}
_WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+./-]{1,}")


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    return {
        token.lower()
        for token in _WORD_PATTERN.findall(text)
        if token.lower() not in _STOPWORDS and len(token) > 1
    }


def extract_vacancy_keywords(job_analysis: dict | None, ats_result: dict | None = None) -> set[str]:
    """Pull every explicit vacancy-side signal available from the existing,
    already-produced job_analysis (analyze_job) and ats_result (ATSEngine)
    structures -- no new AI call, no new data source."""
    job_analysis = job_analysis or {}
    ats_result = ats_result or {}
    keywords: set[str] = set()

    phrase_fields = (
        "required_skills", "preferred_skills", "technologies",
        "finance_domains", "keywords", "soft_skills", "responsibilities",
        "education",
    )
    for field in phrase_fields:
        for item in job_analysis.get(field) or []:
            if isinstance(item, str):
                keywords.add(item.lower())
                keywords |= _tokenize(item)

    for text_field in ("job_title", "industry", "department", "location", "summary"):
        value = job_analysis.get(text_field)
        if isinstance(value, str):
            keywords |= _tokenize(value)

    match_reasoning = job_analysis.get("match_reasoning") or {}
    for field in ("must_have_skills", "nice_to_have_skills"):
        for item in match_reasoning.get(field) or []:
            if isinstance(item, str):
                keywords |= _tokenize(item)

    keyword_summary = ats_result.get("keyword_summary") or {}
    for bucket in ("matched", "partial", "missing"):
        for entry in keyword_summary.get(bucket) or []:
            text = entry.get("keyword") if isinstance(entry, dict) else entry
            if isinstance(text, str):
                keywords.add(text.lower())
                keywords |= _tokenize(text)

    return keywords


def _entry_text(entry: dict, text_fields: tuple[str, ...]) -> str:
    parts: list[str] = []
    for field in text_fields:
        value = entry.get(field)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(item for item in value if isinstance(item, str))
    return " ".join(parts)


def score_entry(entry: dict, vacancy_keywords: set[str], text_fields: tuple[str, ...]) -> int:
    """Count of distinct vacancy keywords/tokens that appear in this entry's
    own text (company/role/summary/responsibilities/achievements/etc.)."""
    if not vacancy_keywords:
        return 0
    entry_tokens = _tokenize(_entry_text(entry, text_fields))
    entry_text_lower = _entry_text(entry, text_fields).lower()
    score = 0
    for keyword in vacancy_keywords:
        if " " in keyword:
            if keyword in entry_text_lower:
                score += 1
        elif keyword in entry_tokens:
            score += 1
    return score


DEFAULT_EMPLOYMENT_FIELDS = (
    "company", "position", "title", "country", "summary",
    "responsibilities", "achievements", "technologies", "key_clients",
)
DEFAULT_VENTURE_FIELDS = ("venture", "company", "role", "description", "technologies")
DEFAULT_BOARD_FIELDS = ("organization", "role", "designation", "responsibilities")
DEFAULT_PROJECT_FIELDS = ("name", "category", "description", "technologies", "skills", "business_domains")


def rank_entries(
    entries: list[dict],
    vacancy_keywords: set[str],
    text_fields: tuple[str, ...] = DEFAULT_EMPLOYMENT_FIELDS,
) -> list[dict]:
    """Return entries sorted by vacancy relevance, most relevant first.
    Ties preserve original order (stable sort) so unranked/equal-score
    content doesn't get shuffled for no reason."""
    scored = [(score_entry(entry, vacancy_keywords, text_fields), index, entry) for index, entry in enumerate(entries)]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _score, _index, entry in scored]


# Below this average word count, a profile's "responsibilities" list reads
# as terse keyword phrases (e.g. "Management Accounting", "Leadership")
# rather than sentences, and is combined into a readable bullet instead of
# being rendered as a bare keyword list.
_KEYWORD_STYLE_WORD_THRESHOLD = 6


def _is_keyword_style(items: list) -> bool:
    strings = [item for item in items if isinstance(item, str)]
    if not strings:
        return False
    avg_words = sum(len(item.split()) for item in strings) / len(strings)
    return avg_words <= _KEYWORD_STYLE_WORD_THRESHOLD


# Per-item threshold used to split a MIXED list (e.g. after evidence-library
# enrichment adds full sentences alongside short original keyword phrases)
# into its keyword-style and sentence-style halves. Tighter than the
# whole-list average threshold above, since a single short phrase should not
# get misclassified just because it happens to share a list with long
# sentences.
_KEYWORD_STYLE_ITEM_WORD_THRESHOLD = 4


def _is_keyword_style_item(item: str) -> bool:
    return len(item.split()) <= _KEYWORD_STYLE_ITEM_WORD_THRESHOLD and not item.rstrip().endswith((".", "!", "?"))


def _join_natural(phrases: list[str]) -> str:
    if len(phrases) == 1:
        return phrases[0]
    return ", ".join(phrases[:-1]) + f" and {phrases[-1]}"


def _combine_keyword_phrases(phrases: list[str], company: str, position: str) -> str:
    """Turn a list of terse HR-style category labels (e.g. "Management
    Accounting", "Leadership") into a natural sentence rather than a bare
    field-list ("Led X, Y, Z and Leadership as Position at Company.").
    "Leadership" is treated as team-leadership context rather than another
    domain noun dumped into the same list, since that's what reads oddest."""
    if position and company:
        lead = f"As {position} at {company}"
    elif company:
        lead = f"At {company}"
    elif position:
        lead = f"As {position}"
    else:
        lead = "In this role"

    has_leadership = any(p.strip().lower() == "leadership" for p in phrases)
    domain_phrases = [p for p in phrases if p.strip().lower() != "leadership"]

    if domain_phrases:
        # Kept in their original casing (they read as short competency
        # labels, e.g. "Financial Reporting") rather than force-lowercased,
        # which produced inconsistent-looking results like "management
        # Accounting" when only the very first character was touched.
        joined = _join_natural(domain_phrases)
        core = f"focused on {joined}"
        if has_leadership:
            core += ", with team leadership responsibility"
    elif has_leadership:
        core = "led the team"
    else:
        core = ""

    sentence = f"{lead}, {core}." if core else f"{lead}."
    return sentence


def humanize_responsibilities(responsibilities: list, company: str, position: str) -> list:
    """Turn a terse keyword-style responsibilities list into one readable
    bullet sentence; sentence-style responsibilities (already written as
    prose, e.g. a hand-tailored application or evidence-library enrichment)
    pass through unchanged. A MIXED list (some short keyword phrases, some
    full sentences -- typical once evidence-library facts are merged into an
    originally keyword-only entry) combines just the keyword-style phrases
    into one lead sentence and keeps the rest as-is, rather than either
    dumping every phrase as a bare bullet or refusing to touch the list."""
    if not responsibilities:
        return responsibilities

    strings = [item for item in responsibilities if isinstance(item, str)]
    if not strings:
        return responsibilities

    if _is_keyword_style(responsibilities):
        return [_combine_keyword_phrases(strings, company, position)]

    keyword_phrases = [item for item in strings if _is_keyword_style_item(item)]
    sentence_items = [item for item in strings if not _is_keyword_style_item(item)]
    if not keyword_phrases:
        return responsibilities

    lead_sentence = _combine_keyword_phrases(keyword_phrases, company, position)
    return [lead_sentence] + sentence_items


def flatten_skill_groups(value) -> list[str]:
    """A skills/software value may be a flat list already, or a dict of
    category -> list (e.g. {"accounting": ["Xero", "MYOB"], ...}). Either
    way, return one flat list of items, preserving first-seen order."""
    if isinstance(value, dict):
        items: list[str] = []
        for group in value.values():
            if isinstance(group, list):
                items.extend(item for item in group if isinstance(item, str))
            elif isinstance(group, str):
                items.append(group)
        return items
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def rank_flat_items(items: list[str], vacancy_keywords: set[str]) -> list[str]:
    """Rank a flat list of short skill/software strings by whether the
    vacancy explicitly mentions them; ties preserve original order."""
    def item_score(item: str) -> int:
        lowered = item.lower()
        if lowered in vacancy_keywords:
            return 1
        return 1 if _tokenize(item) & vacancy_keywords else 0

    if not vacancy_keywords:
        return list(items)
    scored = [(-item_score(item), index, item) for index, item in enumerate(items)]
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _score, _index, item in scored]


_HEADLINE_PATTERN = re.compile(
    r"^(?P<title>.*?)\s+with\s+(?P<years>\d+)\+?\s*years[^,.]*?(?:across|in)\s+(?P<domains>.+)$",
    re.IGNORECASE,
)
_TITLE_YEARS_PATTERN = re.compile(
    r"^(?P<title>.*?)\s+with\s+(?P<years>\d+)\+?\s*years", re.IGNORECASE
)
_TITLE_ONLY_PATTERN = re.compile(r"^(?P<title>.*?)\s+with\s+", re.IGNORECASE)
_HEADLINE_YEARS_HAS_PLUS = re.compile(r"\d\+\s*years", re.IGNORECASE)


def _split_domain_list(domains_text: str) -> list[str]:
    """Split a natural-language "X, Y, Z and W" list back into items,
    handling the common no-Oxford-comma "...Y and Z" tail."""
    items: list[str] = []
    for raw in domains_text.rstrip(".").split(","):
        raw = raw.strip()
        if not raw:
            continue
        if " and " in raw:
            items.extend(part.strip() for part in raw.split(" and ") if part.strip())
        else:
            items.append(raw)
    return items


def build_professional_summary_sentence(
    profile: dict, vacancy_keywords: set[str] | None = None, max_domains: int = 3,
) -> str | None:
    """Build a natural, recruiter-facing identity sentence from structured
    profile data -- generic (no employer/vacancy name, no hardcoded
    professional title): "{Title} with over {years} years of experience in
    {top vacancy-relevant domains}." Long stored domain lists (e.g. 8 items)
    are trimmed to the ones that actually matter for THIS vacancy rather
    than dumped wholesale, per Task 21.13 section 2. Never invents a title,
    years figure, or domain not already present in the stored profile."""
    vacancy_keywords = vacancy_keywords or set()
    headline = ((profile.get("professional_summary") or {}).get("headline") or "").strip()
    structured_years = (profile.get("experience") or {}).get("years")
    headline_has_plus = bool(_HEADLINE_YEARS_HAS_PLUS.search(headline))

    if not headline and not structured_years:
        return None

    title = headline
    domains_text = ""
    years_from_headline = None

    match = _HEADLINE_PATTERN.match(headline)
    if match:
        title = match.group("title").strip()
        domains_text = match.group("domains").strip()
        years_from_headline = match.group("years")
    else:
        title_years_match = _TITLE_YEARS_PATTERN.match(headline)
        if title_years_match:
            title = title_years_match.group("title").strip()
            years_from_headline = title_years_match.group("years")
        else:
            title_match = _TITLE_ONLY_PATTERN.match(headline)
            if title_match:
                title = title_match.group("title").strip()

    if not title:
        title = "Experienced professional"

    # Prefer an explicit structured years figure; fall back to whatever the
    # headline itself states so years information is never silently dropped
    # just because the profile only encodes it as prose (Task 21.13
    # section 2: preserve "over 15 years"/"15+ years", never reduce it).
    effective_years = structured_years if structured_years is not None else years_from_headline

    years_phrase = ""
    if effective_years is not None:
        years_display = str(effective_years)
        if years_display.endswith("+"):
            years_phrase = f" with {years_display} years of experience"
        elif structured_years is None and headline_has_plus:
            years_phrase = f" with {years_display}+ years of experience"
        else:
            years_phrase = f" with over {years_display} years of experience"

    domain_phrase = ""
    if domains_text:
        items = _split_domain_list(domains_text)
        ranked = rank_flat_items(items, vacancy_keywords) if vacancy_keywords else items
        top = ranked[:max_domains]
        if top:
            domain_phrase = f" in {_join_natural(top)}"

    return f"{title}{years_phrase}{domain_phrase}.".strip()


def select_top(entries: list[dict], vacancy_keywords: set[str], text_fields: tuple[str, ...], top_k: int) -> list[dict]:
    """Rank then keep at most top_k entries. If nothing scored (no overlap
    at all -- e.g. an empty/very generic vacancy), falls back to the
    original order rather than arbitrarily dropping verified experience."""
    if not vacancy_keywords:
        return entries[:top_k]
    return rank_entries(entries, vacancy_keywords, text_fields)[:top_k]
