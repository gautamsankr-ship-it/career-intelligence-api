"""Web App Phase 6: assembles interview preparation material from EXISTING
evidence only.

No new candidate/job facts are invented here, and no second scoring/matching
system is introduced -- every output is either a stored value read verbatim
(job_description, evaluation_snapshot, intelligence_priority_reasons) or the
result of calling the SAME per-requirement evidence assessment
`JobIntelligenceService.evaluate()` already computes
(`job_intelligence_service.assess_requirement_evidence`), reused here
read-only. Where evidence genuinely doesn't exist, every function below
reports that honestly (an empty list, or an explicit "Evidence gap" label)
rather than fabricating content to fill the screen.
"""
from __future__ import annotations

import json

from app.services.candidate_evidence_service import get_enriched_profile, load_library
from app.services.job_intelligence_service import assess_requirement_evidence

# Requirement wording that maps to the frozen ICAI/ICAN Chartered Accountant
# vs. ACA/ACCA distinction the Answer Vault already carries (see
# app/services/application_answer_vault.py) -- used only to decide whether a
# behavioural/no-evidence requirement is a genuine wording ambiguity worth a
# "Clarify" question, never to alter the requirement match itself.
_QUALIFICATION_WORDING_TERMS = ("chartered accountant", "aca", "acca", "cpa", "cima", "cfa", "qualified accountant")

READINESS_STRONG = "Strong Evidence"
READINESS_SUPPORTING = "Supporting Evidence"
READINESS_GAP = "Evidence Gap"
READINESS_CLARIFY = "Clarify"

_READINESS_ORDER = {"HARD_REQUIREMENT_GAP": 0, "NO_EVIDENCE": 1, "PARTIAL_EVIDENCE": 2, "STRONG_EVIDENCE": 3}
MAX_READINESS_ROWS = 8
MAX_QUESTIONS_PER_CATEGORY = 3
MAX_ANSWER_PREPARATIONS = 4


def parse_evaluation_snapshot(record: dict) -> dict:
    """Safe read of the stored evaluation_snapshot JSON -- never raises,
    never invents structure that isn't there."""
    raw = record.get("evaluation_snapshot") if record else None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _readiness_label(evidence) -> str:
    if evidence.classification in ("NO_EVIDENCE", "HARD_REQUIREMENT_GAP"):
        if evidence.classification == "NO_EVIDENCE" and evidence.is_behavioural:
            return READINESS_CLARIFY
        return READINESS_GAP
    if evidence.classification == "PARTIAL_EVIDENCE":
        return READINESS_SUPPORTING
    if evidence.classification == "STRONG_EVIDENCE":
        return READINESS_STRONG
    return evidence.classification


def build_readiness_matrix(record: dict, profile: dict) -> list[dict]:
    """Requirement | Evidence | Readiness, straight from
    `assess_requirement_evidence()` -- the intelligence engine's own
    candidate-competitiveness matching logic, never a second heuristic.
    Empty list (never a fabricated row) when the vacancy carries no
    structured job_analysis yet."""
    job_analysis = parse_evaluation_snapshot(record).get("job_analysis") or {}
    if not job_analysis:
        return []
    assessments = assess_requirement_evidence(job_analysis, profile, record.get("job_description") or "")
    ranked = sorted(
        assessments,
        key=lambda e: (not e.is_mandatory, _READINESS_ORDER.get(e.classification, 4)),
    )
    rows = []
    for evidence in ranked[:MAX_READINESS_ROWS]:
        evidence_text = evidence.reason
        if evidence.supporting_evidence:
            evidence_text = f"{evidence_text} ({', '.join(evidence.supporting_evidence)})"
        rows.append({
            "requirement": evidence.requirement,
            "evidence": evidence_text,
            "readiness": _readiness_label(evidence),
            "classification": evidence.classification,
            "is_mandatory": evidence.is_mandatory,
            "is_behavioural": evidence.is_behavioural,
            "matched_words": evidence.supporting_evidence,
        })
    return rows


