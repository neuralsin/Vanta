from __future__ import annotations

import sqlite3

import pytest

from vanta.core.schemas import EventStatus, Job, JobStatus, RunManifest, RunStatus, VantaEvent
from vanta.storage.event_store import EventStore


def test_event_store_persists_runs_events_and_job_transitions(tmp_path) -> None:
    store = EventStore(tmp_path / "vanta.sqlite3")
    manifest = RunManifest(project_id="project-a", artifact_root=str(tmp_path / "runs"))
    store.create_run(manifest)
    job = Job(run_id=manifest.run_id, kind="analysis")
    store.create_job(job)
    running = store.transition_job(job.id, JobStatus.RUNNING)
    assert running.attempts == 1
    with pytest.raises(ValueError):
        store.transition_job(job.id, JobStatus.QUEUED)
    event = VantaEvent(
        run_id=manifest.run_id, stage="probe", action="capability_scan", status=EventStatus.VERIFIED
    )
    sequence = store.add_event(event)
    listed = store.list_events(run_id=manifest.run_id)
    assert listed[0]["sequence"] == sequence
    assert listed[0]["action"] == "capability_scan"
    assert len(store.list_runs()) == 1


def test_event_requires_existing_run(tmp_path) -> None:
    store = EventStore(tmp_path / "vanta.sqlite3")
    event = VantaEvent(run_id="missing", stage="x", action="y", status=EventStatus.FAILED)
    with pytest.raises(sqlite3.IntegrityError):
        store.add_event(event)


def test_run_lifecycle_rejects_skipped_and_terminal_transitions(tmp_path) -> None:
    store = EventStore(tmp_path / "vanta.sqlite3")
    manifest = RunManifest(project_id="project-a", artifact_root=str(tmp_path / "runs"))
    store.create_run(manifest)
    with pytest.raises(ValueError, match="QUEUED -> PAUSED"):
        store.transition_run(manifest.run_id, RunStatus.PAUSED)
    assert store.transition_run(manifest.run_id, RunStatus.RUNNING) == RunStatus.RUNNING
    assert store.transition_run(manifest.run_id, RunStatus.PAUSED) == RunStatus.PAUSED
    assert store.transition_run(manifest.run_id, RunStatus.CANCELLED) == RunStatus.CANCELLED
    with pytest.raises(ValueError, match="CANCELLED -> RUNNING"):
        store.transition_run(manifest.run_id, RunStatus.RUNNING)
