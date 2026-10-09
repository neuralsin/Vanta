"""Authoritative SQLite store for VANTA's knowledge (manual 9.1, section 36).

Media, shots, embeddings, projects, EDG revisions, patches, style profiles,
capsules, rules, corrections, decisions and QC live here. Large artefacts
(analysis JSON, thumbnails, LUTs) live on disk and are referenced by path.
The event/run ledger shares the same database file via ``EventStore``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS library_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('reference','footage','music','sfx')),
    path TEXT NOT NULL,
    content_key TEXT NOT NULL,
    title TEXT,
    source_url TEXT,
    uploader TEXT,
    added_at TEXT NOT NULL,
    duration_s REAL,
    fps REAL,
    width INTEGER,
    height INTEGER,
    has_audio INTEGER,
    info_json TEXT,
    analysis_status TEXT NOT NULL DEFAULT 'pending',
    analysis_version TEXT,
    analysis_path TEXT,
    error TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    rights_json TEXT NOT NULL DEFAULT '{}',
    profile_id TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    favorite INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    thumb TEXT,
    UNIQUE(kind, content_key)
);
CREATE TABLE IF NOT EXISTS shots (
    id TEXT PRIMARY KEY,
    media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    start_frame INTEGER NOT NULL,
    end_frame INTEGER NOT NULL,
    description TEXT,
    thumb TEXT,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_shots_media ON shots(media_id, idx);
CREATE TABLE IF NOT EXISTS embeddings (
    owner_id TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (owner_id, model)
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    spec_json TEXT NOT NULL,
    footage_json TEXT NOT NULL DEFAULT '[]',
    references_json TEXT NOT NULL DEFAULT '[]',
    music_id TEXT,
    profile_id TEXT,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS edgs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    parent_id TEXT,
    created_at TEXT NOT NULL,
    note TEXT,
    json TEXT NOT NULL,
    UNIQUE(project_id, revision)
);
CREATE TABLE IF NOT EXISTS patches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    edg_id TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS capsules (
    id TEXT PRIMARY KEY,
    reference_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    technique TEXT NOT NULL,
    confidence REAL NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capsules_ref ON capsules(reference_id);
CREATE TABLE IF NOT EXISTS rules (
    id TEXT PRIMARY KEY,
    profile_id TEXT,
    dimension TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('principle','conditional','negative','exception','preference')),
    confidence REAL NOT NULL,
    support INTEGER NOT NULL DEFAULT 0,
    contradictions INTEGER NOT NULL DEFAULT 0,
    approval_state TEXT NOT NULL DEFAULT 'candidate',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_confirmed TEXT,
    last_used TEXT,
    json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_profile ON rules(profile_id, dimension, approval_state);
CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    run_id TEXT,
    dimension TEXT NOT NULL,
    kind TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    project_id TEXT,
    dimension TEXT NOT NULL,
    predicted_confidence REAL NOT NULL,
    accepted INTEGER,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qc_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    iteration INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    run_id TEXT,
    target TEXT,
    kind TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
INSERT OR IGNORE INTO library_migrations(version, applied_at) VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Library:
    """Thread-safe accessor; short-lived connections, WAL journal."""

    JSON_COLUMNS = {
        "info_json",
        "tags_json",
        "rights_json",
        "data_json",
        "spec_json",
        "footage_json",
        "references_json",
        "json",
    }

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=15)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=15000")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        for key in list(out):
            if key in self.JSON_COLUMNS and isinstance(out[key], str):
                try:
                    out[key] = json.loads(out[key])
                except json.JSONDecodeError:
                    pass
        return out

    def _rows(self, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        return [self._row(row) for row in rows]  # type: ignore[misc]

    # ---------------------------------------------------------------- media
    def find_media(self, kind: str, content_key: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(
                db.execute(
                    "SELECT * FROM media WHERE kind=? AND content_key=?", (kind, content_key)
                ).fetchone()
            )

    def add_media(self, *, kind: str, path: str, content_key: str, **fields: Any) -> dict[str, Any]:
        existing = self.find_media(kind, content_key)
        if existing:
            return existing
        media_id = new_id({"reference": "ref", "footage": "ftg", "music": "mus", "sfx": "sfx"}[kind])
        record = {
            "id": media_id,
            "kind": kind,
            "path": path,
            "content_key": content_key,
            "added_at": now_iso(),
            **fields,
        }
        for key in ("info_json", "tags_json", "rights_json"):
            if key in record and not isinstance(record[key], str):
                record[key] = _dumps(record[key])
        columns = ", ".join(record)
        marks = ", ".join("?" for _ in record)
        with self.connect() as db:
            db.execute(f"INSERT INTO media({columns}) VALUES({marks})", tuple(record.values()))
        return self.get_media(media_id)  # type: ignore[return-value]

    def get_media(self, media_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone())

    def list_media(self, kind: str | None = None, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM media"
        params: list[Any] = []
        clauses = []
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if enabled_only:
            clauses.append("enabled=1 AND rejected=0")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY added_at DESC"
        with self.connect() as db:
            return self._rows(db.execute(query, params).fetchall())

    def update_media(self, media_id: str, **fields: Any) -> None:
        if not fields:
            return
        for key in ("info_json", "tags_json", "rights_json"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = _dumps(fields[key])
        sets = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as db:
            db.execute(f"UPDATE media SET {sets} WHERE id=?", (*fields.values(), media_id))

    def delete_media(self, media_id: str) -> None:
        """Remove the library record only; the file on disk is never deleted here."""
        with self.connect() as db:
            db.execute("DELETE FROM embeddings WHERE owner_id=? OR owner_id LIKE ?", (media_id, f"{media_id}:%"))
            db.execute("DELETE FROM media WHERE id=?", (media_id,))

    # ---------------------------------------------------------------- shots
    def replace_shots(self, media_id: str, shots: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM shots WHERE media_id=?", (media_id,))
            for shot in shots:
                db.execute(
                    "INSERT INTO shots(id, media_id, idx, start_s, end_s, start_frame, end_frame, description, thumb, data_json)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        shot["id"],
                        media_id,
                        shot["idx"],
                        shot["start_s"],
                        shot["end_s"],
                        shot["start_frame"],
                        shot["end_frame"],
                        shot.get("description"),
                        shot.get("thumb"),
                        _dumps(shot.get("data", {})),
                    ),
                )

    def list_shots(self, media_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            if media_id:
                rows = db.execute("SELECT * FROM shots WHERE media_id=? ORDER BY idx", (media_id,))
            else:
                rows = db.execute("SELECT * FROM shots ORDER BY media_id, idx")
            return self._rows(rows.fetchall())

    def get_shot(self, shot_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM shots WHERE id=?", (shot_id,)).fetchone())

    def update_shot(self, shot_id: str, **fields: Any) -> None:
        if "data_json" in fields and not isinstance(fields["data_json"], str):
            fields["data_json"] = _dumps(fields["data_json"])
        sets = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as db:
            db.execute(f"UPDATE shots SET {sets} WHERE id=?", (*fields.values(), shot_id))

    # ----------------------------------------------------------- embeddings
    def put_embedding(self, owner_id: str, model: str, vector: np.ndarray) -> None:
        vec = np.asarray(vector, dtype=np.float32).ravel()
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO embeddings(owner_id, model, dim, vector) VALUES(?,?,?,?)",
                (owner_id, model, int(vec.size), vec.tobytes()),
            )

    def get_embeddings(self, model: str, owner_prefix: str | None = None) -> tuple[list[str], np.ndarray]:
        with self.connect() as db:
            if owner_prefix:
                rows = db.execute(
                    "SELECT owner_id, dim, vector FROM embeddings WHERE model=? AND owner_id LIKE ?",
                    (model, f"{owner_prefix}%"),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT owner_id, dim, vector FROM embeddings WHERE model=?", (model,)
                ).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        ids = [row["owner_id"] for row in rows]
        matrix = np.stack([np.frombuffer(row["vector"], dtype=np.float32) for row in rows])
        return ids, matrix

    def nearest(self, model: str, query: np.ndarray, *, k: int = 8, owner_prefix: str | None = None) -> list[tuple[str, float]]:
        """Cosine nearest neighbours (exact; the library scale does not need ANN)."""
        ids, matrix = self.get_embeddings(model, owner_prefix)
        if not ids:
            return []
        q = np.asarray(query, dtype=np.float32).ravel()
        q = q / max(np.linalg.norm(q), 1e-9)
        m = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
        scores = m @ q
        order = np.argsort(-scores)[:k]
        return [(ids[i], float(scores[i])) for i in order]

    # ------------------------------------------------------------- projects
    def create_project(self, name: str, spec: dict[str, Any], **fields: Any) -> dict[str, Any]:
        project_id = new_id("prj")
        stamp = now_iso()
        with self.connect() as db:
            db.execute(
                "INSERT INTO projects(id, name, created_at, updated_at, spec_json, footage_json, references_json, music_id, profile_id, data_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    project_id,
                    name,
                    stamp,
                    stamp,
                    _dumps(spec),
                    _dumps(fields.get("footage", [])),
                    _dumps(fields.get("references", [])),
                    fields.get("music_id"),
                    fields.get("profile_id"),
                    _dumps(fields.get("data", {})),
                ),
            )
        return self.get_project(project_id)  # type: ignore[return-value]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return self._rows(db.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall())

    def update_project(self, project_id: str, **fields: Any) -> None:
        mapping = {"spec": "spec_json", "footage": "footage_json", "references": "references_json", "data": "data_json"}
        clean: dict[str, Any] = {}
        for key, value in fields.items():
            column = mapping.get(key, key)
            clean[column] = _dumps(value) if column in self.JSON_COLUMNS and not isinstance(value, str) else value
        clean["updated_at"] = now_iso()
        sets = ", ".join(f"{key}=?" for key in clean)
        with self.connect() as db:
            db.execute(f"UPDATE projects SET {sets} WHERE id=?", (*clean.values(), project_id))

    def delete_project(self, project_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM projects WHERE id=?", (project_id,))

    # ----------------------------------------------------------------- EDGs
    def save_edg(self, project_id: str, revision: int, edg_json: dict[str, Any], *, edg_id: str, parent_id: str | None, note: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO edgs(id, project_id, revision, parent_id, created_at, note, json) VALUES(?,?,?,?,?,?,?)",
                (edg_id, project_id, revision, parent_id, now_iso(), note, _dumps(edg_json)),
            )

    def latest_edg(self, project_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(
                db.execute(
                    "SELECT * FROM edgs WHERE project_id=? ORDER BY revision DESC LIMIT 1", (project_id,)
                ).fetchone()
            )

    def list_edgs(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return self._rows(
                db.execute(
                    "SELECT id, project_id, revision, parent_id, created_at, note FROM edgs WHERE project_id=? ORDER BY revision",
                    (project_id,),
                ).fetchall()
            )

    def get_edg(self, edg_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM edgs WHERE id=?", (edg_id,)).fetchone())

    def save_patch(self, project_id: str, patch: dict[str, Any], *, edg_id: str | None, status: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO patches(id, project_id, edg_id, created_at, status, json) VALUES(?,?,?,?,?,?)",
                (patch["id"], project_id, edg_id, now_iso(), status, _dumps(patch)),
            )

    def list_patches(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return self._rows(
                db.execute("SELECT * FROM patches WHERE project_id=? ORDER BY created_at", (project_id,)).fetchall()
            )

    # ------------------------------------------------------- style profiles
    def ensure_profile(self, name: str, description: str = "") -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM profiles WHERE name=?", (name,)).fetchone()
            if row:
                return self._row(row)  # type: ignore[return-value]
            profile_id = new_id("prof")
            db.execute(
                "INSERT INTO profiles(id, name, description, created_at, data_json) VALUES(?,?,?,?,?)",
                (profile_id, name, description, now_iso(), "{}"),
            )
            return self._row(db.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone())  # type: ignore[return-value]

    def list_profiles(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return self._rows(db.execute("SELECT * FROM profiles ORDER BY name").fetchall())

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM profiles WHERE id=?", (profile_id,)).fetchone())

    def update_profile(self, profile_id: str, **fields: Any) -> None:
        if "data_json" in fields and not isinstance(fields["data_json"], str):
            fields["data_json"] = _dumps(fields["data_json"])
        sets = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as db:
            db.execute(f"UPDATE profiles SET {sets} WHERE id=?", (*fields.values(), profile_id))

    # ------------------------------------------------------------- capsules
    def replace_capsules(self, reference_id: str, capsules: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM capsules WHERE reference_id=? AND approved=0 AND rejected=0", (reference_id,))
            for capsule in capsules:
                db.execute(
                    "INSERT OR REPLACE INTO capsules(id, reference_id, dimension, technique, confidence, approved, rejected, created_at, json)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        capsule["id"],
                        reference_id,
                        capsule["dimension"],
                        capsule["technique"],
                        capsule["confidence"],
                        int(capsule.get("approved", False)),
                        0,
                        now_iso(),
                        _dumps(capsule),
                    ),
                )

    def list_capsules(self, reference_id: str | None = None, *, include_rejected: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM capsules"
        params: list[Any] = []
        clauses = []
        if reference_id:
            clauses.append("reference_id=?")
            params.append(reference_id)
        if not include_rejected:
            clauses.append("rejected=0")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.connect() as db:
            return self._rows(db.execute(query + " ORDER BY dimension, technique", params).fetchall())

    def set_capsule_state(self, capsule_id: str, *, approved: bool | None = None, rejected: bool | None = None) -> None:
        with self.connect() as db:
            if approved is not None:
                db.execute("UPDATE capsules SET approved=? WHERE id=?", (int(approved), capsule_id))
            if rejected is not None:
                db.execute("UPDATE capsules SET rejected=? WHERE id=?", (int(rejected), capsule_id))

    # ---------------------------------------------------------------- rules
    def upsert_rule(self, rule: dict[str, Any]) -> None:
        stamp = now_iso()
        with self.connect() as db:
            existing = db.execute("SELECT approval_state, created_at FROM rules WHERE id=?", (rule["id"],)).fetchone()
            state = rule.get("approval_state", "candidate")
            created = stamp
            if existing:
                created = existing["created_at"]
                # never silently promote or demote a reviewed rule
                if existing["approval_state"] in {"approved", "rejected"} and state == "candidate":
                    state = existing["approval_state"]
            rule = {**rule, "approval_state": state}
            db.execute(
                "INSERT OR REPLACE INTO rules(id, profile_id, dimension, kind, confidence, support, contradictions, approval_state,"
                " created_at, updated_at, last_confirmed, last_used, json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rule["id"],
                    rule.get("profile"),
                    rule["dimension"],
                    rule.get("kind", "conditional"),
                    float(rule.get("confidence", 0.0)),
                    int(rule.get("support", 0)),
                    int(rule.get("contradictions", 0)),
                    state,
                    created,
                    stamp,
                    rule.get("last_confirmed"),
                    rule.get("last_used"),
                    _dumps(rule),
                ),
            )

    def list_rules(
        self, *, profile_id: str | None = None, state: str | None = None, dimension: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM rules"
        clauses, params = [], []
        if profile_id:
            clauses.append("(profile_id=? OR profile_id IS NULL)")
            params.append(profile_id)
        if state:
            clauses.append("approval_state=?")
            params.append(state)
        if dimension:
            clauses.append("dimension=?")
            params.append(dimension)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.connect() as db:
            return self._rows(db.execute(query + " ORDER BY dimension, confidence DESC", params).fetchall())

    def set_rule_state(self, rule_id: str, state: str) -> None:
        if state not in {"candidate", "approved", "rejected", "stale"}:
            raise ValueError("invalid rule state")
        stamp = now_iso()
        with self.connect() as db:
            row = db.execute("SELECT json FROM rules WHERE id=?", (rule_id,)).fetchone()
            if not row:
                raise KeyError(rule_id)
            payload = json.loads(row["json"])
            payload["approval_state"] = state
            if state == "approved":
                payload["last_confirmed"] = stamp
            db.execute(
                "UPDATE rules SET approval_state=?, updated_at=?, last_confirmed=COALESCE(?, last_confirmed), json=? WHERE id=?",
                (state, stamp, stamp if state == "approved" else None, _dumps(payload), rule_id),
            )

    def delete_rule(self, rule_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    # ---------------------------------------------- corrections / decisions
    def add_correction(self, correction: dict[str, Any]) -> str:
        correction_id = correction.get("id") or new_id("corr")
        with self.connect() as db:
            db.execute(
                "INSERT INTO corrections(id, project_id, run_id, dimension, kind, confidence, created_at, json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    correction_id,
                    correction.get("project_id"),
                    correction.get("run_id"),
                    correction["dimension"],
                    correction["kind"],
                    float(correction.get("confidence", 0.5)),
                    now_iso(),
                    _dumps({**correction, "id": correction_id}),
                ),
            )
        return correction_id

    def list_corrections(self, dimension: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            if dimension:
                rows = db.execute("SELECT * FROM corrections WHERE dimension=? ORDER BY created_at", (dimension,))
            else:
                rows = db.execute("SELECT * FROM corrections ORDER BY created_at")
            return self._rows(rows.fetchall())

    def add_decision(self, decision: dict[str, Any]) -> str:
        decision_id = decision.get("id") or new_id("dec")
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO decisions(id, run_id, project_id, dimension, predicted_confidence, accepted, created_at, json)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    decision.get("run_id"),
                    decision.get("project_id"),
                    decision["dimension"],
                    float(decision["predicted_confidence"]),
                    decision.get("accepted"),
                    now_iso(),
                    _dumps({**decision, "id": decision_id}),
                ),
            )
        return decision_id

    def resolve_decision(self, decision_id: str, accepted: bool) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE decisions SET accepted=?, resolved_at=? WHERE id=?",
                (int(accepted), now_iso(), decision_id),
            )

    def list_decisions(self, *, resolved_only: bool = False, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM decisions"
        clauses, params = [], []
        if resolved_only:
            clauses.append("accepted IS NOT NULL")
        if run_id:
            clauses.append("run_id=?")
            params.append(run_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.connect() as db:
            return self._rows(db.execute(query + " ORDER BY created_at", params).fetchall())

    def add_qc(self, run_id: str, iteration: int, payload: dict[str, Any]) -> str:
        qc_id = payload.get("id") or new_id("qc")
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO qc_results(id, run_id, iteration, created_at, json) VALUES(?,?,?,?,?)",
                (qc_id, run_id, iteration, now_iso(), _dumps(payload)),
            )
        return qc_id

    def list_qc(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return self._rows(
                db.execute("SELECT * FROM qc_results WHERE run_id=? ORDER BY iteration", (run_id,)).fetchall()
            )

    def add_feedback(self, *, kind: str, target: str | None, project_id: str | None, run_id: str | None, note: str = "") -> str:
        feedback_id = new_id("fb")
        with self.connect() as db:
            db.execute(
                "INSERT INTO feedback(id, project_id, run_id, target, kind, note, created_at) VALUES(?,?,?,?,?,?,?)",
                (feedback_id, project_id, run_id, target, kind, note, now_iso()),
            )
        return feedback_id

    def list_feedback(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            if project_id:
                rows = db.execute("SELECT * FROM feedback WHERE project_id=? ORDER BY created_at", (project_id,))
            else:
                rows = db.execute("SELECT * FROM feedback ORDER BY created_at")
            return self._rows(rows.fetchall())