def _enriched_employment(profile: dict) -> list[dict]:
    try:
        library = load_library()
    except Exception:
        library = None
    try:
        enriched = get_enriched_profile(profile, library)
    except Exception:
        enriched = profile
    return enriched.get("employment_history") or []


def _library_risk_note(company: str, library: dict | None) -> str | None:
    """Surfaces an already-written evidence-library guardrail note (e.g.
    "Belongs to Trident only -- must never be attributed to GSN Associates")
    verbatim, when one exists for this employer's team_size/period fact --
    never a generated warning."""
    if not library or not company:
        return None
    for record in library.get("employment_history", []):
        if record.get("company") == company or record.get("profile_company_alias") == company:
            for field_name in ("team_size", "period"):
                field = record.get(field_name) or {}
                note = field.get("note")
                if note and field.get("status") == "VERIFIED":
                    return note
    return None


def _find_citation(matched_words: tuple, employment: list[dict]) -> dict | None:
    """First employment entry whose own responsibilities/achievements text
    genuinely contains a matched requirement word -- the SAME evidence
    `assess_requirement_evidence()` already counted, just traced back to its
    concrete source line rather than a bare word list."""
    if not matched_words:
        return None
    words = [w.lower() for w in matched_words if isinstance(w, str)]
    if not words:
        return None
    for entry in employment:
        responsibilities = entry.get("responsibilities") or []
        achievements = entry.get("achievements") or []
        action_line = next((line for line in responsibilities if any(w in line.lower() for w in words)), None)
        if not action_line:
            action_line = next((line for line in achievements if any(w in line.lower() for w in words)), None)
        if action_line:
            result_line = next((line for line in achievements if line != action_line), None)
            return {
                "company": entry.get("company") or "",
                "position": entry.get("position") or "",
                "period": entry.get("period") or "",
                "team_size": entry.get("team_size"),
                "action_line": action_line,
                "result_line": result_line,
            }
    return None


def build_my_evidence(readiness_rows: list[dict], profile: dict) -> list[dict]:
    """For each Strong/Supporting-Evidence requirement, the strongest
    already-VERIFIED evidence line that supports it. `Evidence gap -- do not
    fabricate.` for everything else -- never invented."""
    employment = _enriched_employment(profile)
    entries = []
    for row in readiness_rows:
        if row["readiness"] not in (READINESS_STRONG, READINESS_SUPPORTING):
            entries.append({"requirement": row["requirement"], "readiness": row["readiness"], "evidence": "Evidence gap -- do not fabricate."})
            continue
        citation = _find_citation(row["matched_words"], employment)
        if citation:
            evidence_line = f"{citation['position']} at {citation['company']}" + (f" ({citation['period']})" if citation["period"] else "") + f" -- {citation['action_line']}"
        else:
            evidence_line = row["evidence"]
        entries.append({"requirement": row["requirement"], "readiness": row["readiness"], "evidence": evidence_line})
    return entries


def build_answer_preparation(readiness_rows: list[dict], profile: dict) -> list[dict]:
    """Concise STAR-style evidence structures -- never full written answers
    or a memorized script (item 10)."""
    try:
        library = load_library()
    except Exception:
        library = None
    employment = _enriched_employment(profile)
    preps: list[dict] = []
    # Prefer Strong Evidence, but fall back to Supporting Evidence rather
    # than leaving this section empty when a vacancy's specific terminology
    # (e.g. payments "settlements") doesn't exactly overlap with the
    # candidate's own -- the underlying evidence is still genuinely there.
    candidates = [row for row in readiness_rows if row["readiness"] == READINESS_STRONG and row["matched_words"]]
    if not candidates:
        candidates = [row for row in readiness_rows if row["readiness"] == READINESS_SUPPORTING and row["matched_words"]]
    for row in candidates:
        citation = _find_citation(row["matched_words"], employment)
        if not citation:
            continue
        important_fact = ""
        if citation.get("team_size"):
            important_fact = f"Team size at {citation['company']}: {citation['team_size']}."
        elif citation.get("period"):
            important_fact = f"Dates at {citation['company']}: {citation['period']}."
        risk_note = _library_risk_note(citation["company"], library) or "Keep company, dates and figures consistent with your resume and application."
        preps.append({
            "question": f"Tell me about your experience with {row['requirement']}.",
            "core_point": f"{row['requirement'].capitalize()}, demonstrated at {citation['company']}.",
            "situation": f"{citation['position']} at {citation['company']}" + (f" ({citation['period']})" if citation["period"] else "") + ".",
            "action": citation["action_line"],
            "result": citation["result_line"] or "No separately quantified result evidenced -- do not fabricate one.",
            "important_fact": important_fact,
            "risk_to_avoid": risk_note,
        })
        if len(preps) >= MAX_ANSWER_PREPARATIONS:
            break
    return preps


