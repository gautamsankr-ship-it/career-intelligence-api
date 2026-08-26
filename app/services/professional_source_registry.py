"""Curated professional/specialist job-source intelligence; no scraping here."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfessionalJobSource:
    source_id: str
    name: str
    category: str
    priority: int
    discovery_method: str
    status: str
    base_url: str
    notes: str


# These sources are valuable for research and manual use, but none is enabled
# until it offers a verified, bounded public discovery interface that does not
# require account credentials or circumvent access controls.
PROFESSIONAL_JOB_SOURCES = (
    ProfessionalJobSource("acca", "ACCA Careers", "PROFESSIONAL_ACCOUNTING", 1, "PUBLIC_SEARCH_INTERFACE", "DIAGNOSTIC_ONLY", "https://jobs.accaglobal.com/jobs/", "Public finance/accounting board; direct automated access returned 403 during metadata verification."),
    ProfessionalJobSource("icaew", "ICAEW Jobs", "PROFESSIONAL_ACCOUNTING", 1, "PUBLIC_SEARCH_INTERFACE", "DIAGNOSTIC_ONLY", "https://jobs.icaew.com", "Dedicated ACA/accountancy board; no verified bounded public listing endpoint."),
    ProfessionalJobSource("efinancialcareers", "eFinancialCareers", "FINANCIAL_SERVICES", 2, "PUBLIC_SEARCH_INTERFACE", "DIAGNOSTIC_ONLY", "https://www.efinancialcareers.com/jobs/search", "Public search exists, but documented job API is recruiter-authenticated; no scraper is enabled."),
    ProfessionalJobSource("aicpa_cima", "AICPA-CIMA Career Resources", "PROFESSIONAL_ACCOUNTING", 2, "UNKNOWN", "UNSUPPORTED", "https://www.aicpa-cima.com", "No verified public, bounded vacancy feed identified."),
    ProfessionalJobSource("accountingweb", "AccountingWEB Jobs", "SPECIALIST_FINANCE", 3, "UNKNOWN", "UNSUPPORTED", "https://www.accountingweb.co.uk", "No verified stable public vacancy interface identified."),
)


def source_summary():
    from collections import Counter
    return Counter(source.status for source in PROFESSIONAL_JOB_SOURCES)
