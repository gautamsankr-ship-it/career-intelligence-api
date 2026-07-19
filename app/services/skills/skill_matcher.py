from app.services.evidence_engine import EvidenceEngine


class SkillMatcher:
    """
    Skill Intelligence V3

    - Classifies skills by recruiter importance.
    - Uses Evidence Engine for semantic matching.
    - Returns weighted scores.
    """

    def __init__(self):

        self.evidence = EvidenceEngine()

    # ==========================================================
    # Skill Classification
    # ==========================================================

    def classify(self, skill):

        skill = skill.lower()

        tier1 = [

            "financial modelling",
            "financial modeling",
            "financial reporting",
            "management reporting",
            "financial analysis",
            "valuation",
            "business valuation",
            "financial due diligence",
            "commercial due diligence",
            "transaction advisory",
            "deal advisory",
            "project finance",
            "investment analysis",
            "corporate finance",
            "fp&a",
            "forecasting",
            "budgeting",
            "variance analysis",
            "cash flow",
            "internal controls",
            "risk assessment",
            "compliance",
            "ifrs",
            "gaap",
            "audit"

        ]

        tier2 = [

            "excel",
            "advanced excel",
            "power query",
            "power automate",
            "vba",
            "power bi",
            "tableau",
            "alteryx",
            "snowflake",
            "sql",
            "python",
            "xero",
            "quickbooks",
            "myob",
            "sap",
            "oracle",
            "netsuite",
            "odoo",
            "automation",
            "data analytics",
            "business intelligence"

        ]

        if any(x in skill for x in tier1):
            return 3

        if any(x in skill for x in tier2):
            return 2

        return 1

    # ==========================================================
    # Match Skills
    # ==========================================================

    def match(self, required_skills):

        matched = []

        missing = []

        total_weight = 0
        earned_weight = 0

        for skill in required_skills:

            if not skill.strip():
                continue

            tier = self.classify(skill)

            if tier == 3:
                weight = 5

            elif tier == 2:
                weight = 3

            else:
                weight = 1

            total_weight += weight

            evidence = self.evidence.evidence_score(skill)

            if evidence["score"] >= 7:

                earned_weight += weight

                matched.append({

                    "skill": skill,

                    "tier": tier,

                    "confidence": evidence["confidence"]

                })

            elif evidence["score"] >= 4:

                earned_weight += weight * 0.5

                matched.append({

                    "skill": skill,

                    "tier": tier,

                    "confidence": evidence["confidence"]

                })

            else:

                missing.append(skill)

        coverage = 0

        if total_weight:

            coverage = earned_weight / total_weight

        return {

            "coverage": coverage,

            "matched": matched,

            "missing": missing

        }