def build_likely_questions(record: dict, readiness_rows: list[dict], risks: list[str]) -> dict[str, list[str]]:
    """Organized by category (item 8) -- every question traces back to an
    actual requirement, a real biggest_challenges entry, an actual flagged
    risk, or a genuine qualification-wording ambiguity. A category with
    nothing to justify a question is simply left empty, never padded."""
    job_analysis = parse_evaluation_snapshot(record).get("job_analysis") or {}
    match_reasoning = job_analysis.get("match_reasoning") or {}
    if not isinstance(match_reasoning, dict):
        match_reasoning = {}
    seniority = (job_analysis.get("seniority") or "").lower()

    role_technical = [
        f"Can you walk me through your experience with {row['requirement']}?"
        for row in readiness_rows
        if row["readiness"] in (READINESS_STRONG, READINESS_SUPPORTING) and row["is_mandatory"] and not row["is_behavioural"]
    ][:MAX_QUESTIONS_PER_CATEGORY]

    leadership = []
    if any(term in seniority for term in ("senior", "lead", "head", "director", "chief", "manager")):
        leadership.append("How has your leadership approach adapted across the different team sizes you've managed?")
    behavioural_gap = [row for row in readiness_rows if row["readiness"] == READINESS_CLARIFY and row["is_behavioural"]]
    for row in behavioural_gap[:MAX_QUESTIONS_PER_CATEGORY - len(leadership)]:
        leadership.append(f"Tell me about a time you demonstrated {row['requirement']}.")

    commercial = [
        f"How would you approach: {challenge}?"
        for challenge in (match_reasoning.get("biggest_challenges") or [])
        if isinstance(challenge, str) and challenge
    ][:MAX_QUESTIONS_PER_CATEGORY]

    candidate_specific = []
    for row in readiness_rows:
        if row["readiness"] == READINESS_GAP and not any(t in row["requirement"].lower() for t in _QUALIFICATION_WORDING_TERMS):
            candidate_specific.append(f"The employer may probe this gap: {row['requirement']}.")
        if len(candidate_specific) >= MAX_QUESTIONS_PER_CATEGORY:
            break
    for risk in risks:
        if len(candidate_specific) >= MAX_QUESTIONS_PER_CATEGORY:
            break
        candidate_specific.append(f"Be ready to address: {risk}")

    screening_derived = [
        f"Be ready to explain how your qualification maps to \"{row['requirement']}\" -- the application evidence flags this wording as needing clarification."
        for row in readiness_rows
        if row["readiness"] == READINESS_CLARIFY and any(t in row["requirement"].lower() for t in _QUALIFICATION_WORDING_TERMS)
    ]

    return {
        "Role / Technical": role_technical,
        "Leadership / Behavioural": leadership,
        "Commercial": commercial,
        "Candidate-Specific Concerns": candidate_specific,
        "From Screening / Application Answers": screening_derived,
    }


