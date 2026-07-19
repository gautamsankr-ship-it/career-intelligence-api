"""
Profile Intelligence Capability Graph

This graph defines professional capability relationships.

The graph is directional.

Example

Hydropower Financial DDA
        ↓
Financial Due Diligence
        ↓
Corporate Finance
        ↓
Project Finance
"""

CAPABILITY_GRAPH = {

    # ==========================================================
    # Accounting
    # ==========================================================

    "Financial Reporting": [

        "Management Reporting",
        "Financial Statements",
        "Month End Close",
        "Year End Close",
        "General Ledger",
        "Financial Controls"

    ],

    "Management Reporting": [

        "Commercial Finance",
        "Business Partnering",
        "FP&A"

    ],

    "Bank Reconciliation": [

        "Financial Controls",
        "Cash Management"

    ],

    # ==========================================================
    # Audit
    # ==========================================================

    "External Audit": [

        "Risk Assessment",
        "Compliance",
        "Internal Controls"

    ],

    "Internal Audit": [

        "Risk Assessment",
        "Governance",
        "Compliance"

    ],

    # ==========================================================
    # Corporate Finance
    # ==========================================================

    "Financial Modelling": [

        "Business Valuation",
        "DCF Valuation",
        "Investment Analysis",
        "Project Finance"

    ],

    "Financial Due Diligence": [

        "Corporate Finance",
        "Commercial Due Diligence",
        "Deal Advisory",
        "Transaction Advisory"

    ],

    "Business Valuation": [

        "Corporate Finance",
        "Investment Analysis"

    ],

    "Project Finance": [

        "Infrastructure Finance",
        "Feasibility Study",
        "Financial Advisory"

    ],

    "Infrastructure Finance": [

        "Renewable Energy",
        "Hydropower",
        "Energy Finance"

    ],

    # ==========================================================
    # Commercial Finance
    # ==========================================================

    "Budgeting": [

        "Forecasting",
        "Variance Analysis",
        "FP&A"

    ],

    "Forecasting": [

        "FP&A",
        "Commercial Finance"

    ],

    "Variance Analysis": [

        "Commercial Finance",
        "Business Partnering"

    ],

    # ==========================================================
    # Analytics
    # ==========================================================

    "Power BI": [

        "Business Intelligence",
        "Dashboard Development",
        "Data Analytics"

    ],

    "SQL": [

        "Data Analytics"

    ],

    "Python": [

        "Automation",
        "Business Process Automation",
        "Artificial Intelligence"

    ],

    "Automation": [

        "Artificial Intelligence",
        "Digital Transformation"

    ],

    # ==========================================================
    # ERP
    # ==========================================================

    "Xero": [

        "Cloud Accounting"

    ],

    "QuickBooks": [

        "Cloud Accounting"

    ],

    "SAP": [

        "ERP"

    ],

    "Oracle ERP": [

        "ERP"

    ],

    "Odoo": [

        "ERP"

    ],

    # ==========================================================
    # Leadership
    # ==========================================================

    "Leadership": [

        "People Management",
        "Coaching",
        "Mentoring",
        "Stakeholder Management"

    ],

    "Team Management": [

        "Leadership",
        "Performance Management"

    ],

    "Stakeholder Management": [

        "Client Relationship Management",
        "Executive Communication"

    ]

}