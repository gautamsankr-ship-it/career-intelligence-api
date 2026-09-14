"""Read-only Settings view over the project's existing sources of truth.

This module deliberately does not instantiate the Answer Vault writer: its
normal loader may apply historical migrations. Settings is a management view,
so it reads the already-persisted JSON and existing CRM governance tables
without creating a second profile, eligibility, or answer store.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.application_answer_vault import DEFAULT_PATH as ANSWER_VAULT_PATH
from app.services.candidate_evidence_service import DEFAULT_LIBRARY_PATH

PROFILE_PATH = Path("app/data/master_candidate_profile.json")


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _display(value: Any) -> str:
    if value is None or value == "":
        return "Not recorded"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str) and value.upper() in {"YES", "NO"}:
        return value.title()
    if isinstance(value, list):
        return ", ".join(_display(item) for item in value) or "Not recorded"
    if isinstance(value, dict):
        return "; ".join(f"{key.replace('_', ' ').title()}: {_display(item)}" for key, item in value.items())
    return str(value)


def _fact(label: str, value: Any, source: str, control: str = "Evidence-controlled") -> dict[str, str]:
    return {"label": label, "value": _display(value), "source": source, "control": control}


def _answer_category(concept: str) -> str:
    concept = concept.lower()
    if any(term in concept for term in ("email", "phone", "name")):
        return "Personal Details"
    if any(term in concept for term in ("qualification", "membership", "education", "experience", "sql", "python", "erp")):
        return "Professional Qualifications & Experience"
    if any(term in concept for term in ("work_authorization", "sponsorship")):
        return "Work Rights"
    if any(term in concept for term in ("location", "relocat", "travel", "remote")):
        return "Location / Remote Work"
    if any(term in concept for term in ("salary",)):
        return "Salary"
    if any(term in concept for term in ("notice", "start", "availability")):
        return "Availability"
    return "Screening & Application Preferences"


def build_settings_read_model(*, learning: list[dict] | None = None, integrations: dict | None = None) -> dict[str, Any]:
    profile = _read_json(PROFILE_PATH)
    evidence = _read_json(DEFAULT_LIBRARY_PATH)
    vault = _read_json(ANSWER_VAULT_PATH)
    candidate = profile.get("candidate", {})
    summary = profile.get("professional_summary", {})
    experience = profile.get("experience", {})
    preferences = profile.get("career_preferences", {})
    availability = profile.get("availability", {})

    profile_facts = [
        _fact("Preferred name", candidate.get("full_name"), "master_candidate_profile.json:candidate.full_name"),
        _fact("Application email", candidate.get("email"), "master_candidate_profile.json:candidate.email"),
        _fact("Phone", candidate.get("phone"), "master_candidate_profile.json:candidate.phone"),
        _fact("Current location", candidate.get("location"), "master_candidate_profile.json:candidate.location"),
        _fact("Professional headline", summary.get("headline") or candidate.get("title"), "master_candidate_profile.json:professional_summary.headline"),
        _fact("Total experience", f"{experience.get('years')} years" if experience.get("years") else None, "master_candidate_profile.json:experience.years"),
        _fact("Professional summary", summary.get("value_proposition") or summary.get("career_direction"), "master_candidate_profile.json:professional_summary"),
    ]
    strategy = [
        _fact("Target roles", profile.get("career_direction", {}).get("target_roles") or preferences.get("preferred_roles"), "master_candidate_profile.json:career_direction.target_roles"),
        _fact("Target markets", preferences.get("preferred_locations"), "master_candidate_profile.json:career_preferences.preferred_locations"),
        _fact("Preferred work mode", preferences.get("preferred_work_mode"), "master_candidate_profile.json:career_preferences.preferred_work_mode"),
        _fact("Preferred employment", preferences.get("preferred_employment"), "master_candidate_profile.json:career_preferences.preferred_employment"),
        _fact("Preferred industries", profile.get("career_direction", {}).get("preferred_industries"), "master_candidate_profile.json:career_direction.preferred_industries"),
        _fact("Career-value objective", profile.get("career_direction", {}).get("current_focus"), "master_candidate_profile.json:career_direction.current_focus"),
        _fact("Search/source scope", "Configured discovery sources and saved searches", "app/config.py + app/data/job_searches.json", "System-controlled"),
    ]
    eligibility = [
        _fact("Australia — work authorization", "Not currently authorized for physical employment", "application_answer_vault.json:rules[WORK_AUTHORIZATION_AUSTRALIA]"),
        _fact("Australia — physical relocation", "Cannot currently physically relocate", "application_answer_vault.json:rules[WILLING_TO_RELOCATE_AU]"),
        _fact("United States — work authorization", "Not currently authorized for physical employment", "application_answer_vault.json:rules[WORK_AUTHORIZATION_US]"),
        _fact("United States — physical relocation", "Cannot currently physically relocate", "application_answer_vault.json:rules[WILLING_TO_RELOCATE_US]"),
        _fact("United Kingdom — work authorization", "Not assumed from study or travel plans", "application_answer_vault.json:rules[WORK_AUTHORIZATION_UK]"),
        _fact("Remote / cross-border", "May be possible only where the employer arrangement legally permits it", "application eligibility policy + Answer Vault", "System-controlled"),
        _fact("Silent overseas eligibility", "Human review", "app/services/application_eligibility_policy.py", "System-controlled"),
    ]
    qualifications = []
    for item in profile.get("education", []):
        qualifications.append(_fact(item.get("qualification"), item.get("institution"), "master_candidate_profile.json:education", "Evidence-controlled"))
    qualifications.extend(
        _fact("Evidence", item.get("text"), f"candidate_evidence_library.json:education_and_certifications[{item.get('source', 'unknown')}]", "Evidence-controlled")
        for item in evidence.get("education_and_certifications", [])
        if item.get("status") == "VERIFIED"
    )

    answers = []
    for item in vault.get("answers", []):
        value = item.get("value")
        control = "Requires Human Review" if item.get("action") == "MANUAL_REVIEW" or value is None else "Approved reusable answer"
        answers.append({
            "category": _answer_category(item.get("concept", "")),
            "topic": item.get("concept", "").replace("_", " ").title(),
            "answer": _display(value),
            "source": item.get("source_reference") or item.get("source") or "Answer Vault",
            "confidence": item.get("confidence") or "Unknown",
            "status": control,
            "updated": item.get("updated_at") or item.get("approved_at") or "Persisted in Answer Vault",
        })
    answers.sort(key=lambda item: (item["category"], item["topic"]))

    audit = []
    for item in vault.get("audit", [])[-20:][::-1]:
        audit.append({
            "domain": item.get("concept") or item.get("kind") or "Answer Vault",
            "changed_at": item.get("changed_at") or "Unknown",
            "actor": "User-approved or system migration" if item.get("kind") == "migration" else "Recorded actor",
            "reason": item.get("reason") or "No reason recorded",
        })
    return {
        "profile": profile_facts,
        "strategy": strategy,
        "eligibility": eligibility,
        "qualifications": qualifications,
        "answers": answers,
        "learning": learning or [],
        "audit": audit,
        "future_facts": [_fact("Planned education", profile.get("future_education", {}).get("planned_degree"), "master_candidate_profile.json:future_education", "Evidence-controlled")],
        "policy": [
            "Final application submission requires human authorization.",
            "Gmail monitoring is read-only.",
            "CAPTCHA and MFA are human-only.",
            "Unknown consequential screening answers require human review.",
            "Uncertain final submission outcomes are never retried automatically.",
            "Candidate facts are never fabricated.",
        ],
        "integrations": integrations or {},
        "account": [
            _fact("Account model", "Single local user", "Application architecture", "System-controlled"),
            _fact("Application safety", "Dry-run and human-authorized final submission", "app/config.py", "System-controlled"),
            _fact("Hosting security", "Hosted deployment would require HTTPS, authentication, and 2FA", "Future hosting boundary", "System-controlled"),
        ],
    }
