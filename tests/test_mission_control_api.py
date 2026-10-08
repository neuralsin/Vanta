from __future__ import annotations

import asyncio

import httpx

import vanta.live.app as api
from vanta.storage.artifacts import ArtifactStore
from vanta.storage.event_store import EventStore


def test_run_creation_creates_reviewable_artifacts_without_fake_live_controls(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(api, "DATA_DIR", tmp_path)
    monkeypatch.setattr(api, "store", EventStore(tmp_path / "vanta.sqlite3"))
    monkeypatch.setattr(api, "artifact_store", ArtifactStore(tmp_path))

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/runs",
                json={
                    "project_id": "test-project",
                    "artifact_root": "managed-by-vanta",
                    "run_mode": "STEP",
                },
            )
            assert created.status_code == 201
            data = created.json()
            assert (tmp_path / "runs" / data["run_id"] / "manifest.json").is_file()
            run_id = data["run_id"]
            control = await client.post(f"/api/runs/{run_id}/control?action=pause")
            assert control.status_code == 409
            assert "No live executor" in control.json()["detail"]
            assert api.store.get_run(run_id)["status"] == "QUEUED"
            assert api.store.list_events(run_id=run_id) == []

    asyncio.run(scenario())


def test_run_ids_cannot_escape_artifact_root() -> None:
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/runs",
                json={
                    "run_id": "run_..\\..\\outside",
                    "project_id": "p",
                    "artifact_root": "ignored",
                },
            )
            assert response.status_code == 422

    asyncio.run(scenario())
