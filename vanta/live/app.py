"""Local Mission Control v0: inspect capability gates and persistent run events."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from vanta.core.policy import validate_edg
from vanta.core.schemas import EditDecisionGraph, RunManifest, VantaEvent
from vanta.resolve.adapter import ResolveScriptingAdapter
from vanta.storage.artifacts import ArtifactStore
from vanta.storage.event_store import EventStore

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("VANTA_DATA_DIR", ROOT / "data")).resolve()
CAPABILITIES_PATH = ROOT / "configs" / "capabilities.json"
store = EventStore(DATA_DIR / "vanta.sqlite3")
artifact_store = ArtifactStore(DATA_DIR)
_resolve_root = Path(
    os.environ.get("VANTA_RESOLVE_ROOT", "C:/Program Files/Blackmagic Design/DaVinci Resolve")
)
resolve_adapter = ResolveScriptingAdapter(_resolve_root)
app = FastAPI(title="VANTA Mission Control", version="0.1.0")


def load_capabilities() -> dict:
    if not CAPABILITIES_PATH.exists():
        raise HTTPException(status_code=503, detail="Run scripts/phase0_probe.py first")
    try:
        return json.loads(CAPABILITIES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=503, detail=f"Capabilities report is invalid: {exc}"
        ) from exc


@app.get("/", response_class=HTMLResponse)
def mission_control() -> str:
    return (ROOT / "ui" / "mission_control.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": app.version,
        "data_dir": str(DATA_DIR),
        "resolve_adapter": "unavailable-until-gated",
    }


@app.get("/api/capabilities")
def capabilities() -> dict:
    return load_capabilities()


@app.get("/api/resolve/status")
def resolve_status() -> dict:
    """Perform a bounded read-only local scripting connection check."""
    return resolve_adapter.capabilities()


@app.get("/api/runs")
def runs(limit: int = 50) -> list[dict]:
    return store.list_runs(limit)


@app.get("/api/events")
def events(run_id: str | None = None, after: int = 0, limit: int = 200) -> list[dict]:
    return store.list_events(run_id=run_id, after=after, limit=limit)


@app.post("/api/runs", status_code=201)
def create_run(manifest: RunManifest) -> dict:
    try:
        saved = artifact_store.create_run(manifest)
        store.create_run(saved)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Could not create run: {exc}") from exc
    return saved.model_dump(mode="json")


@app.post("/api/runs/{run_id}/control")
def control_run(run_id: str, action: str) -> dict:
    if not store.get_run(run_id):
        raise HTTPException(status_code=404, detail="Run does not exist")
    raise HTTPException(
        status_code=409,
        detail=(
            "No live executor is attached. This build can persist and inspect a run, "
            "but it will not record fake pause/step/stop signals before a gate-verified executor exists."
        ),
    )


@app.post("/api/events", status_code=201)
def create_event(event: VantaEvent) -> dict:
    try:
        sequence = store.add_event(event)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Could not persist event: {exc}") from exc
    return {"sequence": sequence, **event.model_dump(mode="json")}


@app.post("/api/edg/validate")
def validate_edg_endpoint(graph: EditDecisionGraph) -> dict:
    errors = validate_edg(graph, capabilities=load_capabilities(), project_root=ROOT)
    return {"valid": not errors, "executable": not errors, "errors": errors, "edg_id": graph.id}


@app.websocket("/ws/events")
async def event_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    run_id = websocket.query_params.get("run_id")
    try:
        after = max(0, int(websocket.query_params.get("after", "0")))
        while True:
            items = store.list_events(run_id=run_id, after=after, limit=100)
            for item in items:
                await websocket.send_json(item)
                after = max(after, int(item["sequence"]))
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, ValueError):
        return
