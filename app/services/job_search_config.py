"""Central discovery configuration for the current remote-finance phase."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetMarket:
    key: str
    label: str
    linkedin_geo_id: str
    indeed_country: str


@dataclass(frozen=True)
class RoleFamily:
    key: str
    label: str
    query: str
    covered_roles: tuple[str, ...]


@dataclass(frozen=True)
class ProfessionalServiceQuery:
    key: str
    label: str
    query: str


@dataclass(frozen=True)
class FinanceTechRoleFamily:
    key: str
    label: str
    query: str
    covered_roles: tuple[str, ...]


TARGET_MARKETS = (
    TargetMarket("united_kingdom", "United Kingdom", "101165590", "United Kingdom"),
    TargetMarket("united_states", "United States", "103644278", "United States"),
    TargetMarket("australia", "Australia", "101452733", "Australia"),
)

# Five representative queries cover the requested role families without
# launching a nearly identical search for every title variant.
ROLE_FAMILIES = (
    RoleFamily(
        "accounting_reporting",
        "Accounting / Financial Reporting",
        "Financial Accountant",
        ("Senior Accountant", "Management Accountant", "Senior Management Accountant", "Financial Accountant", "Senior Financial Accountant", "Financial Reporting Manager", "Accounting Manager"),
    ),
    RoleFamily(
        "fpa_analysis",
        "FP&A / Financial Analysis",
        "Financial Analyst",
        ("Financial Analyst", "Senior Financial Analyst", "FP&A Analyst", "Senior FP&A Analyst", "FP&A Manager", "Financial Planning and Analysis Manager"),
    ),
    RoleFamily(
        "finance_management",
        "Finance Management / Business Partnering",
        "Finance Manager",
        ("Finance Manager", "Senior Finance Manager", "Commercial Finance Manager", "Finance Business Partner", "Senior Finance Business Partner"),
    ),
    RoleFamily(
        "controller_leadership",
        "Controller / Finance Leadership",
        "Financial Controller",
        ("Financial Controller", "Group Financial Controller", "Finance Controller", "Head of Finance"),
    ),
    RoleFamily(
        "commercial_transformation",
        "Commercial Finance / Transformation",
        "Commercial Finance",
        ("Commercial Analyst", "Senior Commercial Analyst", "Commercial Finance", "Finance Transformation", "Finance Systems", "Finance Automation"),
    ),
)

# Exact periodic searches provide professional-services coverage without a
# large employer whitelist or extra actor runs. They share the normal rotation
# budget with the finance families below.
PROFESSIONAL_SERVICE_QUERIES = (
    ProfessionalServiceQuery("audit", "Audit / Assurance", "Audit Manager"),
    ProfessionalServiceQuery("tax", "Tax Advisory", "Tax Manager"),
    ProfessionalServiceQuery("risk_advisory", "Risk Advisory", "Risk Advisory Manager"),
    ProfessionalServiceQuery("business_advisory", "Business Advisory", "Business Advisory Manager"),
    ProfessionalServiceQuery("cfo_advisory", "CFO Advisory", "CFO Advisory Consultant"),
    ProfessionalServiceQuery("outsourced_finance", "Outsourced Finance", "Outsourced Finance Manager"),
    ProfessionalServiceQuery("transaction_services", "Transaction Services", "Transaction Services Manager"),
    ProfessionalServiceQuery("financial_due_diligence", "Financial Due Diligence", "Financial Due Diligence Manager"),
    ProfessionalServiceQuery("forensic_accounting", "Forensic Accounting", "Forensic Accountant"),
)

# Finance-to-technology crossover queries are deliberately compact. They share
# the existing bounded rotation rather than adding actor runs or one search per
# synonym. Core finance families above remain unchanged.
FINANCE_TECH_ROLE_FAMILIES = (
    FinanceTechRoleFamily("finance_transformation", "Finance Transformation", "Finance Transformation", ("Finance Transformation Manager", "Digital Finance Transformation", "Finance Process Transformation", "Finance Transformation Business Analyst")),
    FinanceTechRoleFamily("finance_systems", "Finance Systems", "Finance Systems", ("Finance Systems Manager", "Financial Systems Manager", "Finance Applications Manager", "Finance Systems Business Analyst")),
    FinanceTechRoleFamily("finance_automation", "Finance Automation", "Finance Automation", ("Finance Automation Manager", "Finance Process Automation", "Accounting Automation", "Digital Finance")),
    FinanceTechRoleFamily("financial_data_analytics", "Financial Data & Analytics", "Finance Data Analyst", ("Financial Data Analyst", "Finance Analytics", "FP&A Analytics", "Finance Business Intelligence")),
    FinanceTechRoleFamily("fintech_finance", "FinTech Finance", "FinTech Finance Manager", ("FinTech Financial Controller", "Strategic Finance - FinTech", "FinTech Business Analyst", "Financial Product Analyst")),
    FinanceTechRoleFamily("regtech_risk", "RegTech / Risk Technology", "Regulatory Technology", ("Compliance Technology", "Risk Technology", "GRC Technology", "Regulatory Reporting Systems")),
    FinanceTechRoleFamily("accounting_tech", "Accounting Technology", "Accounting Systems", ("Accounting Systems Manager", "Financial Close Technology", "General Ledger Systems", "Record-to-Report Technology")),
    FinanceTechRoleFamily("erp_epm", "ERP / EPM", "Finance Systems Consultant", ("ERP Finance Consultant", "EPM Consultant", "FP&A Systems Consultant", "Oracle EPM", "SAP Finance")),
    FinanceTechRoleFamily("payments_banking_tech", "Payments / Banking Technology", "Payments Operations", ("Payment Technology", "Financial Infrastructure", "Banking Technology", "Treasury Technology")),
    FinanceTechRoleFamily("finance_tech_advisory", "Finance Technology Advisory", "Finance Technology Consultant", ("ERP Advisory", "Digital Audit", "Technology Risk", "CFO Advisory")),
)

DISCOVERY_QUERY_CYCLE = (*ROLE_FAMILIES, *PROFESSIONAL_SERVICE_QUERIES, *FINANCE_TECH_ROLE_FAMILIES)

# LinkedIn's ``f_TPR`` filter is expressed as seconds since posting.  This
# aligns the acquisition window with the deterministic <=7-day admission gate.
LINKEDIN_FRESHNESS_SECONDS = "r604800"  # 7 days
# The current Indeed actor exposes both ``posted_since`` and ``remote_only``;
# strict remote eligibility takes precedence, so the actor's dated output is
# retained but no unverified combined date filter is sent.
INDEED_POSTED_SINCE = None
# The actor has one singular ``keyword`` input. Use one exact representative
# family keyword per market and rotate families deterministically instead of
# assuming it interprets Boolean syntax or quoted multi-queries.
INDEED_QUERY_MODE = "single_family_rotation"


def linkedin_searches():
    """Return 15 compact, strictly remote LinkedIn searches (5 × 3 markets)."""
    return tuple(
        {"market": market, "family": family, "keyword": family.query}
        for market in TARGET_MARKETS
        for family in ROLE_FAMILIES
    )


def linkedin_market_searches(market: TargetMarket, count: int, rotation_index: int = 0):
    """Select compact, rotating family queries for one market actor run.

    The actor has a run-level result cap, so supplying every role URL with a
    tiny cap lets the first URL starve later families.  Select at most one URL
    per requested raw result and rotate through all finance and professional
    services families across refreshes.
    """
    requested_groups = min(max(count, 1), len(DISCOVERY_QUERY_CYCLE))
    return tuple(
        {
            "market": market,
            "family": DISCOVERY_QUERY_CYCLE[(rotation_index + offset) % len(DISCOVERY_QUERY_CYCLE)],
            "keyword": DISCOVERY_QUERY_CYCLE[(rotation_index + offset) % len(DISCOVERY_QUERY_CYCLE)].query,
        }
        for offset in range(requested_groups)
    )


def distribute_result_budget(total: int, buckets: int) -> tuple[int, ...]:
    """Split a source-level result cap without multiplying result volume."""
    if total < 1 or buckets < 1:
        return ()
    active_buckets = min(total, buckets)
    base, remainder = divmod(total, active_buckets)
    return tuple(base + (1 if index < remainder else 0) for index in range(active_buckets))


def indeed_searches(total_results: int, rotation_index: int = 0):
    """Return one exact family query per market at the per-market source cap.

    A market gets the next family offset by its position, so three different
    families are sampled on a small daily run. The caller rotates the offset
    on later refresh days; no generic finance query is emitted.
    """
    return tuple(
        {
            "market": market,
            "family": DISCOVERY_QUERY_CYCLE[(rotation_index + market_index) % len(DISCOVERY_QUERY_CYCLE)],
            "keyword": DISCOVERY_QUERY_CYCLE[(rotation_index + market_index) % len(DISCOVERY_QUERY_CYCLE)].query,
            "max_results": total_results,
        }
        for market_index, market in enumerate(TARGET_MARKETS)
    )
