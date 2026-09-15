"""Explicit human-resolution operations for execution blockers.

This service is deliberately narrower than the application engine.  It only
records a decision the user has already made, optionally promotes a clearly
reusable screening answer to the Answer Vault, and lets the existing CRM and
readiness models recompute.  It never changes score, priority, eligibility, or
submission state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from app.models.application_answer import ApplicationAnswer
from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.execution_readiness_service import ExecutionReadinessService


@dataclass(frozen=True)
class ResolutionResult:
    resolved_blocker_ids: tuple[int, ...]
    reusable_answer_saved: bool = False
    audit_reference: str = ""


class ExecutionResolutionService:
    """Record explicit, auditable resolution without inventing evidence."""

    def __init__(self, crm, vault: ApplicationAnswerVault | None = None):
        self.crm = crm
        self.vault = vault or ApplicationAnswerVault()

    def resolve_blocker(
        self,
        blocker_id: int,
        *,
        resolution: str,
        reusable: bool = False,
        concept: str = "",
        answer: object = None,
        confirm: bool = False,
        actor: str = "USER",
    ) -> ResolutionResult:
        blocker = self._open_blocker(blocker_id)
        if not blocker:
            raise ValueError("Only an open blocker can be resolved.")
        if reusable:
            self._validate_reusable(blocker, concept, answer, confirm)
        reference = self._audit_reference()
        if reusable:
            self._save_reusable_answer(concept, answer, blocker, reference, actor)
        note = resolution.strip()
        if not note:
            raise ValueError("A human resolution note is required.")
        note = f"{note} [resolution:{reference}]"
        self.crm.resolve_human_blocker(blocker_id, resolution_note=note, resolved_by=actor)
        self.crm.append_event(
            blocker["tracker_id"], "EXECUTION_RESOLUTION_RECORDED",
            reason="REUSABLE_ANSWER" if reusable else "APPLICATION_SPECIFIC",
            evidence_reference=reference, actor=actor,
        )
        if reusable:
            self.crm.append_event(
                blocker["tracker_id"], "REUSABLE_ANSWER_APPLIED",
                reason=concept, evidence_reference=reference, actor=actor,
            )
        return ResolutionResult((blocker_id,), reusable, reference)

    def resolve_group(
        self,
        blockers: list[dict],
        *,
        resolution: str,
        concept: str,
        answer: object,
        confirm: bool = False,
        actor: str = "USER",
    ) -> ResolutionResult:
        """Resolve an identical reusable-answer group after confirmation."""
        if not confirm:
            raise ValueError("Explicit confirmation is required for grouped resolution.")
        if not blockers:
            raise ValueError("A non-empty blocker group is required.")
        group = ExecutionReadinessService.group_blockers(blockers)
        ids = {int(row["id"]) for row in blockers}
        matching = next((row for row in group if row["groupable"] and {int(b["id"]) for b in row["blockers"]} == ids), None)
        if matching is None:
            raise ValueError("Only one homogeneous reusable screening-answer group may be resolved.")
        if not concept.strip() or answer is None:
            raise ValueError("A reusable concept and answer are required.")
        reference = self._audit_reference()
        first = matching["blockers"][0]
        self._save_reusable_answer(concept, answer, first, reference, actor)
        resolved_ids = []
        for blocker in matching["blockers"]:
            self.crm.resolve_human_blocker(
                blocker["id"],
                resolution_note=f"{resolution.strip()} [resolution:{reference}]",
                resolved_by=actor,
            )
            self.crm.append_event(
                blocker["tracker_id"], "BLOCKER_DEDUPLICATED",
                reason=concept, evidence_reference=reference, actor=actor,
            )
            self.crm.append_event(
                blocker["tracker_id"], "REUSABLE_ANSWER_APPLIED",
                reason=concept, evidence_reference=reference, actor=actor,
            )
            resolved_ids.append(int(blocker["id"]))
        return ResolutionResult(tuple(resolved_ids), True, reference)

    def safe_answer(self, question_text: str, **kwargs):
        """Expose the existing resolver for pre-escalation checks."""
        return ExecutionReadinessService(self.crm).resolve_question(question_text, **kwargs)

    def _open_blocker(self, blocker_id: int) -> dict | None:
        row = self.crm.connection.execute(
            "SELECT * FROM human_blockers WHERE id = ? AND status = 'OPEN'", (blocker_id,)
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _validate_reusable(blocker: dict, concept: str, answer: object, confirm: bool) -> None:
        if blocker.get("blocker_type") != "HUMAN_ANSWER_APPROVAL_REQUIRED":
            raise ValueError("Only reusable screening-answer blockers may update the Answer Vault.")
        if not confirm:
            raise ValueError("Explicit confirmation is required before saving a reusable answer.")
        if not concept.strip() or answer is None:
            raise ValueError("A reusable concept and answer are required.")

    def _save_reusable_answer(self, concept: str, answer: object, blocker: dict, reference: str, actor: str) -> None:
        existing = self.vault.get_answer(concept)
        if existing and existing.status == "APPROVED" and existing.value != answer:
            raise ValueError("An approved reusable answer already exists with a different value.")
        if existing and existing.status == "APPROVED":
            return
        record = ApplicationAnswer(
            answer_id=f"human-resolution-{uuid4().hex}", concept=concept, value=answer,
            answer_type="TEXT", automation_policy="AUTO_FILL", confidence="HIGH",
            answer_source="USER_APPROVED_ANSWER", evidence_reference=reference,
            sensitivity="CONTEXTUAL", status="APPROVED",
            notes=f"Explicitly confirmed reusable resolution for tracker {blocker['tracker_id']} by {actor}.",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self.vault.add_or_update_answer(record, reason=f"Explicit reusable resolution {reference}")

    @staticmethod
    def _audit_reference() -> str:
        return f"execution-resolution:{uuid4().hex}"
