"""Version-matched embedded-Python broker for a closed VANTA Resolve protocol.

It never evaluates caller text. Mutation actions are disabled unless a separate,
explicit disposable-gate harness enables them for this child process.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

_repo_root = os.environ.get("VANTA_BROKER_ROOT")
if _repo_root:
    sys.path.insert(0, _repo_root)

from vanta.resolve.protocol import ProtocolError, validate_request


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _connect() -> tuple[Any | None, str | None]:
    module_path = os.environ.get("VANTA_RESOLVE_MODULE_PATH")
    if module_path:
        sys.path.insert(0, module_path)
    try:
        import DaVinciResolveScript  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        return None, f"Could not load Resolve scripting module: {type(exc).__name__}: {exc}"
    try:
        resolve = DaVinciResolveScript.scriptapp("Resolve")
    except Exception as exc:  # noqa: BLE001
        return None, f"Could not connect to Resolve: {type(exc).__name__}: {exc}"
    if not resolve:
        return None, "Resolve is not running or external scripting is not enabled for Local access"
    return resolve, None


def _project(resolve: Any) -> tuple[Any, Any]:
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager else None
    if not manager or not project:
        raise RuntimeError("Resolve has no current project")
    return manager, project


def _timeline_entries(project: Any) -> list[tuple[str, str, Any]]:
    entries: list[tuple[str, str, Any]] = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline:
            entries.append((str(timeline.GetUniqueId()), str(timeline.GetName()), timeline))
    return entries


def _timeline_by_id(project: Any, timeline_id: str) -> Any:
    for candidate_id, _, timeline in _timeline_entries(project):
        if candidate_id == timeline_id:
            return timeline
    raise RuntimeError("timeline ID was not found in the current project")


def _norm_path(value: str) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))


def _media_items(folder: Any) -> list[Any]:
    items = list(folder.GetClipList() or [])
    for child in folder.GetSubFolderList() or []:
        items.extend(_media_items(child))
    return items


def _media_by_source_path(project: Any, source_path: str) -> Any:
    media_pool = project.GetMediaPool()
    root = media_pool.GetRootFolder() if media_pool else None
    if not root:
        raise RuntimeError("Resolve media pool root is unavailable")
    wanted = _norm_path(source_path)
    matches: list[Any] = []
    for clip in _media_items(root):
        properties = clip.GetClipProperty() or {}
        file_path = properties.get("File Path") if isinstance(properties, dict) else None
        if isinstance(file_path, str) and _norm_path(file_path) == wanted:
            matches.append(clip)
    if not matches:
        raise RuntimeError("no media-pool item matches the EDG source path")
    if len(matches) != 1:
        raise RuntimeError("more than one media-pool item matches the EDG source path")
    return matches[0]


def _status(resolve: Any) -> dict[str, Any]:
    version = resolve.GetVersion() if hasattr(resolve, "GetVersion") else None
    try:
        _, project = _project(resolve)
    except RuntimeError:
        project = None
    timeline = project.GetCurrentTimeline() if project else None
    return {
        "connected": True,
        "version": _json_safe(version),
        "project_name": project.GetName() if project else None,
        "project_id": project.GetUniqueId() if project else None,
        "timeline_name": timeline.GetName() if timeline else None,
        "timeline_id": timeline.GetUniqueId() if timeline else None,
    }


def _timeline_state(project: Any, timeline: Any) -> dict[str, Any]:
    return {
        "name": str(timeline.GetName()),
        "timeline_id": str(timeline.GetUniqueId()),
        "video_track_count": int(timeline.GetTrackCount("video") or 0),
        "audio_track_count": int(timeline.GetTrackCount("audio") or 0),
        "start_frame": int(timeline.GetStartFrame()),
        "end_frame": int(timeline.GetEndFrame()),
    }


def _read_state(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    scope = params["scope"]
    if scope == "project":
        _, project = _project(resolve)
        state = _status(resolve)
        state["timelines"] = [
            {"timeline_id": timeline_id, "name": name}
            for timeline_id, name, _ in _timeline_entries(project)
        ]
        return state
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    if scope == "timeline":
        return _timeline_state(project, timeline)
    if scope == "timeline_item":
        wanted_path = _norm_path(params["source_path"])
        for item in timeline.GetItemListInTrack("video", params["track_index"]) or []:
            media = item.GetMediaPoolItem()
            properties = media.GetClipProperty() if media else {}
            file_path = properties.get("File Path") if isinstance(properties, dict) else None
            if (
                isinstance(file_path, str)
                and _norm_path(file_path) == wanted_path
                and int(item.GetSourceStartFrame()) == params["source_in_frame"]
                and int(item.GetStart()) == params["timeline_start_frame"]
            ):
                return {
                    "source_path": str(Path(file_path).resolve(strict=False)),
                    "source_in_frame": int(item.GetSourceStartFrame()),
                    "source_out_frame": int(item.GetSourceEndFrame()),
                    "timeline_start_frame": int(item.GetStart()),
                    "track_index": params["track_index"],
                }
        return {"found": False}
    for frame, marker in (timeline.GetMarkers() or {}).items():
        if isinstance(marker, dict) and marker.get("customData") == params["custom_data"]:
            return {
                "frame": int(frame),
                "color": marker.get("color"),
                "name": marker.get("name"),
                "note": marker.get("note"),
                "custom_data": marker.get("customData"),
            }
    return {"found": False}


def _snapshot(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    manager, project = _project(resolve)
    export_path = Path(params["export_path"]).expanduser().resolve()
    if export_path.suffix.casefold() != ".drp":
        raise RuntimeError("snapshot export path must end in .drp")
    if export_path.exists():
        raise RuntimeError("snapshot export path already exists")
    if not export_path.parent.is_dir():
        raise RuntimeError("snapshot parent directory does not exist")
    baseline_current = project.GetCurrentTimeline()
    if not baseline_current:
        raise RuntimeError("a current baseline timeline is required before VANTA may write")
    exported = bool(manager.ExportProject(project.GetName(), str(export_path), True))
    if not exported or not export_path.is_file() or export_path.stat().st_size == 0:
        raise RuntimeError("Resolve did not create a nonempty project export")
    return {
        "snapshot_id": params["label"],
        "project_id": str(project.GetUniqueId()),
        "baseline_current_timeline_id": str(baseline_current.GetUniqueId()),
        "baseline_timeline_ids": [timeline_id for timeline_id, _, _ in _timeline_entries(project)],
        "snapshot_path": str(export_path),
        "verified": True,
    }


def _create_timeline(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    name = params["name"]
    if not name.startswith("VANTA_"):
        raise RuntimeError("broker only creates VANTA-owned timelines")
    before = {timeline_id for timeline_id, _, _ in _timeline_entries(project)}
    if any(existing_name == name for _, existing_name, _ in _timeline_entries(project)):
        raise RuntimeError("refusing to create a timeline whose name already exists")
    media_pool = project.GetMediaPool()
    timeline = media_pool.CreateEmptyTimeline(name) if media_pool else None
    if not timeline:
        raise RuntimeError("Resolve did not create the requested empty timeline")
    timeline_id = str(timeline.GetUniqueId())
    after = {candidate_id for candidate_id, _, _ in _timeline_entries(project)}
    if timeline_id in before or timeline_id not in after or str(timeline.GetName()) != name:
        raise RuntimeError("new timeline identity or name did not verify")
    if not project.SetCurrentTimeline(timeline):
        raise RuntimeError("Resolve did not select the VANTA-created timeline")
    return {"timeline_id": timeline_id, "name": name, "verified": True}


def _append_clip(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    if not project.SetCurrentTimeline(timeline):
        raise RuntimeError("Resolve did not select the VANTA timeline for append")
    media_pool = project.GetMediaPool()
    clip = _media_by_source_path(project, params["source_path"])
    clip_info = {
        "mediaPoolItem": clip,
        "startFrame": params["source_in_frame"],
        "endFrame": params["source_out_frame"],
        "recordFrame": params["timeline_start_frame"],
        "trackIndex": params["track_index"],
    }
    items = media_pool.AppendToTimeline([clip_info]) if media_pool else None
    if not items:
        raise RuntimeError("Resolve did not return an appended timeline item")
    return {"timeline_id": params["timeline_id"], "accepted": True}


def _add_marker(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    if not timeline.AddMarker(
        params["frame"], params["color"], params["name"], params["note"], 1, params["custom_data"]
    ):
        raise RuntimeError("Resolve did not create the VANTA breadcrumb marker")
    return {"timeline_id": params["timeline_id"], "accepted": True}


def _rollback_vanta_timelines(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    entries = {
        timeline_id: (name, timeline) for timeline_id, name, timeline in _timeline_entries(project)
    }
    created = params["created_timeline_ids"]
    if any(timeline_id not in entries for timeline_id in created):
        raise RuntimeError(
            "refusing rollback because a VANTA-created timeline cannot be identified exactly"
        )
    targets = [entries[timeline_id][1] for timeline_id in created]
    if any(not entries[timeline_id][0].startswith("VANTA_") for timeline_id in created):
        raise RuntimeError("refusing rollback because a target is not a VANTA-owned timeline")
    baseline = _timeline_by_id(project, params["baseline_current_timeline_id"])
    media_pool = project.GetMediaPool()
    if not media_pool or not media_pool.DeleteTimelines(targets):
        raise RuntimeError("Resolve did not delete every VANTA-created timeline during rollback")
    if not project.SetCurrentTimeline(baseline):
        raise RuntimeError("Resolve did not restore the baseline timeline selection")
    remaining = {timeline_id for timeline_id, _, _ in _timeline_entries(project)}
    if any(timeline_id in remaining for timeline_id in created):
        raise RuntimeError("rollback verification found a VANTA-created timeline still present")
    return {"restored_snapshot_id": params["snapshot_id"], "verified": True}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    try:
        action, params = validate_request(
            request, allow_mutations=os.environ.get("VANTA_RESOLVE_GATE_MUTATIONS") == "1"
        )
    except ProtocolError as exc:
        return {"ok": False, "error": str(exc)}
    resolve, error = _connect()
    if error:
        return {"ok": True, "result": {"connected": False, "reason": error}}
    try:
        operations = {
            "status": lambda: _status(resolve),
            "read_state": lambda: _read_state(resolve, params),
            "snapshot": lambda: _snapshot(resolve, params),
            "create_timeline": lambda: _create_timeline(resolve, params),
            "append_clip": lambda: _append_clip(resolve, params),
            "add_marker": lambda: _add_marker(resolve, params),
            "rollback_vanta_timelines": lambda: _rollback_vanta_timelines(resolve, params),
        }
        return {"ok": True, "result": _json_safe(operations[action]())}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(limit=4),
        }


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise TypeError("request must be a JSON object")
            response = handle(request)
        except Exception as exc:  # noqa: BLE001
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
