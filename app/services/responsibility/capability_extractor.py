import re


class CapabilityExtractor:
    """
    Extracts business capabilities from job responsibilities.
    """

    def __init__(self):

        self.capability_map = {

            # ===========================
            # Accounting
            # ===========================

            "month": ["Month End Close"],
            "close": ["Month End Close"],
            "month end": ["Month End Close"],
            "month-end": ["Month End Close"],

            "year end": ["Year End Close"],
            "year-end": ["Year End Close"],

            "financial statements": ["Financial Reporting"],
            "financial statement": ["Financial Reporting"],
            "financial reporting": ["Financial Reporting"],
            "reporting": ["Management Reporting"],
            "reports": ["Management Reporting"],
            "report": ["Management Reporting"],

            "general ledger": ["General Ledger"],
            "ledger": ["General Ledger"],

            "journal": ["Journal Entries"],
            "journals": ["Journal Entries"],

            "reconciliation": [
                "Bank Reconciliation",
                "Balance Sheet Reconciliation"
            ],

            "accounts payable": ["Accounts Payable"],
            "payables": ["Accounts Payable"],

            "accounts receivable": ["Accounts Receivable"],
            "receivables": ["Accounts Receivable"],

            "budget": ["Budgeting"],
            "budgeting": ["Budgeting"],

            "forecast": ["Forecasting"],
            "forecasting": ["Forecasting"],

            "variance": ["Variance Analysis"],

            "cash flow": [
                "Cash Flow Management",
                "Cash Flow Forecasting"
            ],

            # ===========================
            # Tax
            # ===========================

            "gst": ["GST"],

            "bas": ["BAS"],

            "tax": [
                "Tax Compliance",
                "Tax Planning"
            ],

            # ===========================
            # Audit
            # ===========================

            "audit": [
                "External Audit",
                "Internal Audit"
            ],

            "risk": ["Risk Assessment"],

            "compliance": ["Compliance"],

            "internal control": ["Internal Controls"],

            # ===========================
            # Finance
            # ===========================

            "analysis": ["Financial Analysis"],

            "analytical": ["Financial Analysis"],

            "financial modelling": ["Financial Modelling"],

            "model": ["Financial Modelling"],

            "project finance": ["Project Finance"],

            "business partner": ["Business Partnering"],

            "valuation": ["Business Valuation"],

            "feasibility": ["Feasibility Study"],

            # ===========================
            # Leadership
            # ===========================

            "lead": ["Leadership"],

            "leading": ["Leadership"],

            "led": ["Leadership"],

            "manage": ["Team Management"],

            "managed": ["Team Management"],

            "managing": ["Team Management"],

            "management": ["Team Management"],

            "stakeholder": ["Stakeholder Management"],

            "client": ["Client Relationship Management"],

            "mentor": ["Mentoring"],

            "coach": ["Coaching"],

            # ===========================
            # Technology
            # ===========================

            "power bi": ["Power BI"],

            "dashboard": [
                "Power BI",
                "Business Performance Analysis"
            ],

            "sql": ["SQL"],

            "python": ["Python"],

            "automation": ["Business Process Automation"],

            "workflow": ["Business Process Automation"]

        }

    # =======================================================
    # Extract
    # =======================================================

    def extract(self, responsibility):

        text = responsibility.lower()

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

        capabilities = set()

        for phrase, mapped in self.capability_map.items():

            if phrase in text:

                capabilities.update(mapped)

        return sorted(capabilities)