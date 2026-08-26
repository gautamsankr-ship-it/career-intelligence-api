"""Curated, discovery-only target employer registry; tiers never affect scoring."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class TargetEmployer:
    employer_id: str; name: str; aliases: tuple[str, ...]; category: str; tier: int
    careers_url: str; ats_platform: str = "UNKNOWN"; ats_identifier: str = ""
    markets: tuple[str, ...] = ("united_kingdom", "united_states", "australia")
    discovery_method: str = "UNKNOWN"; enabled: bool = False
    industry_tags: tuple[str, ...] = ()
    # Optional (market, ATS platform, public endpoint/board identifier) tuples.
    # Employers often use separate career tenants by country.
    ats_by_market: tuple[tuple[str, str, str], ...] = ()

    def ats_for_market(self, market: str | None) -> tuple[str, str]:
        if market:
            for configured_market, platform, identifier in self.ats_by_market:
                if configured_market == market:
                    return platform, identifier
        return self.ats_platform, self.ats_identifier

def _entries(category, tier, names):
    return [TargetEmployer(n.lower().replace(" ", "_").replace("&", "and").replace("/", "_"), n, (), category, tier, "") for n in names]


def _tagged_entries(category, tier, names, tags):
    return [
        TargetEmployer(n.lower().replace(" ", "_").replace("&", "and").replace("/", "_"), n, (), category, tier, "", industry_tags=tags)
        for n in names
    ]

TARGET_EMPLOYERS = tuple([
    TargetEmployer("deloitte", "Deloitte", (), "ACCOUNTING_ADVISORY", 1, "https://jobs.deloitte.com", "WORKDAY", discovery_method="PUBLIC_CAREERS_SEARCH"),
    TargetEmployer("pwc", "PwC", ("PricewaterhouseCoopers",), "ACCOUNTING_ADVISORY", 1, "https://www.pwc.com/careers", "WORKDAY", discovery_method="PUBLIC_CAREERS_SEARCH"),
    TargetEmployer("ey", "EY", ("Ernst & Young",), "ACCOUNTING_ADVISORY", 1, "https://careers.ey.com", "WORKDAY", discovery_method="PUBLIC_CAREERS_SEARCH"),
    TargetEmployer("kpmg", "KPMG", ("KPMG UK",), "ACCOUNTING_ADVISORY", 1, "https://kpmg.com/careers", "WORKDAY", discovery_method="PUBLIC_CAREERS_SEARCH"),
    TargetEmployer("grant_thornton", "Grant Thornton", ("Grant Thornton UK LLP",), "ACCOUNTING_ADVISORY", 1, ""),
    TargetEmployer("cla", "CliftonLarsonAllen", ("CLA",), "ACCOUNTING_ADVISORY", 1, ""),
    *_entries("ACCOUNTING_ADVISORY", 1, ["BDO", "RSM", "Forvis Mazars", "Crowe", "Baker Tilly", "Nexia", "Moore Global", "PKF", "HLB"]),
    *_entries("CONSULTING", 2, ["Accenture", "Capgemini", "IBM Consulting", "BearingPoint", "Protiviti", "Alvarez & Marsal", "FTI Consulting", "Oliver Wyman", "Mercer", "Aon", "Marsh McLennan", "PA Consulting"]),
    TargetEmployer("palantir", "Palantir", (), "TECH_DATA", 2, "https://jobs.lever.co/palantir", "LEVER", "palantir", discovery_method="PUBLIC_STRUCTURED_ENDPOINT", enabled=True),
    TargetEmployer("databricks", "Databricks", (), "TECH_DATA", 2, "https://boards.greenhouse.io/databricks", "GREENHOUSE", "databricks", discovery_method="PUBLIC_STRUCTURED_ENDPOINT", enabled=True),
    *_entries("TECH_DATA", 2, ["Microsoft", "Amazon Web Services", "Google", "Oracle", "SAP", "Salesforce", "ServiceNow", "Workday", "IBM", "Snowflake", "Atlassian", "Xero", "Intuit", "Sage"]),
    *_entries("FINANCIAL_SERVICES", 2, ["JPMorgan Chase", "Goldman Sachs", "Morgan Stanley", "Citi", "Bank of America", "Barclays", "HSBC", "Lloyds Banking Group", "NatWest", "Standard Chartered", "UBS", "Macquarie", "BlackRock", "Fidelity", "Visa", "Mastercard", "PayPal", "Stripe", "Wise", "Revolut", "Block", "Commonwealth Bank", "Westpac", "ANZ", "NAB"]),
    TargetEmployer("pagegroup", "PageGroup", ("Michael Page", "Page Personnel"), "RECRUITER", 3, ""),
    *_entries("RECRUITER", 3, ["Robert Half", "Hays", "Robert Walters", "Morgan McKinley", "Investigo", "Marks Sattin", "Brewer Morris", "Cedar", "Goodman Masson"]),
    *_tagged_entries("FINANCIAL_SERVICES", 2, ["Adyen", "Checkout.com", "Klarna", "Airwallex", "Marqeta", "GoCardless"], ("FINTECH", "PAYMENTS")),
    *_tagged_entries("TECH_DATA", 2, ["BlackLine", "Anaplan", "OneStream", "Planful", "Pigment", "FloQast"], ("ACCOUNTING_TECH", "ERP_EPM", "FINANCE_AUTOMATION")),
    *_tagged_entries("TECH_DATA", 2, ["ComplyAdvantage", "Quantexa", "Featurespace", "Trulioo", "Riskified"], ("REGTECH", "RISK_COMPLIANCE_TECH")),
    *_tagged_entries("FINANCIAL_SERVICES", 2, ["Bloomberg", "LSEG", "S&P Global", "Moody's", "MSCI"], ("FINANCIAL_DATA",)),
])

def normalize_employer_name(value: str) -> str:
    text = (value or "").casefold().replace(".", "").strip()
    for employer in TARGET_EMPLOYERS:
        if text in {employer.name.casefold(), *(alias.casefold() for alias in employer.aliases)}:
            return employer.employer_id
    return text

def registry_summary():
    from collections import Counter
    return Counter(e.category for e in TARGET_EMPLOYERS), Counter(e.tier for e in TARGET_EMPLOYERS), Counter(e.ats_platform for e in TARGET_EMPLOYERS)


def industry_tag_summary():
    from collections import Counter
    return Counter(tag for employer in TARGET_EMPLOYERS for tag in employer.industry_tags)
