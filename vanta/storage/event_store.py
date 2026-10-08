"""SQLite WAL store for runs, jobs, and the persistent event stream."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vanta.core.schemas import Job, JobStatus, RunManifest, RunStatus, VantaEvent

ALLOWED_JOB_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.RETRYABLE,
        JobStatus.CANCELLED,
    },
    JobStatus.RETRYABLE: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}

ALLOWED_RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {
        RunStatus.PAUSED,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.RETRYABLE,
        RunStatus.CANCELLED,
    },
    RunStatus.PAUSED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.RETRYABLE: {RunStatus.QUEUED, RunStatus.CANCELLED},
    RunStatus.SUCCEEDED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


def _json(model: Any) -> str:
    return model.model_dump_json()


class EventStore:
    """Thread-safe SQLite store; each call opens a short-lived connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    timestamp TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_run_sequence ON events(run_id, sequence);
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
                """
            )

    def create_run(self, manifest: RunManifest) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO runs(run_id, project_id, status, manifest_json, created_at) VALUES(?,?,?,?,?)",
                (
                    manifest.run_id,
                    manifest.project_id,
                    RunStatus.QUEUED.value,
                    _json(manifest),
                    manifest.created_at.isoformat(),
                ),
            )

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT run_id, project_id, status, manifest_json, created_at FROM runs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as db:
            row = db.execute(
                "SELECT run_id, project_id, status, manifest_json, created_at FROM runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return dict(row) if row else None

    def transition_run(self, run_id: str, status: RunStatus) -> RunStatus:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(f"unknown run {run_id}")
            current = RunStatus(row["status"])
            if status not in ALLOWED_RUN_TRANSITIONS[current]:
                raise ValueError(f"invalid run transition {current.value} -> {status.value}")
            db.execute("UPDATE runs SET status=? WHERE run_id=?", (status.value, run_id))
            return status

    def set_run_status(self, run_id: str, status: str) -> None:
        """Compatibility wrapper; new callers should use the typed transition API."""
        self.transition_run(run_id, RunStatus(status))

    def add_event(self, event: VantaEvent) -> int:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                "INSERT INTO events(event_id, run_id, timestamp, status, payload_json) VALUES(?,?,?,?,?)",
                (
                    event.event_id,
                    event.run_id,
                    event.timestamp.isoformat(),
                    event.status.value,
                    _json(event),
                ),
            )
            return int(cursor.lastrowid)

    def list_events(
        self, *, run_id: str | None = None, after: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        query = "SELECT sequence, payload_json FROM events WHERE sequence > ?"
        params: list[Any] = [max(0, after)]
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        query += " ORDER BY sequence LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        with self._lock, self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return [{"sequence": row["sequence"], **json.loads(row["payload_json"])} for row in rows]

    def create_job(self, job: Job) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO jobs(job_id, run_id, status, payload_json) VALUES(?,?,?,?)",
                (job.id, job.run_id, job.status.value, _json(job)),
            )

    def get_job(self, job_id: str) -> Job | None:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT payload_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return Job.model_validate_json(row[0]) if row else None

    def transition_job(self, job_id: str, status: JobStatus, *, error: str | None = None) -> Job:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT payload_json FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(f"unknown job {job_id}")
            job = Job.model_validate_json(row[0])
            if status not in ALLOWED_JOB_TRANSITIONS[job.status]:
                raise ValueError(f"invalid job transition {job.status.value} -> {status.value}")
            now = datetime.now(UTC)
            updated = job.model_copy(
                update={
                    "status": status,
                    "updated_at": now,
                    "attempts": job.attempts + (1 if status == JobStatus.RUNNING else 0),
                    "error": error,
                }
            )
            db.execute(
                "UPDATE jobs SET status=?, payload_json=? WHERE job_id=?",
                (status.value, _json(updated), job_id),
            )
            return updated