def build_company_intelligence(record: dict) -> dict:
    """Stored Application Evidence only (item 11) -- the intelligence
    engine's own employer assessment, already computed and stored at
    evaluation time. External research is deliberately not attempted here
    (item 11: no new research subsystem in this phase)."""
    employer = parse_evaluation_snapshot(record).get("employer") or {}
    if not isinstance(employer, dict) or not employer:
        return {"available": False}
    return {
        "available": True,
        "industry": employer.get("industry") or "",
        "company_size": employer.get("company_size") or "",
        "remote_friendly": employer.get("remote_friendly"),
        "strengths": [s for s in (employer.get("strengths") or []) if isinstance(s, str)],
        "risks": [r for r in (employer.get("risks") or []) if isinstance(r, str)],
        "recommendation": employer.get("recommendation") or "",
        "reason": employer.get("reason") or "",
    }


def build_employer_questions(record: dict, readiness_rows: list[dict]) -> list[str]:
    """~5 high-value questions for the candidate to ask the employer (item
    12) -- derived from role/seniority/business context/known uncertainties,
    never a generic filler list."""
    job_analysis = parse_evaluation_snapshot(record).get("job_analysis") or {}
    match_reasoning = job_analysis.get("match_reasoning") or {}
    if not isinstance(match_reasoning, dict):
        match_reasoning = {}
    questions = [
        "What does success in this role look like in the first 6-12 months?",
        "Who does this role report to, and how is the finance/leadership team currently structured?",
    ]
    challenges = match_reasoning.get("biggest_challenges") or []
    if challenges and isinstance(challenges[0], str):
        questions.append(f"You mentioned challenges like \"{challenges[0]}\" -- what has already been tried, and what's blocking progress?")
    technologies = job_analysis.get("technologies") or []
    if technologies:
        questions.append(f"How mature are the current systems/data ({', '.join(t for t in technologies[:3] if isinstance(t, str))}), and what's the appetite for further transformation?")
    if record.get("remote_eligibility") in (None, "", "MANUAL_REVIEW"):
        questions.append("What are the specific geographic/work-authorization expectations for this role, given it's remote?")
    if job_analysis.get("industry"):
        questions.append(f"What are the biggest commercial pressures facing the business in {job_analysis['industry']} right now?")
    return questions[:5]


def build_briefing(record: dict, profile: dict, readiness_rows: list[dict], risks: list[str], why_pursue: list[str]) -> dict:
    """Assembles the top-level Briefing (item 6). Every section either
    restates an existing structured field or is empty/explicit about a gap
    -- never a fabricated narrative."""
    job_analysis = parse_evaluation_snapshot(record).get("job_analysis") or {}
    match_reasoning = job_analysis.get("match_reasoning") or {}
    if not isinstance(match_reasoning, dict):
        match_reasoning = {}

    strong_rows = [row for row in readiness_rows if row["readiness"] == READINESS_STRONG]
    if not strong_rows:
        strong_rows = [row for row in readiness_rows if row["readiness"] == READINESS_SUPPORTING]
    strongest_match = [row["requirement"] for row in strong_rows[:4]]

    concerns = list(risks)
    for row in readiness_rows:
        if row["readiness"] == READINESS_GAP and row["requirement"] not in concerns:
            concerns.append(f"Evidence gap: {row['requirement']}.")
        if len(concerns) >= 5:
            break

    what_employer_needs = list(match_reasoning.get("must_have_skills") or job_analysis.get("required_skills") or [])[:6]

    if strongest_match:
        objective = f"Demonstrate {', '.join(strongest_match[:3])}"
        if concerns:
            objective += " while addressing " + concerns[0].rstrip(".")
        objective += "."
    elif concerns:
        objective = "Insufficient strong-match evidence to lead with a specific strength -- come prepared to address " + concerns[0].rstrip(".") + "."
    else:
        objective = "Insufficient evidence to derive a specific objective -- rely on the role summary and readiness matrix below."

    return {
        "role": {
            "title": record.get("job_title") or "",
            "company": record.get("company") or "",
            "market": record.get("market") or "",
            "work_arrangement": record.get("work_arrangement") or "",
        },
        "summary": job_analysis.get("summary") or "",
        "why_it_matters": why_pursue,
        "what_employer_needs": what_employer_needs,
        "strongest_match": strongest_match,
        "potential_concerns": concerns,
        "objective": objective,
    }
