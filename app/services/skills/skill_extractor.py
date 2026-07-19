import re


class SkillExtractor:
    """
    Extracts canonical professional skills from job requirements.
    """

    def __init__(self):

        self.skill_map = {

            # ==================================================
            # Accounting
            # ==================================================

            "financial reporting": ["Financial Reporting"],
            "financial statements": ["Financial Reporting"],
            "management reporting": ["Management Reporting"],
            "month end": ["Month End Close"],
            "month-end": ["Month End Close"],
            "year end": ["Year End Close"],
            "year-end": ["Year End Close"],
            "general ledger": ["General Ledger"],
            "journal": ["Journal Entries"],
            "reconciliation": [
                "Bank Reconciliation",
                "Balance Sheet Reconciliation"
            ],
            "accounts payable": ["Accounts Payable"],
            "accounts receivable": ["Accounts Receivable"],
            "payables": ["Accounts Payable"],
            "receivables": ["Accounts Receivable"],

            # ==================================================
            # Tax
            # ==================================================

            "tax": [
                "Tax Compliance",
                "Tax Planning"
            ],
            "gst": ["GST"],
            "bas": ["BAS"],

            # ==================================================
            # Audit
            # ==================================================

            "audit": [
                "External Audit",
                "Internal Audit"
            ],
            "risk": ["Risk Assessment"],
            "compliance": ["Compliance"],
            "internal control": ["Internal Controls"],

            # ==================================================
            # Corporate Finance
            # ==================================================

            "financial modelling": ["Financial Modelling"],
            "financial modeling": ["Financial Modelling"],
            "modelling": ["Financial Modelling"],
            "modeling": ["Financial Modelling"],

            "valuation": [
                "Business Valuation",
                "DCF Valuation"
            ],

            "due diligence": [
                "Financial Due Diligence"
            ],

            "deal advisory": [
                "Deal Advisory"
            ],

            "transaction services": [
                "Transaction Advisory"
            ],

            "project finance": [
                "Project Finance"
            ],

            "investment analysis": [
                "Investment Analysis"
            ],

            "feasibility": [
                "Feasibility Study"
            ],

            "budget": ["Budgeting"],
            "forecast": ["Forecasting"],
            "variance": ["Variance Analysis"],
            "cash flow": [
                "Cash Flow Management",
                "Cash Flow Forecasting"
            ],

            # ==================================================
            # ERP / Accounting Systems
            # ==================================================

            "xero": ["Xero"],
            "quickbooks": ["QuickBooks"],
            "myob": ["MYOB"],
            "sap": ["SAP"],
            "oracle": ["Oracle ERP"],
            "netsuite": ["NetSuite"],
            "odoo": ["Odoo"],

            # ==================================================
            # Analytics
            # ==================================================

            "excel": ["Microsoft Excel"],
            "power bi": ["Power BI"],
            "sql": ["SQL"],
            "python": ["Python"],
            "dashboard": [
                "Power BI",
                "Business Intelligence"
            ],
            "analytics": [
                "Business Intelligence"
            ],
            "automation": [
                "Business Process Automation"
            ],

            # ==================================================
            # Leadership
            # ==================================================

            "lead": ["Leadership"],
            "manage": ["Team Management"],
            "stakeholder": ["Stakeholder Management"],
            "client": ["Client Relationship Management"],
            "mentor": ["Mentoring"],
            "coach": ["Coaching"]

        }

    # ==========================================================
    # Extract Skills
    # ==========================================================

    def extract(self, text):

        if not text:

            return []

        text = text.lower()

        text = re.sub(

            r"[^a-z0-9 ]",

            " ",

            text

        )

        text = re.sub(

            r"\s+",

            " ",

            text

        ).strip()

        skills = set()

        for phrase, mapped in self.skill_map.items():

            if phrase in text:

                skills.update(mapped)

        return sorted(skills)