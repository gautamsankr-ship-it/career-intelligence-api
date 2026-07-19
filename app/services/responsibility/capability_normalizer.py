class CapabilityNormalizer:
    """
    Converts extracted capabilities into canonical names.

    Example

    Financial Statements
            ↓
    Financial Reporting
    """

    def __init__(self):

        self.mapping = {

            # ==========================================
            # Accounting
            # ==========================================

            "financial statements": "Financial Reporting",

            "financial reporting": "Financial Reporting",

            "management reporting": "Management Reporting",

            "month end": "Month End Close",

            "month-end": "Month End Close",

            "month end close": "Month End Close",

            "financial close": "Month End Close",

            "year end": "Year End Close",

            "year-end": "Year End Close",

            "journal": "Journal Entries",

            "journals": "Journal Entries",

            "ledger": "General Ledger",

            "general ledger": "General Ledger",

            "ap": "Accounts Payable",

            "accounts payable": "Accounts Payable",

            "ar": "Accounts Receivable",

            "accounts receivable": "Accounts Receivable",

            "cashflow": "Cash Flow Management",

            "cash flow": "Cash Flow Management",

            # ==========================================
            # Finance
            # ==========================================

            "forecast": "Forecasting",

            "forecasting": "Forecasting",

            "budget": "Budgeting",

            "budgeting": "Budgeting",

            "variance": "Variance Analysis",

            "analysis": "Financial Analysis",

            "financial analysis": "Financial Analysis",

            "financial modelling": "Financial Modelling",

            "modeling": "Financial Modelling",

            "valuation": "Business Valuation",

            "feasibility": "Feasibility Study",

            # ==========================================
            # Tax
            # ==========================================

            "gst": "GST",

            "bas": "BAS",

            "tax": "Tax Compliance",

            "taxation": "Tax Compliance",

            # ==========================================
            # Audit
            # ==========================================

            "audit": "External Audit",

            "internal audit": "Internal Audit",

            "risk": "Risk Assessment",

            "compliance": "Compliance",

            "internal control": "Internal Controls",

            # ==========================================
            # Leadership
            # ==========================================

            "lead": "Leadership",

            "leadership": "Leadership",

            "manage": "Team Management",

            "management": "Team Management",

            "stakeholder": "Stakeholder Management",

            "client": "Client Relationship Management",

            "mentor": "Mentoring",

            "coach": "Coaching",

            # ==========================================
            # Analytics
            # ==========================================

            "dashboard": "Power BI",

            "power bi": "Power BI",

            "sql": "SQL",

            "python": "Python",

            "automation": "Business Process Automation"

        }

    # =====================================================
    # Normalize
    # =====================================================

    def normalize(self, capability: str) -> str:

        if not capability:

            return ""

        key = capability.lower().strip()

        return self.mapping.get(

            key,

            capability

        )

    # =====================================================
    # Normalize Multiple
    # =====================================================

    def normalize_all(self, capabilities):

        normalized = []

        for capability in capabilities:

            normalized.append(

                self.normalize(capability)

            )

        return sorted(

            list(set(normalized))

        )