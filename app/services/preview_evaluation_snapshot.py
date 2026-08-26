"""Short-lived, local preview-evaluation snapshots for controlled processing."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from app.config import (
    CACHE_FOLDER,
    PREVIEW_EVALUATION_SNAPSHOT_TTL_SECONDS,
    SCREENING_AUTO_APPLY_THRESHOLD,
    SCREENING_REVIEW_THRESHOLD,
)
from app.models.decision_model import CareerDecision, ScoreCard
from app.models.employer import Employer
from app.models.recruiter_decision import RecruiterDecision
from app.services.application_history_service import fingerprint_for_opportunity
from app.services.application_service import JobEvaluation
from app.services.remote_work_eligibility import RemoteEligibilityResult


SNAPSHOT_VERSION = 1


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PreviewEvaluationSnapshot:
    evaluation: JobEvaluation
    eligibility: RemoteEligibilityResult | None


class PreviewEvaluationSnapshotStore:
    """JSON-backed, expiring snapshots; never an application-history record."""

    def __init__(self, path: str | Path | None = None, ttl_seconds: int = PREVIEW_EVALUATION_SNAPSHOT_TTL_SECONDS) -> None:
        self.path = Path(path) if path else Path(CACHE_FOLDER) / "preview_evaluations.json"
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def current_profile_hash(application_service) -> str | None:
        loader = getattr(application_service, "profile_loader", None)
        if loader is None:
            return None
        try:
            return _stable_hash(loader.get_profile())
        except Exception:
            # Snapshot validity still uses the vacancy content hash when an
            # injected lightweight test/service cannot expose the profile.
            return None

    @staticmethod
    def current_scoring_config_hash(application_service) -> str | None:
        engine = getattr(application_service, "career_engine", None)
        if engine is None:
            return None
        return _stable_hash({
            "snapshot_version": SNAPSHOT_VERSION,
            "weights": getattr(engine, "weights", None),
            "review_threshold": SCREENING_REVIEW_THRESHOLD,
            "auto_apply_threshold": SCREENING_AUTO_APPLY_THRESHOLD,
        })

    def save(self, opportunity, evaluation: JobEvaluation, eligibility: RemoteEligibilityResult | None = None, scoring_config_hash: str | None = None) -> bool:
        data = self._load()
        fingerprint = fingerprint_for_opportunity(opportunity)
        data[fingerprint] = {
            "version": SNAPSHOT_VERSION,
            "created_at": time.time(),
            "description_hash": _stable_hash(opportunity.job_description),
            "profile_hash": _stable_hash(evaluation.profile),
            "scoring_config_hash": scoring_config_hash,
            "evaluation": self._json_value(evaluation),
            "eligibility": self._json_value(eligibility) if eligibility else None,
        }
        try:
            self._write(data)
        except (OSError, TypeError):
            # A preview remains useful even when an injected test/dynamic
            # evaluation object is not serializable for later reuse.
            return False
        return True

    def get(self, opportunity, current_profile_hash: str | None = None, scoring_config_hash: str | None = None) -> PreviewEvaluationSnapshot | None:
        data = self._load()
        fingerprint = fingerprint_for_opportunity(opportunity)
        record = data.get(fingerprint)
        if not record or not self._valid(record, opportunity, current_profile_hash, scoring_config_hash):
            if record:
                data.pop(fingerprint, None)
                self._write(data)
            return None
        return self._deserialize(record)

    def consume(self, opportunity) -> None:
        data = self._load()
        if data.pop(fingerprint_for_opportunity(opportunity), None) is not None:
            self._write(data)

    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        now = time.time()
        cleaned = {
            key: value for key, value in data.items()
            if isinstance(value, dict) and now - value.get("created_at", 0) <= self.ttl_seconds
        }
        if cleaned != data:
            self._write(cleaned)
        return cleaned

    def _write(self, data: dict[str, dict]) -> None:
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _valid(self, record: dict, opportunity, current_profile_hash: str | None, scoring_config_hash: str | None) -> bool:
        return (
            record.get("version") == SNAPSHOT_VERSION
            and record.get("description_hash") == _stable_hash(opportunity.job_description)
            and (current_profile_hash is None or record.get("profile_hash") == current_profile_hash)
            and (scoring_config_hash is None or record.get("scoring_config_hash") == scoring_config_hash)
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        return asdict(value) if is_dataclass(value) else value

    @staticmethod
    def _deserialize(record: dict) -> PreviewEvaluationSnapshot:
        raw = record["evaluation"]
        decision_data = raw["career_decision"]
        decision = CareerDecision(
            **{**decision_data, "scorecards": [ScoreCard(**card) for card in decision_data["scorecards"]]}
        )
        evaluation = JobEvaluation(
            profile=raw["profile"],
            job_analysis=raw["job_analysis"],
            employer=Employer(**raw["employer"]),
            career_decision=decision,
            ats_result=raw["ats_result"],
            screening_decision=raw["screening_decision"],
            recruiter=RecruiterDecision(**raw["recruiter"]) if raw.get("recruiter") else None,
        )
        raw_eligibility = record.get("eligibility")
        eligibility = RemoteEligibilityResult(**raw_eligibility) if raw_eligibility else None
        return PreviewEvaluationSnapshot(evaluation=evaluation, eligibility=eligibility)
