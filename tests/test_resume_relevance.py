"""Task 21.11: proves resume content ranking is genuinely vacancy-driven --
the same two synthetic employment_history entries (an accounting/Xero role
and an AI/automation role) rank in OPPOSITE order depending purely on which
vacancy's keywords are supplied. No employer/vacancy name is hardcoded in
the ranking logic itself; only these tests' own fixtures name anything."""

from app.services.resume_relevance import (
    build_professional_summary_sentence,
    extract_vacancy_keywords,
    humanize_responsibilities,
    rank_entries,
    score_entry,
    select_top,
)


ACCOUNTING_ENTRY = {
    "company": "Australian Accounting Firm",
    "position": "Offshore Accounting Manager",
    "responsibilities": ["Management Accounting", "Financial Reporting", "Australian Tax"],
    "technologies": ["Xero", "MYOB", "QuickBooks"],
}

AI_ENTRY = {
    "company": "Career Intelligence Platform",
    "position": "Founder",
    "responsibilities": ["AI Agents", "LLM Integration", "Workflow Automation"],
    "technologies": ["Python", "FastAPI", "OpenAI"],
}


def test_accounting_vacancy_ranks_accounting_entry_first():
    vacancy_keywords = extract_vacancy_keywords({
        "required_skills": ["Xero", "MYOB", "Australian Tax"],
        "finance_domains": ["Management Accounting", "Financial Reporting"],
        "job_title": "Tax & Business Advisory Accountant",
    })
    ranked = rank_entries([AI_ENTRY, ACCOUNTING_ENTRY], vacancy_keywords)
    assert ranked[0] is ACCOUNTING_ENTRY


def test_ai_vacancy_ranks_ai_entry_first():
    """Same two entries, same code path -- opposite vacancy, opposite order."""
    vacancy_keywords = extract_vacancy_keywords({
        "required_skills": ["Python", "FastAPI", "OpenAI"],
        "keywords": ["AI Agents", "LLM Integration", "Workflow Automation"],
        "job_title": "AI Automation Engineer",
    })
    ranked = rank_entries([ACCOUNTING_ENTRY, AI_ENTRY], vacancy_keywords)
    assert ranked[0] is AI_ENTRY


def test_no_vacancy_keywords_preserves_original_order():
    ranked = rank_entries([ACCOUNTING_ENTRY, AI_ENTRY], set())
    assert ranked == [ACCOUNTING_ENTRY, AI_ENTRY]


def test_select_top_limits_count_and_ranks():
    vacancy_keywords = extract_vacancy_keywords({"required_skills": ["Xero"]})
    third = {"company": "Unrelated", "responsibilities": ["Nothing relevant"]}
    top = select_top([third, AI_ENTRY, ACCOUNTING_ENTRY], vacancy_keywords, (
        "company", "position", "responsibilities", "technologies",
    ), top_k=2)
    assert len(top) == 2
    assert ACCOUNTING_ENTRY in top


def test_extract_vacancy_keywords_reads_ats_keyword_summary_too():
    keywords = extract_vacancy_keywords(
        {},
        ats_result={"keyword_summary": {"matched": [{"keyword": "Xero"}], "missing": [{"keyword": "MYOB"}]}},
    )
    assert "xero" in keywords
    assert "myob" in keywords


def test_score_entry_counts_distinct_keyword_matches():
    score = score_entry(ACCOUNTING_ENTRY, {"xero", "myob", "unrelated-keyword"}, (
        "company", "position", "responsibilities", "technologies",
    ))
    assert score == 2


# --- Task 21.13: natural prose instead of mechanical field-list sentences ---

def test_keyword_responsibilities_become_natural_prose_not_a_field_list():
    """"Led X, Y, Z as Position at Company." reads like a database dump.
    The generic rewrite should produce a natural sentence structure instead,
    without inventing or dropping any of the underlying facts."""
    sentence = humanize_responsibilities(
        ["Management Accounting", "Financial Reporting", "Australian Tax", "Leadership"],
        "Example Accounting Firm", "Offshore Accounting Manager",
    )[0]
    assert not sentence.startswith("Led Management Accounting")
    assert not sentence.startswith("Responsible for Management Accounting")
    assert "As Offshore Accounting Manager at Example Accounting Firm" in sentence
    assert "Management Accounting" in sentence
    assert "Financial Reporting" in sentence
    assert "Australian Tax" in sentence
    # Leadership is folded into team-leadership context, not dumped into the
    # domain-noun list alongside "Management Accounting" etc.
    assert "and Leadership" not in sentence
    assert "leadership responsibility" in sentence.lower()


def test_natural_prose_preserves_domain_phrase_capitalization():
    """A previous version force-lowercased only the first character of the
    joined list, producing inconsistent results like "management Accounting"."""
    sentence = humanize_responsibilities(
        ["Management Accounting", "Financial Reporting"], "Firm Co", "Accountant",
    )[0]
    assert "Management Accounting" in sentence
    assert "management Accounting" not in sentence


def test_responsibilities_without_leadership_omit_leadership_clause():
    sentence = humanize_responsibilities(
        ["Project Finance", "Financial Modelling"], "Independent Consulting", "Consultant",
    )[0]
    assert "leadership" not in sentence.lower()


# --- Task 21.13: professional summary quality ------------------------------

def test_professional_summary_preserves_title_capitalization():
    profile = {
        "professional_summary": {"headline": "Chartered Accountant with 15+ years of experience across accounting, taxation and audit."},
    }
    sentence = build_professional_summary_sentence(profile)
    assert sentence.startswith("Chartered Accountant")
    assert "chartered Accountant" not in sentence
    assert "chartered accountant" not in sentence


def test_professional_summary_preserves_over_n_years_not_reduced():
    profile = {"experience": {"years": 15}, "professional_summary": {"headline": "Chartered Accountant"}}
    sentence = build_professional_summary_sentence(profile)
    assert "over 15 years" in sentence
    assert "with 15 years" not in sentence


def test_professional_summary_preserves_headline_plus_when_no_structured_years():
    profile = {
        "professional_summary": {"headline": "Chartered Accountant with 15+ years of experience across accounting, taxation and audit."},
    }
    sentence = build_professional_summary_sentence(profile)
    assert "15+ years" in sentence


def test_professional_summary_trims_long_domain_list_to_vacancy_relevant_items():
    """The stored headline lists 8 domains; only the vacancy-relevant ones
    should survive, not the full keyword dump."""
    profile = {
        "experience": {"years": 15},
        "professional_summary": {
            "headline": (
                "Chartered Accountant with 15+ years of experience across accounting, taxation, "
                "audit, financial reporting, Australian public practice, ERP implementation, "
                "business automation and data-driven finance."
            ),
        },
    }
    sentence = build_professional_summary_sentence(profile, vacancy_keywords={"audit", "taxation"}, max_domains=3)
    domain_count = sentence.count(",") + 1  # rough count of items in the "in X, Y and Z" clause
    assert domain_count <= 4
    assert "audit" in sentence.lower()
    assert "taxation" in sentence.lower()
    assert "ERP implementation" not in sentence


def test_professional_summary_never_hardcodes_a_professional_title():
    """A profile with no headline and no stored title still produces a
    generic (not hardcoded to any specific profession) fallback."""
    sentence = build_professional_summary_sentence({"experience": {"years": 10}})
    assert "Chartered Accountant" not in sentence
    assert "over 10 years" in sentence
