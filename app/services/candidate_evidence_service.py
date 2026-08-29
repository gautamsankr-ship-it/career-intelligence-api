"""Career Evidence Library: a reusable, provenance-tagged layer over the
candidate's richer source documents (full resume, past cover letters),
sitting alongside the existing structured `master_candidate_profile.json`.

Purpose (Task 21.11 Addendum): application generators should not be limited
to the simplified profile record -- they should be able to draw on the full
depth of verified career history (e.g. SMSF/ASIC/BAS/IAS specifics for the
Trident Financial Group role) while never promoting an unconfirmed,
single-source, or conflicting claim into an employer-facing document.

Every fact in the library carries a `status`:
  VERIFIED          -- safe to surface in generated documents
  NEEDS_CONFIRMATION -- candidate must confirm before it can ever be used
  CONFLICTING        -- two sources disagree, unresolved
  SUPERSEDED         -- an older fact overridden by a newer authoritative one

`get_enriched_profile()` is the integration point generators call: it merges
only VERIFIED facts into a copy of the profile dict, so a generator that
already reads `profile["employment_history"]` picks up the richer evidence
automatically without needing to know the library exists. NEEDS_CONFIRMATION/
CONFLICTING facts are never merged in -- they are only reachable via
`reconciliation_report()`, which exists for human review, not for document
generation.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

VERIFIED = "VERIFIED"
NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
CONFLICTING = "CONFLICTING"
SUPERSEDED = "SUPERSEDED"

_USABLE_STATUSES = {VERIFIED}

DEFAULT_LIBRARY_PATH = Path("app/data/candidate_evidence_library.json")


def load_library(path: str | Path = DEFAULT_LIBRARY_PATH) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _usable_texts(items: list[dict]) -> list[str]:
    return [item["text"] for item in items if item.get("status") in _USABLE_STATUSES]


def _find_record(records: list[dict], alias_key: str, name_key: str, alias_or_name: str) -> dict | None:
    for record in records:
        if record.get(alias_key) == alias_or_name:
            return record
        if record.get(name_key) == alias_or_name:
            return record
    return None


def _find_employment_record(library: dict, alias_or_company: str) -> dict | None:
    return _find_record(library.get("employment_history", []), "profile_company_alias", "company", alias_or_company)


def _find_board_record(library: dict, alias_or_organization: str) -> dict | None:
    return _find_record(
        library.get("board_positions", []), "profile_organization_alias", "organization", alias_or_organization
    )


def enrich_employment_history(employment_history: list[dict], library: dict) -> list[dict]:
    """Return a new employment_history list where each entry that has a
    matching evidence-library record gets its responsibilities/technologies
    extended with VERIFIED-only facts. Entries with no library match, and
    NEEDS_CONFIRMATION/CONFLICTING facts, pass through untouched."""
    enriched = []
    for entry in employment_history:
        entry = copy.deepcopy(entry)
        record = _find_employment_record(library, entry.get("company", ""))
        if record is not None:
            period = record.get("period") or {}
            if period.get("status") == VERIFIED and period.get("value"):
                entry["period"] = period["value"]

            team_size = record.get("team_size") or {}
            if team_size.get("status") == VERIFIED and team_size.get("value") is not None:
                entry["team_size"] = team_size["value"]

            extra_responsibilities = _usable_texts(record.get("extended_responsibilities", []))
            extra_technologies = _usable_texts(record.get("extended_technologies", []))
            extra_achievements = _usable_texts(record.get("quantified_achievements", []))

            existing_responsibilities = list(entry.get("responsibilities") or [])
            seen_r = {r.lower() for r in existing_responsibilities}
            for text in extra_responsibilities:
                if text.lower() not in seen_r:
                    existing_responsibilities.append(text)
                    seen_r.add(text.lower())
            entry["responsibilities"] = existing_responsibilities

            existing_technologies = list(entry.get("technologies") or [])
            seen_t = {t.lower() for t in existing_technologies}
            for text in extra_technologies:
                if text.lower() not in seen_t:
                    existing_technologies.append(text)
                    seen_t.add(text.lower())
            entry["technologies"] = existing_technologies

            existing_achievements = list(entry.get("achievements") or [])
            seen_a = {a.lower() for a in existing_achievements}
            for text in extra_achievements:
                if text.lower() not in seen_a:
                    existing_achievements.append(text)
                    seen_a.add(text.lower())
            entry["achievements"] = existing_achievements

        enriched.append(entry)
    return enriched


def enrich_board_positions(board_positions: list[dict], library: dict) -> list[dict]:
    """Same VERIFIED-only merge as `enrich_employment_history`, for board
    positions (matched by organization name/alias instead of company)."""
    enriched = []
    for entry in board_positions:
        entry = copy.deepcopy(entry)
        record = _find_board_record(library, entry.get("organization", ""))
        if record is not None:
            period = record.get("period") or {}
            if period.get("status") == VERIFIED and period.get("value"):
                entry["period"] = period["value"]

            extra_responsibilities = _usable_texts(record.get("extended_responsibilities", []))
            extra_achievements = _usable_texts(record.get("quantified_achievements", []))

            existing_responsibilities = list(entry.get("responsibilities") or [])
            seen_r = {r.lower() for r in existing_responsibilities}
            for text in extra_responsibilities:
                if text.lower() not in seen_r:
                    existing_responsibilities.append(text)
                    seen_r.add(text.lower())
            entry["responsibilities"] = existing_responsibilities

            existing_achievements = list(entry.get("achievements") or [])
            seen_a = {a.lower() for a in existing_achievements}
            for text in extra_achievements:
                if text.lower() not in seen_a:
                    existing_achievements.append(text)
                    seen_a.add(text.lower())
            entry["achievements"] = existing_achievements

        enriched.append(entry)
    return enriched


def enrich_ventures(entrepreneurship: list[dict], library: dict) -> list[dict]:
    """Same VERIFIED-only merge for entrepreneurship/venture entries (matched
    by the profile's 'venture' or 'company' field against the library)."""
    enriched = []
    for entry in entrepreneurship:
        entry = copy.deepcopy(entry)
        name = entry.get("venture") or entry.get("company") or ""
        record = _find_record(library.get("ventures", []), "profile_company_alias", "company", name)
        if record is not None:
            description = record.get("description") or {}
            if description.get("status") == VERIFIED and description.get("text") and not entry.get("description"):
                entry["description"] = description["text"]

            extra_achievements = _usable_texts(record.get("quantified_achievements", []))
            existing_achievements = list(entry.get("achievements") or [])
            seen_a = {a.lower() for a in existing_achievements}
            for text in extra_achievements:
                if text.lower() not in seen_a:
                    existing_achievements.append(text)
                    seen_a.add(text.lower())
            entry["achievements"] = existing_achievements

        enriched.append(entry)
    return enriched


def quantified_achievements(library: dict, status: str = VERIFIED) -> list[dict]:
    """All quantified achievements across employment_history, board_positions
    and ventures at the given status, each still carrying its
    `text`/`source`/`status`."""
    results = []
    for record in (
        library.get("employment_history", [])
        + library.get("board_positions", [])
        + library.get("ventures", [])
    ):
        for item in record.get("quantified_achievements", []):
            if item.get("status") == status:
                results.append(item)
    return results


def get_enriched_profile(profile: dict, library: dict | None = None) -> dict:
    """Return a copy of `profile` enriched with VERIFIED-only evidence-library
    facts. Never introduces a NEEDS_CONFIRMATION/CONFLICTING/SUPERSEDED fact.
    Safe to call repeatedly and cheap (the library itself is loaded once by
    the caller and can be reused across calls)."""
    if library is None:
        library = load_library()

    enriched = copy.deepcopy(profile)
    enriched["employment_history"] = enrich_employment_history(
        profile.get("employment_history") or [], library
    )
    enriched["board_positions"] = enrich_board_positions(
        profile.get("board_positions") or [], library
    )
    enriched["entrepreneurship"] = enrich_ventures(
        profile.get("entrepreneurship") or [], library
    )

    # Deliberately NOT a blind global merge of every VERIFIED quantified
    # achievement in the library: that would let a GSN-only fact (e.g. "40
    # professionals") leak into a profile that has no GSN entry at all.
    # Achievements are already delivered scoped to their actual employer/
    # board/venture entry by the three enrich_* calls above.
    return enriched


def reconciliation_report(library: dict | None = None) -> list[dict]:
    """The full CANDIDATE_FACT_RECONCILIATION_REPORT data: every detected
    conflict between source documents, for human review. Never consumed by
    document generators -- this is a transparency/audit artifact only."""
    if library is None:
        library = load_library()
    return library.get("conflicts", [])
