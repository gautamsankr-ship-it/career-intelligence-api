"""Durable, single-flight control for the existing Career Intelligence runner.

This module owns run lifecycle metadata only. CRM evidence, application
execution artifacts, Gmail outcome evidence, and submission receipts remain in
their existing authoritative stores.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import APPLICATION_HISTORY_DB, MAX_JOBS

RUN_STATUSES = {
    "QUEUED", "RUNNING", "WAITING_FOR_HUMAN", "STOP_REQUESTED", "COMPLETED",
    "COMPLETED_WITH_WARNINGS", "FAILED", "INTERRUPTED",
}
RUN_MODES = {"FULL", "GMAIL"}
GLOBAL_STATES = {"READY", "RUNNING", "WAITING FOR YOU", "ATTENTION REQUIRED", "STOPPED"}

RUN_LOCK_DIR = Path("app/data")
RUN_LOCK_PATH = RUN_LOCK_DIR / "automation_run.lock"
_PROCESS_ACTIVE_RUN_IDS: set[str] = set()


class AutomationRunAlreadyActive(RuntimeError):
    """Another full or Gmail automation operation owns the local run lock."""


class AutomationRunNotFound(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class AutomationControlService:
    """Small run-control store using the existing application SQLite DB.

    A service instance is intentionally cheap and opens a fresh SQLite
    connection per operation, which lets a background worker and request
    handlers safely share the same database without sharing a connection
    across threads.
    """

    def __init__(
        self,
        db_path: str | Path = APPLICATION_HISTORY_DB,
        lock_path: str | Path = RUN_LOCK_PATH,
        runner_factory=None,
        gmail_runner_factory=None,
    ) -> None:
        self.db_path = Path(db_path)
        self.lock_path = Path(lock_path)
        self.runner_factory = runner_factory
        self.gmail_runner_factory = gmail_runner_factory
        # Process-wide so a second request-scoped service instance cannot
        # mistake a run owned by this same server process for a stale run.
        self._active_run_ids = _PROCESS_ACTIVE_RUN_IDS
        self._initialize_schema()
        self.reconcile_interrupted()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    lifecycle_status TEXT NOT NULL,
                    current_stage TEXT,
                    configured_scope TEXT,
                    summary_counts TEXT,
                    human_pause_reason TEXT,
                    warnings TEXT,
                    errors TEXT,
                    stop_requested INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail TEXT
                )
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row | dict | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        for key in ("configured_scope", "summary_counts", "warnings", "errors"):
            result[key] = _loads(result.get(key), {} if key in {"configured_scope", "summary_counts"} else [])
        result["stop_requested"] = bool(result.get("stop_requested"))
        return result

    def _append_event(self, connection, run_id: str, event_type: str, detail: str = "") -> None:
        connection.execute(
            "INSERT INTO automation_run_events (run_id, occurred_at, event_type, detail) VALUES (?, ?, ?, ?)",
            (run_id, _now(), event_type, detail),
        )

    def reconcile_interrupted(self) -> int:
        """Conservatively close runs not proven alive in this process.

        A new server process has an empty in-memory active set, so persisted
        RUNNING rows are treated as interrupted rather than resumed.
        """
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id FROM automation_runs WHERE lifecycle_status IN "
                "('QUEUED', 'RUNNING', 'STOP_REQUESTED')"
            ).fetchall()
            stale = [row[0] for row in rows if row[0] not in self._active_run_ids]
            for run_id in stale:
                connection.execute(
                    "UPDATE automation_runs SET lifecycle_status = 'INTERRUPTED', finished_at = ?, "
                    "human_pause_reason = ?, warnings = ? WHERE run_id = ?",
                    (_now(), "Previous run was active when the process stopped; no automatic resume was attempted.",
                     _json(["Run interrupted conservatively after process restart."]), run_id),
                )
                self._append_event(connection, run_id, "INTERRUPTED", "No live process could prove ownership after restart.")
            try:
                lock_owner = self.lock_path.read_text(encoding="ascii").strip()
                live_row = connection.execute(
                    "SELECT 1 FROM automation_runs WHERE run_id = ? AND lifecycle_status IN "
                    "('QUEUED', 'RUNNING', 'STOP_REQUESTED')", (lock_owner,)
                ).fetchone()
                if lock_owner not in self._active_run_ids and not live_row:
                    self.lock_path.unlink()
            except (FileNotFoundError, OSError):
                pass
            return len(stale)

    def acquire(self, mode: str, trigger: str) -> dict:
        mode = mode.upper()
        if mode not in RUN_MODES:
            raise ValueError(f"Unsupported automation mode: {mode}")
        run_id = uuid4().hex
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, run_id.encode("ascii"))
            os.close(fd)
        except FileExistsError:
            raise AutomationRunAlreadyActive("Another automation run is already active.") from None
        requested_at = _now()
        scope = {"max_jobs": MAX_JOBS, "package_prepare_limit": 5, "sources": ["linkedin", "indeed"]}
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO automation_runs (run_id, mode, trigger, requested_at, lifecycle_status, "
                    "current_stage, configured_scope, summary_counts, warnings, errors) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, mode, trigger, requested_at, "QUEUED", "QUEUED", _json(scope), _json({}), _json([]), _json([])),
                )
                self._append_event(connection, run_id, "QUEUED", f"{mode} requested from {trigger}.")
            self._active_run_ids.add(run_id)
            return self.get(run_id)
        except Exception:
            self._release_lock(run_id)
            raise

    def _release_lock(self, run_id: str) -> None:
        try:
            if self.lock_path.read_text(encoding="ascii").strip() == run_id:
                self.lock_path.unlink()
        except (FileNotFoundError, OSError):
            pass
        self._active_run_ids.discard(run_id)

    def get(self, run_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM automation_runs WHERE run_id = ?", (run_id,)).fetchone()
        record = self._record(row)
        if not record:
            raise AutomationRunNotFound(run_id)
        return record

    def latest(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM automation_runs ORDER BY requested_at DESC LIMIT 1").fetchone()
        return self._record(row)

    def current(self) -> dict | None:
        """Return a run still owned by this process, if any."""
        self.reconcile_interrupted()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM automation_runs WHERE lifecycle_status IN ('QUEUED', 'RUNNING', 'STOP_REQUESTED') "
                "ORDER BY requested_at DESC"
            ).fetchall()
        return next((self._record(row) for row in rows if row["run_id"] in self._active_run_ids), None)

    def recent(self, limit: int = 10) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM automation_runs ORDER BY requested_at DESC LIMIT ?", (max(1, min(limit, 50)),)
            ).fetchall()
        return [self._record(row) for row in rows]

    def events(self, run_id: str, limit: int = 20) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM automation_run_events WHERE run_id = ? ORDER BY id DESC LIMIT ?",
                (run_id, max(1, min(limit, 50))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def update(self, run_id: str, *, status: str | None = None, stage: str | None = None,
               summary: dict | None = None, human_pause_reason: str | None = None,
               warnings: list[str] | None = None, errors: list[str] | None = None,
               event_type: str | None = None, event_detail: str = "") -> dict:
        if status and status not in RUN_STATUSES:
            raise ValueError(f"Unsupported automation run status: {status}")
        fields, values = [], []
        if status:
            fields.append("lifecycle_status = ?"); values.append(status)
            if status == "RUNNING": fields.extend(["started_at = COALESCE(started_at, ?)"]); values.append(_now())
            if status in {"WAITING_FOR_HUMAN", "COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "INTERRUPTED"}:
                fields.append("finished_at = ?"); values.append(_now())
        if stage is not None: fields.append("current_stage = ?"); values.append(stage)
        if summary is not None: fields.append("summary_counts = ?"); values.append(_json(summary))
        if human_pause_reason is not None: fields.append("human_pause_reason = ?"); values.append(human_pause_reason)
        if warnings is not None: fields.append("warnings = ?"); values.append(_json(warnings))
        if errors is not None: fields.append("errors = ?"); values.append(_json(errors))
        with self._connect() as connection:
            if fields:
                connection.execute(f"UPDATE automation_runs SET {', '.join(fields)} WHERE run_id = ?", (*values, run_id))
            if event_type:
                self._append_event(connection, run_id, event_type, event_detail)
        return self.get(run_id)

    def request_stop(self, run_id: str) -> dict:
        with self._connect() as connection:
            connection.execute(
                "UPDATE automation_runs SET stop_requested = 1, lifecycle_status = CASE "
                "WHEN lifecycle_status IN ('QUEUED', 'RUNNING') THEN 'STOP_REQUESTED' ELSE lifecycle_status END WHERE run_id = ?",
                (run_id,),
            )
            self._append_event(connection, run_id, "STOP_REQUESTED", "Operator requested stop after the current safe step.")
        return self.get(run_id)

    def stop_requested(self, run_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT stop_requested FROM automation_runs WHERE run_id = ?", (run_id,)).fetchone()
        return bool(row and row[0])

    def global_state(self, crm=None) -> str:
        self.reconcile_interrupted()
        current = next((r for r in self.recent(20) if r["run_id"] in self._active_run_ids and r["lifecycle_status"] in {"QUEUED", "RUNNING", "STOP_REQUESTED"}), None)
        if current:
            return "STOPPED" if current["stop_requested"] else "RUNNING"
        latest = self.latest()
        if latest and latest["stop_requested"] and latest["lifecycle_status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS", "STOP_REQUESTED"}:
            return "STOPPED"
        if latest and latest["lifecycle_status"] == "WAITING_FOR_HUMAN":
            return "WAITING FOR YOU"
        if latest and latest["lifecycle_status"] in {"FAILED", "INTERRUPTED", "COMPLETED_WITH_WARNINGS"}:
            return "ATTENTION REQUIRED"
        if crm is not None:
            try:
                if crm.action_required_items():
                    return "WAITING FOR YOU"
            except Exception:
                return "ATTENTION REQUIRED"
        return "READY"

    def run_async(self, mode: str, trigger: str = "WEB") -> dict:
        """Acquire and execute in a daemon thread; callers should use run()."""
        import threading
        record = self.acquire(mode, trigger)
        thread = threading.Thread(target=self._run, args=(record["run_id"], mode.upper()), daemon=True)
        thread.start()
        return record

    def _run(self, run_id: str, mode: str) -> None:
        try:
            self.update(run_id, status="RUNNING", stage="STARTING", event_type="STARTED")
            stop_check = lambda: self.stop_requested(run_id)
            progress = lambda stage: self.update(run_id, stage=stage, event_type="STAGE", event_detail=stage)
            if mode == "GMAIL":
                from app.services.career_intelligence_runner import CareerIntelligenceRunner
                factory = self.gmail_runner_factory or (lambda: CareerIntelligenceRunner(stop_check=stop_check, progress_callback=progress))
                result = factory().gmail_only()
                summary = {key: value for key, value in result.items() if key != "details"}
                warnings = []
                errors = []
                pause = "" if not result.get("human_review") else "Employer messages require human review."
            else:
                from app.services.career_intelligence_runner import CareerIntelligenceRunner
                factory = self.runner_factory or (lambda: CareerIntelligenceRunner(stop_check=stop_check, progress_callback=progress))
                result = factory().run()
                summary = result.to_dict()
                warnings = list(summary.get("errors") or [])
                errors = []
                pause = "Human action is required for one or more opportunities." if summary.get("unresolved_human_blockers") else ""
            if stop_check():
                self.update(run_id, status="STOP_REQUESTED", stage="STOPPED_AT_SAFE_BOUNDARY", summary=summary,
                            human_pause_reason=pause or "Stop requested; no further safe stage was started.", warnings=warnings,
                            errors=errors, event_type="STOPPED_AT_SAFE_BOUNDARY")
            else:
                final = "WAITING_FOR_HUMAN" if pause else ("COMPLETED_WITH_WARNINGS" if warnings else "COMPLETED")
                self.update(run_id, status=final, stage="COMPLETE", summary=summary,
                            human_pause_reason=pause, warnings=warnings, errors=errors, event_type="FINISHED")
        except Exception as exc:
            self.update(run_id, status="FAILED", stage="FAILED", errors=[f"{type(exc).__name__}: {exc}"],
                        warnings=[], event_type="FAILED", event_detail=type(exc).__name__)
        finally:
            self._release_lock(run_id)
