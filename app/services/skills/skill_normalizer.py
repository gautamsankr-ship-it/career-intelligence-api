class SkillNormalizer:
    """
    Converts extracted skills into canonical professional skills.
    """

    def __init__(self):

        self.mapping = {

            # ==================================================
            # Accounting
            # ==================================================

            "financial statements": "Financial Reporting",
            "financial statement": "Financial Reporting",
            "financial reporting": "Financial Reporting",

            "management reporting": "Management Reporting",

            "month end": "Month End Close",
            "month-end": "Month End Close",
            "month end close": "Month End Close",
            "financial close": "Month End Close",

            "year end": "Year End Close",
            "year-end": "Year End Close",

            "general ledger": "General Ledger",
            "ledger": "General Ledger",

            "journal": "Journal Entries",
            "journals": "Journal Entries",

            "ap": "Accounts Payable",
            "accounts payable": "Accounts Payable",

            "ar": "Accounts Receivable",
            "accounts receivable": "Accounts Receivable",

            "cashflow": "Cash Flow Management",
            "cash flow": "Cash Flow Management",

            # ==================================================
            # Finance
            # ==================================================

            "budget": "Budgeting",
            "budgeting": "Budgeting",

            "forecast": "Forecasting",
            "forecasting": "Forecasting",

            "variance": "Variance Analysis",

            "analysis": "Financial Analysis",
            "financial analysis": "Financial Analysis",

            "financial modelling": "Financial Modelling",
            "financial modeling": "Financial Modelling",
            "modeling": "Financial Modelling",
            "modelling": "Financial Modelling",

            "valuation": "Business Valuation",
            "business valuation": "Business Valuation",

            "dcf": "DCF Valuation",

            "due diligence": "Financial Due Diligence",

            "deal advisory": "Deal Advisory",

            "transaction advisory": "Transaction Advisory",
            "transaction services": "Transaction Advisory",

            "project finance": "Project Finance",

            "investment analysis": "Investment Analysis",

            "feasibility": "Feasibility Study",

            # ==================================================
            # Tax
            # ==================================================

            "gst": "GST",
            "bas": "BAS",

            "tax": "Tax Compliance",
            "taxation": "Tax Compliance",

            # ==================================================
            # Audit
            # ==================================================

            "audit": "External Audit",
            "external audit": "External Audit",
            "internal audit": "Internal Audit",

            "risk": "Risk Assessment",

            "compliance": "Compliance",

            "internal control": "Internal Controls",

            # ==================================================
            # ERP
            # ==================================================

            "xero": "Xero",
            "quickbooks": "QuickBooks",
            "myob": "MYOB",
            "sap": "SAP",
            "oracle": "Oracle ERP",
            "netsuite": "NetSuite",
            "odoo": "Odoo",

            # ==================================================
            # Analytics
            # ==================================================

            "excel": "Microsoft Excel",
            "microsoft excel": "Microsoft Excel",

            "power bi": "Power BI",

            "sql": "SQL",

            "python": "Python",

            "dashboard": "Business Intelligence",

            "analytics": "Business Intelligence",

            "automation": "Business Process Automation",

            # ==================================================
            # Leadership
            # ==================================================

            "lead": "Leadership",
            "leadership": "Leadership",

            "manage": "Team Management",
            "managed": "Team Management",
            "management": "Team Management",

            "stakeholder": "Stakeholder Management",

            "client": "Client Relationship Management",

            "mentor": "Mentoring",

            "coach": "Coaching"

        }

    # ==========================================================
    # Normalize One Skill
    # ==========================================================

    def normalize(self, skill):

        if not skill:

            return ""

        key = skill.lower().strip()

        return self.mapping.get(

            key,

            skill

        )

    # ==========================================================
    # Normalize Multiple Skills
    # ==========================================================

    def normalize_all(self, skills):

        normalized = []

        for skill in skills:

            normalized.append(

                self.normalize(skill)

            )

        return sorted(

            list(set(normalized))

        )