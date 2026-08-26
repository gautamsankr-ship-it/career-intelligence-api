"""Conservative vacancy-level remote-work eligibility for the current candidate."""

from __future__ import annotations

from dataclasses import dataclass


ELIGIBLE = "ELIGIBLE"
INELIGIBLE = "INELIGIBLE"
MANUAL_REVIEW = "MANUAL_REVIEW"
NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class RemoteEligibilityResult:
    decision: str
    scope: str
    reason: str
    evidence: str


class RemoteWorkEligibilityClassifier:
    """Use explicit worker-location evidence; never infer it from search market."""

    def classify(self, opportunity) -> RemoteEligibilityResult:
        arrangement = (getattr(opportunity, "work_arrangement", "") or "").upper()
        if arrangement != "REMOTE" and getattr(opportunity, "remote_status", None) is not True:
            return RemoteEligibilityResult(NOT_APPLICABLE, "REMOTE_NOT_APPLICABLE", "Vacancy is not confirmed remote", "")
        text = " ".join((getattr(opportunity, "job_title", "") or "", getattr(opportunity, "job_description", "") or "")).casefold()

        restrictions = (
            ("uk residents only", "UK residence required"), ("must be based in the uk", "UK residence required"),
            ("must reside in the united kingdom", "UK residence required"), ("remote - uk only", "UK-only remote"),
            ("remote in the uk only", "UK-only remote"), ("remote within the uk", "UK-only remote"),
            ("australia-based applicants only", "Australian residence required"), ("must reside in australia", "Australian residence required"),
            ("remote within australia", "Australia-only remote"), ("full australian working rights", "Australian work rights required"),
            ("us remote only", "US-only remote"), ("remote anywhere in the us", "US-only remote"),
            ("must be located in the united states", "US residence required"), ("eu residents only", "EU residence required"),
            ("eea candidates only", "EEA residence required"), ("right to work in the uk", "UK work authorization required"),
            ("uk work authorization", "UK work authorization required"), ("us work authorization", "US work authorization required"),
            ("authorized to work in the united states", "US work authorization required"),
            ("australian working rights", "Australian work rights required"), ("us citizen", "US citizenship required"),
            ("australian citizen", "Australian citizenship required"), ("uk security clearance", "UK security clearance required"),
            ("us security clearance", "US security clearance required"), ("security clearance required", "Security clearance requirement needs incompatible status"),
        )
        for phrase, reason in restrictions:
            if phrase in text:
                return RemoteEligibilityResult(INELIGIBLE, "REMOTE_COUNTRY_RESTRICTED", reason, phrase)

        global_terms = ("work from anywhere", "worldwide remote", "global remote", "remote worldwide", "work from any country", "location independent", "international remote", "open globally", "remote from nepal", "nepal-based remote", "international contractors accepted", "contractors may work from any country")
        evidence = next((term for term in global_terms if term in text), "")
        if evidence:
            return RemoteEligibilityResult(ELIGIBLE, "REMOTE_GLOBAL", "Explicit worldwide/Nepal remote eligibility", evidence)
        return RemoteEligibilityResult(MANUAL_REVIEW, "REMOTE_ELIGIBILITY_UNCLEAR", "Remote vacancy but geographic eligibility not stated", "")
