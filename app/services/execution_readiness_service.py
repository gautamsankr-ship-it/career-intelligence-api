"""Derived execution readiness for the existing application funnel.

This is deliberately a read model.  It does not add a CRM stage, change a
priority, or create a second task queue.  It translates the existing package,
answer, eligibility, and human-blocker evidence into one operational view.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.services.application_answer_engine import ApplicationAnswerEngine
from app.services.application_package_orchestrator import ApplicationPackageOrchestrator, TERMINAL
from app.services.application_eligibility_policy import intelligence_priority_gate


READY_FOR_AUTOMATION = "READY_FOR_AUTOMATION"
READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
WAITING_FOR_ANSWER = "WAITING_FOR_ANSWER"
WAITING_FOR_ELIGIBILITY = "WAITING_FOR_ELIGIBILITY_DECISION"
WAITING_FOR_BROWSER = "WAITING_FOR_BROWSER_ACTION"
WAITING_FOR_FINAL_APPROVAL = "WAITING_FOR_FINAL_APPROVAL"
NOT_EXECUTABLE = "NOT_EXECUTABLE"
COMPLETED = "COMPLETED"

BLOCKER_TAXONOMY = {
    "HUMAN_ELIGIBILITY_REVIEW_REQUIRED": "ELIGIBILITY",
    "HUMAN_ANSWER_APPROVAL_REQUIRED": "SCREENING_ANSWER",
    "HUMAN_SALARY_REVIEW_REQUIRED": "SALARY",
    "HUMAN_LOGIN_REQUIRED": "LOGIN",
    "HUMAN_MFA_REQUIRED": "MFA",
    "HUMAN_CAPTCHA_REQUIRED": "CAPTCHA",
    "HUMAN_BROWSER_VERIFICATION_REQUIRED": "BROWSER_VERIFICATION",
    "HUMAN_DOCUMENT_REVIEW_REQUIRED": "DOCUMENT_REVIEW",
    "READY_FOR_HUMAN_SUBMIT": "FINAL_SUBMIT_AUTHORIZATION",
    "HUMAN_FINAL_SUBMIT_AUTHORIZATION_REQUIRED": "FINAL_SUBMIT_AUTHORIZATION",
    "EMPLOYER_ACTION_REQUIRED": "EMPLOYER_COMMUNICATION",
    "OTHER": "OTHER_CONSEQUENTIAL_UNKNOWN",
}


@dataclass
class ExecutionReadiness:
    tracker_id: int
    state: str
    label: str
    blocker_category: str | None = None
    reason: str = ""
    auto_resolvable: bool = False
    user_action_required: bool = False
    reusable: bool = False
    evidence_checked: list[str] = field(default_factory=list)
    next_safe_action: str = ""
    priority: str | None = None
    score: float | None = None

    @property
    def can_automate(self) -> bool:
        return self.state == READY_FOR_AUTOMATION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_LABELS = {
    READY_FOR_AUTOMATION: "Ready for Automation",
    READY_FOR_HUMAN_REVIEW: "Ready for Human Review",
    WAITING_FOR_ANSWER: "Waiting for Answer",
    WAITING_FOR_ELIGIBILITY: "Waiting for Eligibility Decision",
    WAITING_FOR_BROWSER: "Waiting for Browser Action",
    WAITING_FOR_FINAL_APPROVAL: "Waiting for Final Approval",
    NOT_EXECUTABLE: "Not Executable",
    COMPLETED: "Completed",
}


class ExecutionReadinessService:
    """One shared, conservative read model over existing evidence."""

    def __init__(self, crm, package_service=None, answer_engine=None):
        self.crm = crm
        self.package_service = package_service or ApplicationPackageOrchestrator(history=crm.history)
        vault = getattr(self.package_service, "vault", None)
        self.answer_engine = answer_engine or ApplicationAnswerEngine(vault)

    def for_tracker(self, tracker_id: int) -> ExecutionReadiness:
        record = self.crm.get_opportunity(tracker_id)
        if not record:
            return ExecutionReadiness(tracker_id, NOT_EXECUTABLE, _LABELS[NOT_EXECUTABLE], reason="Opportunity was not found.", user_action_required=False, next_safe_action="Verify the opportunity record.")
        priority = record.get("intelligence_priority")
        base = dict(tracker_id=tracker_id, priority=priority, score=record.get("career_score"))
        checked = ["application_history", "intelligence_priority"]
        if record.get("crm_stage") in {"APPLIED", "INTERVIEW_1", "INTERVIEW_2", "FINAL_INTERVIEW", "OFFER", "ACCEPTED", "HIRED"} or record.get("application_status") in TERMINAL:
            return self._result(COMPLETED, "Application has already reached a terminal or outcome stage.", next_safe_action="Monitor the recorded application outcome.", **base)
        blockers = self.crm.list_open_blockers(tracker_id)
        if blockers:
            blocker = blockers[0]
            raw = blocker.get("blocker_type") or "OTHER"
            category = BLOCKER_TAXONOMY.get(raw, "OTHER_CONSEQUENTIAL_UNKNOWN")
            if category == "ELIGIBILITY":
                state = WAITING_FOR_ELIGIBILITY
            elif category in {"LOGIN", "MFA", "CAPTCHA", "BROWSER_VERIFICATION"}:
                state = WAITING_FOR_BROWSER
            elif category == "FINAL_SUBMIT_AUTHORIZATION":
                state = WAITING_FOR_FINAL_APPROVAL
            else:
                state = WAITING_FOR_ANSWER
            return self._result(state, blocker.get("detail") or raw, blocker_category=category, user_action_required=True,
                                reusable=category in {"SCREENING_ANSWER", "DOCUMENT_REVIEW"},
                                evidence_checked=checked + ["human_blockers"],
                                next_safe_action=self._next_action(category), **base)
        if record.get("remote_eligibility") == "MANUAL_REVIEW" or record.get("crm_stage") == "ELIGIBILITY_REVIEW":
            return self._result(WAITING_FOR_ELIGIBILITY, record.get("remote_eligibility_reason") or "Eligibility evidence requires a decision.",
                                blocker_category="ELIGIBILITY", user_action_required=True, evidence_checked=checked + ["eligibility_policy"],
                                next_safe_action="Review the jurisdiction-specific work-right evidence.", **base)
        if priority not in {"A", "B"}:
            if priority == "C":
                return self._result(READY_FOR_HUMAN_REVIEW, "Priority C requires human review before consequential execution.", blocker_category="OTHER_CONSEQUENTIAL_UNKNOWN",
                                    user_action_required=True, evidence_checked=checked + ["priority_policy"], next_safe_action="Review the opportunity; do not change its priority to unlock execution.", **base)
            return self._result(NOT_EXECUTABLE, "The frozen intelligence gate does not authorize application execution.", evidence_checked=checked + ["priority_policy"], next_safe_action="No application action.", **base)
        if intelligence_priority_gate(record):
            return self._result(NOT_EXECUTABLE, "The frozen eligibility/priority gate does not authorize application execution.", evidence_checked=checked + ["eligibility_policy"], next_safe_action="Resolve the underlying evidence through the existing workflow.", **base)
        package = self.package_service.load(tracker_id)
        if not package:
            return self._result(READY_FOR_HUMAN_REVIEW, "An application package has not yet been prepared.", user_action_required=False,
                                evidence_checked=checked + ["application_package"], next_safe_action="Prepare the existing application package.", **base)
        if package.readiness == "HUMAN_REVIEW_REQUIRED":
            return self._result(READY_FOR_HUMAN_REVIEW, "; ".join(package.blocking_reasons) or "Package requires human review.", user_action_required=True,
                                evidence_checked=checked + ["application_package"], next_safe_action="Review the package evidence.", **base)
        if package.readiness not in {"READY_FOR_APPLICATION", "READY_FOR_BROWSER_PREPARATION"}:
            return self._result(NOT_EXECUTABLE, "; ".join(package.blocking_reasons) or "Package is not ready for safe browser preparation.",
                                evidence_checked=checked + ["application_package"], next_safe_action="Refresh or complete the package safely.", **base)
        return self._result(READY_FOR_AUTOMATION, "Package, eligibility, and frozen priority checks are ready for controlled execution.",
                            evidence_checked=checked + ["application_package", "answer_vault"], next_safe_action="Continue through the existing browser preparation boundary.", **base)

    def resolve_question(self, question_text: str, **kwargs):
        """Expose the existing approved resolver; no blocker or vault write is made."""
        return self.answer_engine.resolve(question_text, **kwargs)

    @staticmethod
    def group_blockers(blockers: list[dict]) -> list[dict]:
        """Group only clearly reusable answer blockers for presentation.

        Legal, jurisdiction-specific, salary, security, browser, and final
        approval blockers are intentionally never grouped.  This method is
        read-only and offers no bulk-resolution operation.
        """
        groups: dict[tuple[str, str], list[dict]] = {}
        for blocker in blockers:
            category = BLOCKER_TAXONOMY.get(blocker.get("blocker_type"), "OTHER_CONSEQUENTIAL_UNKNOWN")
            if category != "SCREENING_ANSWER":
                key = (f"individual:{blocker.get('id')}", category)
            else:
                detail = " ".join((blocker.get("detail") or "").lower().split())
                key = (f"answer:{detail}", category) if detail else (f"individual:{blocker.get('id')}", category)
            groups.setdefault(key, []).append(blocker)
        return [
            {"group_key": key[0], "blocker_category": key[1], "count": len(rows),
             "blockers": rows, "groupable": key[0].startswith("answer:") and len(rows) > 1}
            for key, rows in groups.items()
        ]

    @staticmethod
    def _next_action(category: str) -> str:
        return {
            "ELIGIBILITY": "Review the eligibility evidence.",
            "SCREENING_ANSWER": "Provide or approve the application-specific answer.",
            "SALARY": "Make the job-specific compensation decision.",
            "FINAL_SUBMIT_AUTHORIZATION": "Review this application and authorize or decline submission.",
            "EMPLOYER_COMMUNICATION": "Review the employer communication; no reply is sent automatically.",
        }.get(category, "Complete the required browser or application action safely.")

    @staticmethod
    def _result(state, reason, **kwargs):
        kwargs.setdefault("evidence_checked", ["application_history"])
        return ExecutionReadiness(state=state, label=_LABELS[state], reason=reason, **kwargs)
