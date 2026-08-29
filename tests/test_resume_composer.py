"""Regression for Task 21.10C: ResumeComposer's executive summary was hardcoded
as f"...with over {years}+ years of experience." -- the stray "+" produced the
awkward "over 15+ years" wording even though the stored years value is plainly
15 (not "15+"). Also confirms the new tailored executive_summary override seam
used to supply a per-application professional profile without editing the
generic template. Synthetic inputs only; never touches the production profile."""

from app.services.resume_composer import ResumeComposer


def _compose(profile_overrides=None, strategy_overrides=None, job_analysis_overrides=None, ats_overrides=None):
    profile = {
        "candidate": {"full_name": "Jane Candidate", "email": "jane@example.test"},
        "experience": {"years": 15},
        "professional_summary": {"headline": "Chartered Accountant"},
    }
    profile.update(profile_overrides or {})
    strategy = {"resume_title": "Senior Accountant"}
    strategy.update(strategy_overrides or {})
    job_analysis = {"job_title": "Senior Accountant"}
    job_analysis.update(job_analysis_overrides or {})
    ats = {}
    ats.update(ats_overrides or {})
    return ResumeComposer().compose(profile, job_analysis, None, ats, strategy)


def test_default_summary_says_over_n_years_without_a_stray_plus():
    composition = _compose()
    assert composition["summary"]["executive_summary"] == "Chartered Accountant with over 15 years of experience."
    assert "15+" not in composition["summary"]["executive_summary"]


def test_tailored_executive_summary_override_is_used_when_supplied():
    tailored = "Chartered Accountant with over 15 years of Australian and international accounting experience."
    composition = _compose(strategy_overrides={"executive_summary": tailored})
    assert composition["summary"]["executive_summary"] == tailored


ACCOUNTING_JOB = {"company": "Example Accounting Firm", "position": "Offshore Accounting Manager",
                   "responsibilities": ["Management Accounting", "Financial Reporting", "Australian Tax"],
                   "technologies": ["Xero", "MYOB", "QuickBooks"]}
AI_JOB = {"company": "Career Intelligence Platform", "position": "Founder",
          "responsibilities": ["AI Agents", "LLM Integration"], "technologies": ["Python", "FastAPI", "OpenAI"]}


def test_employment_history_is_reordered_by_vacancy_relevance_end_to_end():
    """Same two jobs, same profile -- composing against an accounting vacancy
    puts the accounting job first; a different (AI) vacancy would put the
    other job first (see the companion test below). Nothing about either
    job's content is hardcoded into ResumeComposer itself."""
    composition = _compose(
        profile_overrides={"employment_history": [AI_JOB, ACCOUNTING_JOB]},
        job_analysis_overrides={"required_skills": ["Xero", "MYOB"], "finance_domains": ["Management Accounting"]},
    )
    companies = [job["company"] for job in composition["experience"]["employment_history"]]
    assert companies[0] == "Example Accounting Firm"


def test_employment_history_reorders_the_other_way_for_a_different_vacancy():
    composition = _compose(
        profile_overrides={"employment_history": [ACCOUNTING_JOB, AI_JOB]},
        job_analysis_overrides={"required_skills": ["Python", "FastAPI", "OpenAI"]},
    )
    companies = [job["company"] for job in composition["experience"]["employment_history"]]
    assert companies[0] == "Career Intelligence Platform"


def test_terse_keyword_responsibilities_are_humanized_into_a_sentence():
    composition = _compose(profile_overrides={"employment_history": [ACCOUNTING_JOB]})
    responsibilities = composition["experience"]["employment_history"][0]["responsibilities"]
    assert len(responsibilities) == 1
    sentence = responsibilities[0]
    assert sentence.endswith(".")
    assert "anagement Accounting" in sentence
    assert "Offshore Accounting Manager" in sentence
    assert "Example Accounting Firm" in sentence


def test_sentence_style_responsibilities_pass_through_unchanged():
    hand_written = ["Managed offshore accounting operations for a portfolio of Australian SME clients, working remotely."]
    composition = _compose(profile_overrides={"employment_history": [{**ACCOUNTING_JOB, "responsibilities": hand_written}]})
    assert composition["experience"]["employment_history"][0]["responsibilities"] == hand_written


def test_software_dict_is_flattened_and_ranked_not_dumped_by_category():
    composition = _compose(
        profile_overrides={"software": {"accounting": ["Xero", "MYOB"], "development": ["FastAPI", "PostgreSQL"]}},
        job_analysis_overrides={"required_skills": ["Xero"]},
    )
    software = composition["skills"]["software"]
    assert isinstance(software, list)
    assert software[0] == "Xero"
    assert "Xero" in software and "MYOB" in software and "FastAPI" in software


def test_employment_history_is_capped_for_length():
    many_jobs = [{"company": f"Company {i}", "responsibilities": []} for i in range(10)]
    composition = _compose(profile_overrides={"employment_history": many_jobs})
    assert len(composition["experience"]["employment_history"]) <= 5


# --- Task 21.13 section 4: consolidated resume structure --------------------

def test_skills_are_consolidated_into_one_core_competencies_list():
    """Previously "core"/"technical"/"industry" rendered as four overlapping
    sections (Core Focus Areas, Core Skills, Technical Skills, Industry
    Expertise). They're now folded into a single deduplicated "core" list."""
    composition = _compose(
        strategy_overrides={"skills_priority": ["Tax Planning", "Business Advisory"], "summary_focus": ["Client Advisory"]},
        profile_overrides={
            "technical_capabilities": {"ai": ["AI Agents"]},
            "industry_expertise": {"primary": ["Accounting", "Audit"]},
        },
    )
    skills = composition["skills"]
    assert "technical" not in skills
    assert "industry" not in skills
    core = skills["core"]
    assert "Tax Planning" in core
    assert "Business Advisory" in core
    assert "Client Advisory" in core
    assert "AI Agents" in core
    assert "Accounting" in core


def test_core_competencies_are_deduplicated_case_insensitively():
    composition = _compose(
        strategy_overrides={"skills_priority": ["Tax Planning"], "summary_focus": ["tax planning"]},
    )
    core = composition["skills"]["core"]
    assert sum(1 for item in core if item.lower() == "tax planning") == 1


def test_summary_no_longer_carries_a_separate_focus_areas_list():
    """"Core Focus Areas" as its own Professional Summary sub-section is
    gone -- that content is now part of the single Core Competencies list."""
    composition = _compose(strategy_overrides={"summary_focus": ["Client Advisory"]})
    assert "focus_areas" not in composition["summary"]
