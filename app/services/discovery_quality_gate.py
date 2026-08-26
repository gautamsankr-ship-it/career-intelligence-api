"""Deterministic discovery admission checks, independent of CareerDecision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable

from app.models.career_opportunity import CareerOpportunity


DISCOVERY_RELEVANT = "DISCOVERY_RELEVANT"
DISCOVERY_IRRELEVANT = "DISCOVERY_IRRELEVANT"
DISCOVERY_AMBIGUOUS = "DISCOVERY_AMBIGUOUS"

CORE_FINANCE = "CORE_FINANCE"
FINANCE_TECH = "FINANCE_TECH"
BOTH = "BOTH"
CAREER_TRACK_UNKNOWN = "UNKNOWN"

FRESH = "FRESH"
STALE = "STALE"
FRESHNESS_UNKNOWN = "FRESHNESS_UNKNOWN"
MAX_JOB_AGE_DAYS = 7

PROFESSIONAL_SERVICE_SECTORS = (
    "accounting",
    "chartered accountancy",
    "audit",
    "assurance",
    "tax",
    "tax advisory",
    "risk advisory",
    "internal audit",
    "governance",
    "controls",
    "business advisory",
    "cfo advisory",
    "finance transformation",
    "management consulting",
    "outsourced finance",
    "virtual cfo",
    "bookkeeping",
    "corporate finance",
    "transaction advisory",
    "transaction services",
    "deal advisory",
    "restructuring",
    "turnaround",
    "financial due diligence",
    "forensic accounting",
    "regulatory compliance",
)

FINANCE_ROLE_TERMS = (
    "accountant", "accounting", "fp&a", "financial analyst", "finance manager",
    "finance business partner", "financial controller", "finance controller",
    "financial reporting", "management accounting", "commercial finance",
    "treasury", "corporate finance", "finance transformation",
)

# Each theme requires explicit finance/accounting/risk context in the title or
# vacancy text. Technology words alone never make a listing relevant.
FINANCE_TECH_THEMES = {
    "FINANCE_TRANSFORMATION": ("finance transformation", "digital finance", "finance process transformation", "finance function transformation"),
    "FINANCE_SYSTEMS": ("finance systems", "financial systems", "finance applications", "financial reporting systems"),
    "FINANCE_AUTOMATION": ("finance automation", "financial process automation", "accounting automation", "finance digitalisation"),
    "FINANCIAL_DATA_ANALYTICS": ("financial data", "finance data", "finance analytics", "financial analytics", "fp&a analytics", "finance business intelligence"),
    "FINTECH": ("fintech", "financial technology", "finance product", "financial product", "financial operations"),
    "REGTECH_RISK": ("regulatory technology", "compliance technology", "risk technology", "risk systems", "grc technology", "aml technology", "kyc technology", "regulatory reporting technology"),
    "ACCOUNTING_TECH": ("accounting technology", "accounting systems", "financial close technology", "record-to-report", "general ledger systems", "digital accounting"),
    "ERP_EPM": ("sap fico", "sap finance", "oracle financials", "oracle epm", "workday financial management", "dynamics 365 finance", "netsuite", "anaplan", "onestream", "planful", "pigment", "blackline", "fp&a systems"),
    "PAYMENTS_BANKING_TECH": ("payment technology", "payments operations", "payment infrastructure", "financial infrastructure", "open banking", "banking technology", "treasury technology"),
    "FINANCE_TECH_ADVISORY": ("finance technology consultant", "finance implementation consultant", "finance solutions consultant", "erp advisory", "digital audit", "technology consulting - finance"),
}

GENERIC_TECH_TITLE_TERMS = (
    "software engineer", "backend engineer", "frontend engineer", "devops engineer",
    "cloud engineer", "machine learning engineer", "ai engineer", "data engineer",
    "cybersecurity engineer", "network engineer", "database administrator", "data scientist",
)

PROFESSIONAL_ROLE_TERMS = (
    "audit manager", "senior auditor", "internal audit manager", "tax manager",
    "tax consultant", "risk advisory", "risk manager", "business advisory",
    "accounting advisory", "cfo advisory", "outsourced cfo", "outsourced finance",
    "client accounting", "transaction services", "deals advisory",
    "financial due diligence", "forensic accountant", "restructuring",
)

IRRELEVANT_TITLE_TERMS = (
    "mortgage advisor", "mortgage adviser", "insurance lawyer", "insurance senior associate",
    "investment sales", "investment advisor", "investment adviser", "sales executive",
    "business development", "relationship manager", "lawyer", "legal counsel",
)


@dataclass(frozen=True)
class DiscoveryRelevance:
    classification: str
    professional_services_context: bool
    professional_services_sector: str
    evidence: str
    career_track: str = CAREER_TRACK_UNKNOWN
    opportunity_themes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscoveryAdmission:
    admitted: list[CareerOpportunity]
    relevance_counts: dict[str, int]
    freshness_counts: dict[str, int]
    professional_services_relevant: int


class DiscoveryQualityGate:
    """Reject obviously off-domain or stale listings before CareerDecision."""

    def classify_relevance(self, job: CareerOpportunity) -> DiscoveryRelevance:
        title = (job.job_title or "").casefold()
        context = " ".join((job.company or "", job.job_description or "", str(job.metadata or ""))).casefold()
        sector = next((term for term in PROFESSIONAL_SERVICE_SECTORS if term in f"{title} {context}"), "")
        combined = f"{title} {context}"
        themes = tuple(key for key, terms in FINANCE_TECH_THEMES.items() if any(term in combined for term in terms))
        professional_firm_context = any(term in context for term in (
            "accounting firm", "accounting practice", "advisory firm", "audit firm",
            "assurance practice", "professional services", "consulting firm",
        ))

        excluded = next((term for term in IRRELEVANT_TITLE_TERMS if term in title), "")
        if excluded:
            return DiscoveryRelevance(DISCOVERY_IRRELEVANT, False, "", f"excluded title: {excluded}")

        generic_technology = next((term for term in GENERIC_TECH_TITLE_TERMS if term in title), "")
        if generic_technology:
            return DiscoveryRelevance(DISCOVERY_IRRELEVANT, False, "", f"excluded generic technology title: {generic_technology}")

        if themes:
            # Finance transformation inside an accounting/advisory context is
            # a genuine bridge role. Otherwise label finance-tech directly.
            traditional_finance = any(
                term in title for term in FINANCE_ROLE_TERMS if term != "finance transformation"
            )
            track = BOTH if professional_firm_context or traditional_finance else FINANCE_TECH
            return DiscoveryRelevance(
                DISCOVERY_RELEVANT, bool(sector), sector,
                f"finance-tech theme: {', '.join(themes)}", track, themes,
            )

        direct = next((term for term in FINANCE_ROLE_TERMS if term in title), "")
        if direct:
            return DiscoveryRelevance(DISCOVERY_RELEVANT, bool(sector), sector, f"finance role: {direct}", CORE_FINANCE)

        professional_role = next((term for term in PROFESSIONAL_ROLE_TERMS if term in title), "")
        if professional_role and (sector or professional_role != "risk manager"):
            return DiscoveryRelevance(
                DISCOVERY_RELEVANT, bool(sector), sector, f"professional-services role: {professional_role}", CORE_FINANCE
            )
        if sector and any(term in title for term in ("manager", "consultant", "advisor", "adviser", "auditor", "accountant")):
            return DiscoveryRelevance(DISCOVERY_RELEVANT, True, sector, f"sector context: {sector}", CORE_FINANCE)
        if sector:
            return DiscoveryRelevance(DISCOVERY_AMBIGUOUS, True, sector, f"sector without relevant role: {sector}")
        return DiscoveryRelevance(DISCOVERY_AMBIGUOUS, False, "", "no finance or professional-services evidence")

    @staticmethod
    def freshness(job: CareerOpportunity, today: date | None = None) -> str:
        value = (job.posted_date or "").strip()
        if not value:
            return FRESHNESS_UNKNOWN
        try:
            posted = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return FRESHNESS_UNKNOWN
        age = (today or date.today()) - posted
        return FRESH if age.days <= MAX_JOB_AGE_DAYS else STALE

    def admit(self, jobs: Iterable[CareerOpportunity], today: date | None = None) -> DiscoveryAdmission:
        relevance_counts = {
            DISCOVERY_RELEVANT: 0,
            DISCOVERY_IRRELEVANT: 0,
            DISCOVERY_AMBIGUOUS: 0,
        }
        freshness_counts = {FRESH: 0, STALE: 0, FRESHNESS_UNKNOWN: 0}
        admitted: list[CareerOpportunity] = []
        professional_services_relevant = 0

        for job in jobs:
            relevance = self.classify_relevance(job)
            relevance_counts[relevance.classification] += 1
            job.metadata["discovery_relevance"] = relevance.classification
            job.metadata["professional_services_context"] = relevance.professional_services_context
            job.metadata["professional_services_sector"] = relevance.professional_services_sector
            job.metadata["discovery_relevance_evidence"] = relevance.evidence
            job.metadata["career_track"] = relevance.career_track
            job.metadata["opportunity_themes"] = list(relevance.opportunity_themes)
            if relevance.classification != DISCOVERY_RELEVANT:
                continue
            if relevance.professional_services_context:
                professional_services_relevant += 1
            freshness = self.freshness(job, today)
            freshness_counts[freshness] += 1
            job.metadata["freshness"] = freshness
            if freshness != STALE:
                admitted.append(job)

        return DiscoveryAdmission(admitted, relevance_counts, freshness_counts, professional_services_relevant)
