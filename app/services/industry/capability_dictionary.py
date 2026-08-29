"""
Master Industry Capability Dictionary

Every capability belongs to a capability family.

Industry Matcher,
ATS Engine,
Resume Optimizer,
Recruiter Reasoning

will all reuse this dictionary.
"""


CAPABILITY_FAMILIES = {

    # ==========================================================
    # ACCOUNTING
    # ==========================================================

    "Accounting": [

        "Financial Reporting",
        "Management Reporting",
        "Month End Close",
        "Year End Close",
        "Financial Statements",
        "General Ledger",
        "Journal Entries",
        "Accounts Payable",
        "Accounts Receivable",
        "Bank Reconciliation",
        "Balance Sheet Reconciliation",
        "Cash Flow Management",
        "Financial Controls",
        "Consolidation",
        "Statutory Reporting"

    ],

    # ==========================================================
    # AUDIT
    # ==========================================================

    "Audit": [

        "External Audit",
        "Internal Audit",
        "Risk Assessment",
        "Internal Controls",
        "Compliance",
        "Governance"

    ],

    # ==========================================================
    # TAX
    # ==========================================================

    "Tax": [

        "Tax Compliance",
        "GST",
        "BAS",
        "Income Tax",
        "Corporate Tax",
        "Tax Planning"

    ],

    # ==========================================================
    # CORPORATE FINANCE
    # ==========================================================

    "Corporate Finance": [

        "Financial Modelling",
        "Business Valuation",
        "DCF Valuation",
        "Financial Due Diligence",
        "Commercial Due Diligence",
        "Deal Advisory",
        "Transaction Advisory",
        "Mergers & Acquisitions",
        "Capital Raising",
        "Investment Analysis",
        "Capital IQ",
        "Pitchbook",
        "Business Advisory",
        "Corporate Development",
        "Deal Structuring",
        "Private Equity",
        "M&A Integration",
        "Post-Merger Integration"

    ],

    # ==========================================================
    # COMMERCIAL FINANCE
    # ==========================================================

    "Commercial Finance": [

        "Business Partnering",
        "Budgeting",
        "Forecasting",
        "Variance Analysis",
        "Financial Planning",
        "FP&A",
        "Commercial Accounting",
        "Commercial Finance",
        "Financial Analysis"

    ],

    # ==========================================================
    # PROJECT FINANCE
    # ==========================================================

    "Project Finance": [

        "Project Finance",
        "Infrastructure Finance",
        "Feasibility Study",
        "Project Evaluation",
        "Financial Advisory"

    ],

    # ==========================================================
    # ERP
    # ==========================================================

    "ERP": [

        "SAP",
        "Oracle ERP",
        "NetSuite",
        "Odoo",
        "Xero",
        "QuickBooks",
        "MYOB"

    ],

    # ==========================================================
    # DATA & ANALYTICS
    # ==========================================================

    "Data Analytics": [

        "Power BI",
        "Business Intelligence",
        "SQL",
        "Python",
        "Data Analytics",
        "Dashboard Development",
        "Automation",
        "Business Process Automation",
        "Blockchain",
        "Cryptocurrency",
        "Digital Assets",
        "Excel"

    ],

    # ==========================================================
    # LEADERSHIP
    # ==========================================================

    "Leadership": [

        "Leadership",
        "Team Management",
        "Stakeholder Management",
        "Client Relationship Management",
        "Coaching",
        "Mentoring"

    ],

    # ==========================================================
    # FORENSIC & INVESTIGATIONS
    # ==========================================================
    # Task 21.15E: real vacancies (forensic accounting, dispute resolution,
    # litigation support) had no home family at all -- every such
    # requirement fell back to an unmatchable singleton "family" regardless
    # of whether a candidate genuinely has this experience.

    "Forensic & Investigations": [

        "Forensic Accounting",
        "Investigations",
        "Fraud Investigation",
        "Litigation Support",
        "Expert Witness",
        "Dispute Resolution",
        "Asset Tracing"

    ],

    # ==========================================================
    # INSOLVENCY & RESTRUCTURING
    # ==========================================================

    "Insolvency & Restructuring": [

        "Insolvency",
        "Restructuring",
        "Turnaround",
        "Liquidation",
        "Voluntary Administration",
        "Receivership"

    ],

    # ==========================================================
    # RISK & COMPLIANCE
    # ==========================================================

    "Risk & Compliance": [

        "Transaction Monitoring",
        "Anti-Money Laundering",
        "Know Your Customer",
        "Sanctions Screening",
        "Regulatory Compliance",
        "Financial Crime"

    ],

    # ==========================================================
    # INSURANCE & CLAIMS
    # ==========================================================

    "Insurance & Claims": [

        "Insurance",
        "Claims Assessment",
        "Loss Adjusting",
        "Underwriting"

    ]

}