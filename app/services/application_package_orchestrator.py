"""Build persisted, non-submitting application packages for tracked vacancies."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.models.application_package import ApplicationPackage
from app.models.employer import Employer
from app.services.application_answer_vault import ApplicationAnswerVault
from app.services.application_eligibility_policy import (
    INTELLIGENCE_PRIORITY_MISSING,
    intelligence_priority_gate,
)
from app.services.application_history_service import ApplicationHistoryService
from app.services.application_route_resolver import ApplicationRouteResolver
from app.services.application_service import ApplicationService
from app.services.remote_work_eligibility import ELIGIBLE, NOT_APPLICABLE, RemoteEligibilityResult

PACKAGE_DIR = Path("app/data/application_packages")
TERMINAL = {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "WITHDRAWN", "FAILED", "INTELLIGENCE_REJECTED"}


class ApplicationPackageOrchestrator:
    """Connect existing preparation components without changing lifecycle state."""
    def __init__(self, history=None, document_service=None, vault=None, package_dir=PACKAGE_DIR):
        self.history = history or ApplicationHistoryService()
        self.document_service = document_service or ApplicationService()
        self.vault = vault or ApplicationAnswerVault()
        self.routes = ApplicationRouteResolver()
        self.package_dir = Path(package_dir)

    def prepare(self, tracker_id: int) -> ApplicationPackage:
        record = self.history.get_record_by_id(tracker_id)
        if not record:
            raise ValueError(f"Tracker ID {tracker_id} was not found.")
        identity = self._identity(record)
        ineligible = self._eligibility_reason(record)
        if ineligible:
            return self._save(self._base(record, identity, readiness="NOT_APPLICATION_ELIGIBLE", reasons=[ineligible]))

        prior = self.load(tracker_id)
        route = self.routes.resolve(record)
        package = self._base(record, identity)
        self._apply_route(package, record, route)
        self._apply_answers(package)
        self._apply_documents(package, record, prior)
        self._apply_readiness(package)
        return self._save(package)

    def show(self, tracker_id: int) -> ApplicationPackage | None:
        return self.load(tracker_id)

    def ready(self):
        return [package for path in sorted(self.package_dir.glob("tracker-*.json"))
                if (package := ApplicationPackage.from_dict(json.loads(path.read_text(encoding="utf-8")))).readiness in {"READY_FOR_APPLICATION", "READY_FOR_BROWSER_PREPARATION"}]

    def prepare_ready(self, limit=5):
        if limit < 1:
            raise ValueError("limit must be at least 1")
        packages = []
        for record in self.history.list_ready_records():
            if len(packages) >= limit:
                break
            if self._eligibility_reason(record):
                continue
            packages.append(self.prepare(record["id"]))
        return packages

    def load(self, tracker_id):
        path = self._path(tracker_id)
        return ApplicationPackage.from_dict(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None

    def _base(self, record, identity, readiness="FAILED", reasons=None):
        package_id = f"pkg-{record['id']}-{identity[:12]}"
        return ApplicationPackage(
            package_id=package_id, tracker_id=record["id"], company=record.get("company") or "",
            job_title=record.get("job_title") or "", market=record.get("market") or "",
            career_track=record.get("career_track") or "", career_score=record.get("career_score"),
            ats_score=record.get("ats_score"), application_method=record.get("application_method") or "",
            vacancy_identity=identity, readiness=readiness, blocking_reasons=reasons or [],
        )

    @staticmethod
    def _identity(record):
        source = "|".join(str(record.get(key) or "") for key in ("job_fingerprint", "company", "job_title", "job_description", "application_url", "source_listing_url"))
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    @staticmethod
    def _eligibility_reason(record):
        if record.get("validation_only") is True:
            return "VALIDATION_ONLY_REJECTED"

        # Task 21.14E / 21.17C: intelligence_priority (persisted by CareerAgent
        # from the one authoritative JobIntelligenceService.evaluate() call) is
        # the primary gate, via the same shared policy ApplicationExecutionOrchestrator
        # uses (application_eligibility_policy.py) so the two can never silently
        # diverge. The raw decision/remote_eligibility checks below remain ONLY
        # as a fallback for records persisted before intelligence_priority
        # existed, and never override it when present.
        gate_reason = intelligence_priority_gate(record)
        if gate_reason == INTELLIGENCE_PRIORITY_MISSING:
            if record.get("decision") != "AUTO_APPLY":
                return "NOT_AUTO_APPLY"
            # Task 21.14B: NOT_APPLICABLE (a non-remote vacancy -- no known
            # blocker, same as the Task 21.14A funnel gate treats it) was
            # previously rejected here just like INELIGIBLE/unassessed, because
            # this check required the literal string "ELIGIBLE". That silently
            # blocked every legitimately non-remote vacancy.
            if record.get("remote_eligibility") not in (ELIGIBLE, NOT_APPLICABLE):
                return "REMOTE_ELIGIBILITY_NOT_CONFIRMED"
        elif gate_reason:
            return gate_reason

        if record.get("application_status") in TERMINAL or record.get("status") in TERMINAL:
            return "TERMINAL_APPLICATION_STATUS"
        return ""

    def _apply_route(self, package, record, route):
        if route.resolution_status == "RESOLVED":
            package.application_url = route.application_url
            package.application_portal = route.portal
            package.route_confidence = route.route_confidence
            if route.application_url_type == "ATS_URL" and route.portal in {"GREENHOUSE", "LEVER"}:
                package.portal_capability = "FULL_PREPARATION_SUPPORTED"
            elif route.application_url_type in {"ATS_URL", "EMPLOYER_CAREER_URL", "DIRECT_APPLICATION_URL"}:
                package.portal_capability = "PARTIAL_AUTOMATION"
            else:
                package.portal_capability = "MANUAL_WEB"
        else:
            package.application_url = record.get("source_listing_url") or record.get("job_url") or ""
            package.application_portal = record.get("application_portal") or "UNKNOWN"
            package.route_confidence = record.get("application_route_confidence") or "LOW"
            package.portal_capability = "MANUAL_WEB" if package.application_url else "ROUTE_UNRESOLVED"

    def _apply_answers(self, package):
        answers = [item for item in self.vault.answers if item.status == "APPROVED"]
        rules = [item for item in self.vault.rules if item.status == "APPROVED"]
        items = answers + rules
        package.answer_counts = {policy: sum(item.automation_policy == policy for item in items)
                                 for policy in ("AUTO_FILL", "AUTO_FILL_WITH_RULES", "MANUAL_REVIEW")}
        package.manual_answer_count = package.answer_counts["MANUAL_REVIEW"]
        package.answer_vault_status = "ANSWER_VAULT_READY" if package.answer_counts["AUTO_FILL"] else "ANSWER_REVIEW_REQUIRED"

    def _apply_documents(self, package, record, prior):
        # Paths stored against this tracker are vacancy-specific. A prior package
        # must also share the content identity before it may be reused.
        reusable_prior = prior if prior and prior.vacancy_identity == package.vacancy_identity else None
        resume = self._existing(record.get("resume_path")) or (reusable_prior and self._existing(reusable_prior.resume_path))
        cover = self._existing(record.get("cover_letter_path")) or (reusable_prior and self._existing(reusable_prior.cover_letter_path))
        package.resume_path = resume or ""
        package.cover_letter_path = cover or ""
        package.resume_status = "READY" if resume else "DOCUMENT_NOT_READY"
        package.resume_vacancy_identity = package.vacancy_identity if resume else ""
        package.cover_letter_status = "READY" if cover else self._cover_requirement(record)
        if resume and (package.cover_letter_status in {"READY", "OPTIONAL", "NOT_NEEDED"}):
            return
        try:
            generated = self._generate_documents(package, record)
        except Exception as exc:
            package.blocking_reasons.append(f"DOCUMENT_GENERATION_FAILED:{type(exc).__name__}")
            return
        package.resume_path = self._existing(getattr(generated, "docx_path", "")) or ""
        package.cover_letter_path = self._existing(getattr(generated, "cover_letter_docx_path", "")) or ""
        package.resume_status = "READY" if package.resume_path else "DOCUMENT_NOT_READY"
        package.resume_generated_at = self._now() if package.resume_path else ""
        package.resume_vacancy_identity = package.vacancy_identity if package.resume_path else ""
        package.cover_letter_status = "READY" if package.cover_letter_path else self._cover_requirement(record)

    def _generate_documents(self, package, record):
        # Task 21.14B: reuse the tracker's already-recorded, authoritative
        # remote_eligibility (the same value _eligibility_reason() above
        # already gated on) rather than letting evaluate_job() silently
        # recompute a weaker one from a job_analysis-derived shim, which
        # has no access to the real discovery-layer opportunity object and
        # would almost always resolve to NOT_APPLICABLE regardless of what
        # was actually determined about this vacancy.
        hard_eligibility = self._known_hard_eligibility(record)
        job_description = record.get("job_description") or record.get("job_title") or ""

        # Task 21.17D: reuse the persisted evaluation snapshot (job_analysis
        # + employer, both OpenAI-derived, captured once by CareerAgent
        # alongside intelligence_priority) when available -- deterministic,
        # no new OpenAI call, and cannot drift from the decision that already
        # gated this call. A missing or unparseable snapshot (a record from
        # before this field existed, or corrupted data) falls back to a
        # fresh evaluate_job() call -- explicit and reported via
        # package.evaluation_source, never silent, and never able to change
        # the already-persisted, authoritative intelligence_priority.
        snapshot = self._load_snapshot(record)
        if snapshot is not None:
            job_analysis, employer = snapshot
            evaluation = self.document_service.evaluate_from_snapshot(
                job_description, job_analysis, employer, hard_eligibility=hard_eligibility,
            )
            package.evaluation_source = "PERSISTED_SNAPSHOT"
        else:
            evaluation = self.document_service.evaluate_job(
                job_description, hard_eligibility=hard_eligibility,
            )
            package.evaluation_source = "FRESH_EVALUATION_FALLBACK"
        return self.document_service.generate_application_documents(evaluation)

    @staticmethod
    def _load_snapshot(record):
        """Returns (job_analysis, employer) reconstructed from the persisted
        evaluation_snapshot column, or None if absent/unparseable/invalid --
        fails safe to the fresh-evaluation fallback rather than raising or
        fabricating a partial evaluation. The snapshot is read directly off
        the already-fetched tracker record for this exact tracker_id, so it
        can never belong to a different vacancy."""
        raw = record.get("evaluation_snapshot")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            job_analysis = data["job_analysis"]
            employer = Employer(**data["employer"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        if not isinstance(job_analysis, dict):
            return None
        return job_analysis, employer

    @staticmethod
    def _known_hard_eligibility(record):
        decision = record.get("remote_eligibility")
        if not decision:
            return None
        return RemoteEligibilityResult(
            decision=decision,
            scope="",
            reason=record.get("remote_eligibility_reason") or "",
            evidence=record.get("remote_eligibility_evidence") or "",
        )

    @staticmethod
    def _cover_requirement(record):
        requested = (record.get("cover_letter_requirement") or "").upper()
        if requested in {"REQUIRED", "RECOMMENDED", "OPTIONAL", "NOT_NEEDED"}:
            return requested
        return "RECOMMENDED"

    def _apply_readiness(self, package):
        if package.resume_status != "READY" or package.cover_letter_status in {"REQUIRED", "RECOMMENDED"}:
            package.readiness = "DOCUMENT_NOT_READY"; return
        if package.answer_vault_status != "ANSWER_VAULT_READY":
            package.readiness = "ANSWER_REVIEW_REQUIRED"; return
        if package.portal_capability == "ROUTE_UNRESOLVED":
            package.readiness = "ROUTE_UNRESOLVED"; return
        if package.portal_capability == "MANUAL_WEB":
            package.readiness = "MANUAL_WEB_REQUIRED"; return
        package.readiness = "READY_FOR_BROWSER_PREPARATION" if package.portal_capability == "FULL_PREPARATION_SUPPORTED" else "READY_FOR_APPLICATION"

    @staticmethod
    def _existing(path): return str(path) if path and Path(path).is_file() else ""
    def _path(self, tracker_id): return self.package_dir / f"tracker-{tracker_id}.json"
    def _save(self, package):
        previous = self.load(package.tracker_id)
        package.created_at = previous.created_at if previous else package.created_at
        package.updated_at = self._now()
        self.package_dir.mkdir(parents=True, exist_ok=True)
        self._path(package.tracker_id).write_text(json.dumps(package.to_dict(), indent=2), encoding="utf-8")
        return package
    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat()
