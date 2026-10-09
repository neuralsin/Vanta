"""Version-matched embedded-Python broker for the closed VANTA Resolve protocol.

Runs under Resolve's own Python (ResolvePython.exe). It never evaluates caller
text: each action maps to one fixed function that writes, reads back and
returns observed state. Write actions require the session to be started with
``VANTA_RESOLVE_WRITES=1`` (or the legacy gate flag) by the adapter.
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

_RESOLVE: Any = None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _connect(force: bool = False) -> tuple[Any | None, str | None]:
    global _RESOLVE
    if _RESOLVE is not None and not force:
        try:
            if _RESOLVE.GetVersionString():
                return _RESOLVE, None
        except Exception:
            _RESOLVE = None
    module_path = os.environ.get("VANTA_RESOLVE_MODULE_PATH")
    if module_path and module_path not in sys.path:
        sys.path.insert(0, module_path)
    try:
        import DaVinciResolveScript  # type: ignore[import-not-found]
    except Exception as exc:
        return None, f"Could not load Resolve scripting module: {type(exc).__name__}: {exc}"
    try:
        resolve = DaVinciResolveScript.scriptapp("Resolve")
    except Exception as exc:
        return None, f"Could not connect to Resolve: {type(exc).__name__}: {exc}"
    if not resolve:
        return None, "Resolve is not running or external scripting is not enabled for Local access"
    _RESOLVE = resolve
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


def _clip_path(clip: Any) -> str | None:
    try:
        props = clip.GetClipProperty() or {}
    except Exception:
        return None
    value = props.get("File Path") if isinstance(props, dict) else None
    return value if isinstance(value, str) and value else None


def _media_by_source_path(project: Any, source_path: str) -> Any:
    media_pool = project.GetMediaPool()
    root = media_pool.GetRootFolder() if media_pool else None
    if not root:
        raise RuntimeError("Resolve media pool root is unavailable")
    wanted = _norm_path(source_path)
    matches = [clip for clip in _media_items(root) if (_clip_path(clip) and _norm_path(_clip_path(clip)) == wanted)]
    if not matches:
        raise RuntimeError("no media-pool item matches the EDG source path")
    if len(matches) != 1:
        raise RuntimeError("more than one media-pool item matches the EDG source path")
    return matches[0]


def _all_items(timeline: Any) -> list[tuple[str, int, Any]]:
    out = []
    for track_type in ("video", "audio", "subtitle"):
        for index in range(1, int(timeline.GetTrackCount(track_type) or 0) + 1):
            for item in timeline.GetItemListInTrack(track_type, index) or []:
                out.append((track_type, index, item))
    return out


def _item_by_id(timeline: Any, item_id: str) -> tuple[str, int, Any]:
    for track_type, index, item in _all_items(timeline):
        if str(item.GetUniqueId()) == item_id:
            return track_type, index, item
    raise RuntimeError(f"timeline item {item_id} was not found")


def _item_state(timeline: Any, track_type: str, index: int, item: Any) -> dict[str, Any]:
    start_offset = int(timeline.GetStartFrame())
    media = item.GetMediaPoolItem()
    state = {
        "item_id": str(item.GetUniqueId()),
        "name": str(item.GetName()),
        "track_type": track_type,
        "track_index": index,
        "start_frame": int(item.GetStart()) - start_offset,
        "end_frame": int(item.GetEnd()) - start_offset,
        "duration": int(item.GetDuration()),
        "source_path": _clip_path(media) if media else None,
        "clip_color": item.GetClipColor() if hasattr(item, "GetClipColor") else None,
    }
    try:
        state["source_start_frame"] = int(item.GetSourceStartFrame())
        state["source_end_frame"] = int(item.GetSourceEndFrame())
    except Exception:
        pass
    if track_type == "video":
        try:
            state["fusion_comps"] = list(item.GetFusionCompNameList() or [])
        except Exception:
            state["fusion_comps"] = []
    return state


def _status(resolve: Any) -> dict[str, Any]:
    try:
        _, project = _project(resolve)
    except RuntimeError:
        project = None
    timeline = project.GetCurrentTimeline() if project else None
    product = resolve.GetProductName() if hasattr(resolve, "GetProductName") else None
    return {
        "connected": True,
        "version": _json_safe(resolve.GetVersion() if hasattr(resolve, "GetVersion") else None),
        "version_string": resolve.GetVersionString() if hasattr(resolve, "GetVersionString") else None,
        "product": product,
        "studio": bool(product and "studio" in str(product).casefold()),
        "page": resolve.GetCurrentPage() if hasattr(resolve, "GetCurrentPage") else None,
        "project_name": project.GetName() if project else None,
        "project_id": project.GetUniqueId() if project else None,
        "timeline_name": timeline.GetName() if timeline else None,
        "timeline_id": timeline.GetUniqueId() if timeline else None,
        "rendering": bool(project.IsRenderingInProgress()) if project else False,
    }


def _timeline_state(timeline: Any) -> dict[str, Any]:
    settings = {}
    try:
        settings = timeline.GetSettings() or {}
    except Exception:
        pass
    return {
        "name": str(timeline.GetName()),
        "timeline_id": str(timeline.GetUniqueId()),
        "video_track_count": int(timeline.GetTrackCount("video") or 0),
        "audio_track_count": int(timeline.GetTrackCount("audio") or 0),
        "start_frame": int(timeline.GetStartFrame()),
        "end_frame": int(timeline.GetEndFrame()),
        "width": settings.get("timelineResolutionWidth"),
        "height": settings.get("timelineResolutionHeight"),
        "fps": settings.get("timelineFrameRate"),
    }


def _read_state(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    scope = params["scope"]
    if scope == "page":
        return {"page": resolve.GetCurrentPage()}
    _, project = _project(resolve)
    if scope == "project":
        state = _status(resolve)
        state["timelines"] = [{"timeline_id": tid, "name": name} for tid, name, _ in _timeline_entries(project)]
        return state
    if scope == "media_pool":
        root = project.GetMediaPool().GetRootFolder()
        return {"items": [{"name": c.GetName(), "media_id": c.GetMediaId(), "path": _clip_path(c)} for c in _media_items(root)]}
    if scope == "render_jobs":
        return {"jobs": _json_safe(project.GetRenderJobList() or []), "rendering": bool(project.IsRenderingInProgress())}
    timeline = _timeline_by_id(project, params["timeline_id"])
    if scope == "timeline":
        return _timeline_state(timeline)
    if scope == "timeline_items":
        return {"items": [_item_state(timeline, t, i, it) for t, i, it in _all_items(timeline) if not params.get("track_type") or t == params["track_type"]]}
    if scope == "markers":
        return {"markers": {str(k): _json_safe(v) for k, v in (timeline.GetMarkers() or {}).items()}}
    if scope == "item":
        return _item_state(timeline, *_item_by_id(timeline, params["item_id"]))
    if scope == "grade":
        _, _, item = _item_by_id(timeline, params["item_id"])
        graph = item.GetNodeGraph()
        nodes = int(graph.GetNumNodes() or 0) if graph else 0
        node = params.get("node_index")
        return {
            "nodes": nodes,
            "labels": [graph.GetNodeLabel(i) for i in range(1, nodes + 1)] if graph else [],
            "luts": {str(i): graph.GetLUT(i) for i in range(1, nodes + 1)} if graph else {},
            "lut": graph.GetLUT(node) if graph and node else None,
        }
    if scope == "timeline_item":
        wanted_path = _norm_path(params["source_path"])
        start_offset = int(timeline.GetStartFrame())
        for item in timeline.GetItemListInTrack("video", params["track_index"]) or []:
            media = item.GetMediaPoolItem()
            file_path = _clip_path(media) if media else None
            if file_path and _norm_path(file_path) == wanted_path and int(item.GetStart()) - start_offset == params["timeline_start_frame"]:
                end = int(item.GetSourceEndFrame())
                start = int(item.GetSourceStartFrame())
                # Resolve reports an inclusive source end on some versions; normalise to exclusive
                duration = int(item.GetDuration())
                exclusive = end + 1 if end - start + 1 == duration else end
                return {
                    "source_path": str(Path(file_path).resolve(strict=False)),
                    "source_in_frame": start,
                    "source_out_frame": exclusive,
                    "timeline_start_frame": int(item.GetStart()) - start_offset,
                    "track_index": params["track_index"],
                    "item_id": str(item.GetUniqueId()),
                    "raw_source_end_frame": end,
                }
        return {"found": False}
    for frame, marker in (timeline.GetMarkers() or {}).items():
        if isinstance(marker, dict) and marker.get("customData") == params["custom_data"]:
            return {"frame": int(frame), "color": marker.get("color"), "name": marker.get("name"), "note": marker.get("note"), "custom_data": marker.get("customData")}
    return {"found": False}


def _timeline_snapshot(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    items = []
    for track_type, index, item in _all_items(timeline):
        state = _item_state(timeline, track_type, index, item)
        if track_type == "video":
            try:
                graph = item.GetNodeGraph()
                n = int(graph.GetNumNodes() or 0)
                state["nodes"] = n
                state["luts"] = {str(i): graph.GetLUT(i) for i in range(1, n + 1)}
            except Exception:
                pass
            try:
                state["speed"] = _json_safe(item.GetSpeed())
                state["properties"] = {k: v for k, v in (item.GetProperties() or {}).items() if k in ("Pan", "Tilt", "ZoomX", "ZoomY", "RotationAngle", "Opacity")}
            except Exception:
                pass
        try:
            state["markers"] = _json_safe(item.GetMarkers() or {})
        except Exception:
            pass
        items.append(state)
    return {**_timeline_state(timeline), "items": items, "markers": {str(k): _json_safe(v) for k, v in (timeline.GetMarkers() or {}).items()}}


# ------------------------------------------------------------------ writes
def _snapshot(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    manager, project = _project(resolve)
    export_path = Path(params["export_path"]).expanduser().resolve()
    if export_path.exists():
        raise RuntimeError("snapshot export path already exists")
    if not export_path.parent.is_dir():
        raise RuntimeError("snapshot parent directory does not exist")
    project.SaveProject() if hasattr(project, "SaveProject") else manager.SaveProject()
    baseline_current = project.GetCurrentTimeline()
    exported = bool(manager.ExportProject(project.GetName(), str(export_path), True))
    if not exported or not export_path.is_file() or export_path.stat().st_size == 0:
        raise RuntimeError("Resolve did not create a nonempty project export")
    return {
        "snapshot_id": params["label"],
        "project_id": str(project.GetUniqueId()),
        "project_name": str(project.GetName()),
        "baseline_current_timeline_id": str(baseline_current.GetUniqueId()) if baseline_current else None,
        "baseline_timeline_ids": [tid for tid, _, _ in _timeline_entries(project)],
        "snapshot_path": str(export_path),
        "verified": True,
    }


def _create_timeline(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    name = params["name"]
    before = {tid for tid, _, _ in _timeline_entries(project)}
    if any(existing == name for _, existing, _ in _timeline_entries(project)):
        raise RuntimeError("refusing to create a timeline whose name already exists")
    media_pool = project.GetMediaPool()
    timeline = media_pool.CreateEmptyTimeline(name) if media_pool else None
    if not timeline:
        raise RuntimeError("Resolve did not create the requested empty timeline")
    timeline_id = str(timeline.GetUniqueId())
    after = {tid for tid, _, _ in _timeline_entries(project)}
    if timeline_id in before or timeline_id not in after or str(timeline.GetName()) != name:
        raise RuntimeError("new timeline identity or name did not verify")
    settings = {}
    if params.get("width") and params.get("height"):
        settings.update(
            {
                "useCustomSettings": "1",
                "timelineResolutionWidth": str(params["width"]),
                "timelineResolutionHeight": str(params["height"]),
                "timelineOutputResMatchTimelineRes": "1",
                # fill the frame (no letterboxing) so VANTA's subject-aware pan decides the crop
                "timelineInputResMismatchBehavior": "scaleToCrop",
            }
        )
    if params.get("fps"):
        settings.update({"useCustomSettings": "1", "timelineFrameRate": params["fps"]})
    applied = None
    if settings:
        if hasattr(timeline, "SetSettings"):
            applied = bool(timeline.SetSettings(settings))
        else:
            applied = all(timeline.SetSetting(k, v) for k, v in settings.items())
    if not project.SetCurrentTimeline(timeline):
        raise RuntimeError("Resolve did not select the VANTA-created timeline")
    state = _timeline_state(timeline)
    if params.get("width") and str(state.get("width")) != str(params["width"]):
        raise RuntimeError(f"timeline width read back {state.get('width')} != {params['width']}")
    if params.get("height") and str(state.get("height")) != str(params["height"]):
        raise RuntimeError(f"timeline height read back {state.get('height')} != {params['height']}")
    try:
        science = str(project.GetSetting("colorScienceMode") or "")
    except Exception:
        science = ""
    return {"timeline_id": timeline_id, "name": name, "verified": True, "settings_applied": applied, "state": state, "color_science": science}


def _append(project: Any, timeline: Any, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not project.SetCurrentTimeline(timeline):
        raise RuntimeError("Resolve did not select the VANTA timeline for append")
    media_pool = project.GetMediaPool()
    start_offset = int(timeline.GetStartFrame())
    infos = []
    for clip in clips:
        info = {
            "mediaPoolItem": _media_by_source_path(project, clip["source_path"]),
            "startFrame": clip["source_in_frame"],
            "endFrame": clip["source_out_frame"],
            "recordFrame": start_offset + clip["timeline_start_frame"],
            "trackIndex": clip["track_index"],
        }
        if clip.get("media_type"):
            info["mediaType"] = clip["media_type"]
        infos.append(info)
    items = media_pool.AppendToTimeline(infos) if media_pool else None
    if not items or len(items) < len(clips):
        raise RuntimeError(f"Resolve appended {len(items or [])} of {len(clips)} clips")
    observed = []
    for clip, item in zip(clips, items, strict=False):
        start = int(item.GetStart()) - start_offset
        src_start = int(item.GetSourceStartFrame()) if clip.get("media_type") != 2 else clip["source_in_frame"]
        src_end = int(item.GetSourceEndFrame()) if clip.get("media_type") != 2 else clip["source_out_frame"]
        duration = int(item.GetDuration())
        convention = "inclusive" if src_end - src_start + 1 == clip["source_out_frame"] - clip["source_in_frame"] else "exclusive"
        exclusive_end = src_end + 1 if convention == "inclusive" else src_end
        ok = start == clip["timeline_start_frame"] and src_start == clip["source_in_frame"] and exclusive_end == clip["source_out_frame"]
        observed.append({"item_id": str(item.GetUniqueId()), "timeline_start_frame": start, "source_in_frame": src_start, "source_out_frame": exclusive_end, "duration": duration, "end_convention": convention, "verified": ok})
    return observed


def _append_clip(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    observed = _append(project, timeline, [{k: params[k] for k in params if k != "timeline_id"}])
    return {"timeline_id": params["timeline_id"], "accepted": True, **observed[0]}


def _append_clips(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    observed = _append(project, timeline, params["clips"])
    return {"timeline_id": params["timeline_id"], "items": observed, "verified": all(o["verified"] for o in observed)}


def _add_marker(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    existing = timeline.GetMarkers() or {}
    if any(isinstance(m, dict) and m.get("customData") == params["custom_data"] for m in existing.values()):
        return {"timeline_id": params["timeline_id"], "accepted": True, "idempotent": True}
    if not timeline.AddMarker(params["frame"], params["color"], params["name"], params["note"], 1, params["custom_data"]):
        raise RuntimeError("Resolve did not create the VANTA breadcrumb marker")
    return {"timeline_id": params["timeline_id"], "accepted": True}


def _rollback_vanta_timelines(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    entries = {tid: (name, timeline) for tid, name, timeline in _timeline_entries(project)}
    created = params["created_timeline_ids"]
    if any(tid not in entries for tid in created):
        raise RuntimeError("refusing rollback because a VANTA-created timeline cannot be identified exactly")
    if any(not entries[tid][0].startswith("VANTA_") for tid in created):
        raise RuntimeError("refusing rollback because a target is not a VANTA-owned timeline")
    targets = [entries[tid][1] for tid in created]
    baseline = _timeline_by_id(project, params["baseline_current_timeline_id"])
    media_pool = project.GetMediaPool()
    if not project.SetCurrentTimeline(baseline):
        raise RuntimeError("Resolve did not restore the baseline timeline selection")
    if not media_pool or not media_pool.DeleteTimelines(targets):
        raise RuntimeError("Resolve did not delete every VANTA-created timeline during rollback")
    remaining = {tid for tid, _, _ in _timeline_entries(project)}
    if any(tid in remaining for tid in created):
        raise RuntimeError("rollback verification found a VANTA-created timeline still present")
    return {"restored_snapshot_id": params["snapshot_id"], "verified": True}


def _import_media(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    media_pool = project.GetMediaPool()
    root = media_pool.GetRootFolder()
    folder = next((f for f in root.GetSubFolderList() or [] if f.GetName() == params["folder_name"]), None)
    if folder is None:
        folder = media_pool.AddSubFolder(root, params["folder_name"])
    if not folder:
        raise RuntimeError("could not create the VANTA media-pool folder")
    existing = {_norm_path(p): c for c in _media_items(root) if (p := _clip_path(c))}
    todo = [p for p in params["paths"] if _norm_path(p) not in existing]
    imported = []
    if todo:
        media_pool.SetCurrentFolder(folder)
        imported = media_pool.ImportMedia([{"FilePath": p} for p in todo]) or []
    after = {_norm_path(p): c for c in _media_items(root) if (p := _clip_path(c))}
    missing = [p for p in params["paths"] if _norm_path(p) not in after]
    if missing:
        raise RuntimeError(f"Resolve did not import: {missing[:3]}")
    return {"verified": True, "imported": len(imported), "already_present": len(params["paths"]) - len(todo), "media": {p: after[_norm_path(p)].GetMediaId() for p in params["paths"]}}


def _ensure_tracks(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    for kind, wanted, sub in (("video", params["video_tracks"], None), ("audio", params["audio_tracks"], "stereo")):
        guard = 0
        while int(timeline.GetTrackCount(kind) or 0) < wanted and guard < 20:
            ok = timeline.AddTrack(kind, sub) if sub else timeline.AddTrack(kind)
            if not ok:
                raise RuntimeError(f"Resolve did not add a {kind} track")
            guard += 1
    state = _timeline_state(timeline)
    if state["video_track_count"] < params["video_tracks"] or state["audio_track_count"] < params["audio_tracks"]:
        raise RuntimeError("track counts did not verify")
    for kind, count in (("video", state["video_track_count"]), ("audio", state["audio_track_count"])):
        for index in range(1, count + 1):
            if timeline.GetIsTrackLocked(kind, index):
                raise RuntimeError(f"{kind} track {index} is locked; VANTA never writes to locked tracks")
    return {"verified": True, **state}


def _open_page(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    ok = bool(resolve.OpenPage(params["page"]))
    page = resolve.GetCurrentPage()
    return {"verified": ok and page == params["page"], "page": page}


def _set_current_timeline(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    ok = bool(project.SetCurrentTimeline(timeline))
    current = project.GetCurrentTimeline()
    return {"verified": ok and current and str(current.GetUniqueId()) == params["timeline_id"]}


def _item(resolve: Any, params: dict[str, Any]) -> tuple[Any, Any, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    _, _, item = _item_by_id(timeline, params["item_id"])
    return project, timeline, item


def _set_clip_color(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    ok = bool(item.SetClipColor(params["color"]))
    return {"verified": ok and item.GetClipColor() == params["color"], "color": item.GetClipColor()}


def _set_lut(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    graph = item.GetNodeGraph()
    if not graph or int(graph.GetNumNodes() or 0) < params["node_index"]:
        raise RuntimeError(f"node {params['node_index']} does not exist on this clip")
    ok = bool(graph.SetLUT(params["node_index"], params["lut_path"]))
    observed = graph.GetLUT(params["node_index"]) or ""
    verified = ok and (_norm_path(observed) == _norm_path(params["lut_path"]) or Path(observed).name == Path(params["lut_path"]).name)
    return {"verified": verified, "observed_lut": observed}


def _set_cdl(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    cdl = {"NodeIndex": str(params["node_index"]), "Slope": params["slope"], "Offset": params["offset"], "Power": params["power"], "Saturation": f"{params['saturation']:.5f}"}
    ok = bool(item.SetCDL(cdl))
    # SetCDL has no getter: the caller must verify through export_lut
    return {"accepted": ok, "verified": False, "needs_export_verification": True}


def _apply_drx(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    graph = item.GetNodeGraph()
    ok = bool(graph.ApplyGradeFromDRX(params["drx_path"], params["grade_mode"]))
    n = int(graph.GetNumNodes() or 0)
    return {"verified": ok and n >= 1, "nodes": n, "labels": [graph.GetNodeLabel(i) for i in range(1, n + 1)]}


def _capture_drx_template(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Export the grade of the clip under the playhead (the user's 8-node build) as a .drx still."""
    _, project = _project(resolve)
    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("open the timeline with your 8-node grade first")
    resolve.OpenPage("color")
    item = timeline.GetCurrentVideoItem()
    if item is None:
        raise RuntimeError("park the playhead on the clip that carries the 8-node grade")
    graph = item.GetNodeGraph()
    nodes = int(graph.GetNumNodes() or 0) if graph else 0
    labels = [graph.GetNodeLabel(i) for i in range(1, nodes + 1)] if graph else []
    still = timeline.GrabStill()
    if not still:
        raise RuntimeError("Resolve did not grab a still from the clip")
    album = project.GetGallery().GetCurrentStillAlbum()
    out = Path(params["export_dir"])
    out.mkdir(parents=True, exist_ok=True)
    before = set(out.glob("*.drx"))
    try:
        ok = bool(album.ExportStills([still], str(out), params["prefix"], "drx"))
    finally:
        try:
            album.DeleteStills([still])  # leave the user's gallery as it was
        except Exception:
            pass
    new = sorted(set(out.glob("*.drx")) - before, key=lambda p: p.stat().st_mtime)
    return {"verified": ok and bool(new), "drx_path": str(new[-1]) if new else None, "nodes": nodes, "labels": labels}


def _create_magic_mask(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Track a Magic Mask on the clip, then prove which node received it (the API cannot select nodes)."""
    _, _, item = _item(resolve, params)
    resolve.OpenPage("color")
    ok = bool(item.CreateMagicMask(params["mode"]))
    graph = item.GetNodeGraph()
    nodes = int(graph.GetNumNodes() or 0) if graph else 0
    with_mask = [i for i in range(1, nodes + 1) if any("magic" in str(tool).casefold() for tool in (graph.GetToolsInNode(i) or []))]
    return {"accepted": ok, "nodes_with_mask": with_mask, "verified": ok and with_mask == [params["node_index"]]}


def _set_node_enabled(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    ok = bool(item.GetNodeGraph().SetNodeEnabled(params["node_index"], params["enabled"]))
    return {"accepted": ok, "verified": False, "needs_export_verification": True}


def _export_lut(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    kind = {17: resolve.EXPORT_LUT_17PTCUBE, 33: resolve.EXPORT_LUT_33PTCUBE, 65: resolve.EXPORT_LUT_65PTCUBE}[params["size"]]
    target = Path(params["export_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    ok = bool(item.ExportLUT(kind, str(target)))
    return {"verified": ok and target.is_file() and target.stat().st_size > 0, "path": str(target)}


def _import_fusion_comp(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    before = int(item.GetFusionCompCount() or 0)
    comp = item.ImportFusionComp(params["comp_path"])
    after = int(item.GetFusionCompCount() or 0)
    names = list(item.GetFusionCompNameList() or [])
    if comp is None or after != before + 1:
        raise RuntimeError("Fusion comp import did not add exactly one comp to the clip")
    new_name = names[-1] if names else None
    wanted = params.get("comp_name")
    if new_name and wanted and wanted != new_name:
        # Resolve's default "Composition N" names can repeat after deletions; VANTA comps get unique names
        if wanted in names:
            raise RuntimeError(f"a comp named {wanted} already exists on the clip")
        if item.RenameFusionCompByName(new_name, wanted):
            new_name = wanted
    if new_name:
        item.LoadFusionCompByName(new_name)
    return {"verified": True, "comp_count": after, "comp_name": new_name}


def _add_transition(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    created = item.AddTransition({"type": params["type"], "category": "simple", "position": params["position"], "alignment": params["alignment"], "duration": params["duration"]})
    if created is None:
        raise RuntimeError("Resolve did not create the transition (media handles may be too short)")
    duration = int(created.GetDuration()) if hasattr(created, "GetDuration") else None
    return {"verified": duration == params["duration"], "duration": duration, "transition_id": str(created.GetUniqueId()) if hasattr(created, "GetUniqueId") else None}


def _set_speed(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    ok = bool(item.SetSpeed({"Percentage": params["percentage"], "RippleTimeline": False}))
    observed = item.GetSpeed() or {}
    value = float(observed.get("Percentage", -1))
    return {"verified": ok and abs(value - params["percentage"]) < 0.05, "observed": value}


def _set_fades(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    ok = bool(item.SetFades({"FadeIn": params["fade_in"], "FadeOut": params["fade_out"]}))
    observed = item.GetFades() or {}
    return {"verified": ok and int(observed.get("FadeIn", -1)) == params["fade_in"] and int(observed.get("FadeOut", -1)) == params["fade_out"], "observed": _json_safe(observed)}


def _set_properties(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    ok = bool(item.SetProperties(params["properties"]))
    observed = item.GetProperties() or {}
    mismatch = {k: observed.get(k) for k, v in params["properties"].items() if not isinstance(v, bool) and abs(float(observed.get(k, 1e9)) - float(v)) > 1e-3}
    return {"verified": ok and not mismatch, "mismatch": _json_safe(mismatch)}


def _add_clip_mattes(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    clip = _media_by_source_path(project, params["source_path"])
    storage = resolve.GetMediaStorage()
    ok = bool(storage.AddClipMattesToMediaPool(clip, params["matte_paths"]))
    listed = project.GetMediaPool().GetClipMatteList(clip) or []
    return {"verified": ok and len(listed) > 0, "mattes": _json_safe(listed)}


def _render_start(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    if not project.SetCurrentTimeline(timeline):
        raise RuntimeError("could not select the timeline to render")
    if params.get("preset"):
        project.LoadRenderPreset(params["preset"])
    if not project.SetCurrentRenderFormatAndCodec(params.get("format", "mp4"), params.get("codec", "H264")):
        raise RuntimeError("Resolve rejected the render format/codec")
    target = Path(params["target_dir"])
    target.mkdir(parents=True, exist_ok=True)
    settings: dict[str, Any] = {
        "TargetDir": str(target),
        "CustomName": params["custom_name"],
        "FormatWidth": params["width"],
        "FormatHeight": params["height"],
        "ExportVideo": True,
        "ExportAudio": True,
        "VideoQuality": params.get("quality", "Medium"),
    }
    if params.get("mark_in") is not None and params.get("mark_out") is not None:
        offset = int(timeline.GetStartFrame())
        settings.update({"SelectAllFrames": False, "MarkIn": offset + params["mark_in"], "MarkOut": offset + params["mark_out"]})
    else:
        settings["SelectAllFrames"] = True
    if not project.SetRenderSettings(settings):
        raise RuntimeError("Resolve rejected the render settings")
    job_id = project.AddRenderJob()
    if not job_id:
        raise RuntimeError("Resolve did not add a render job")
    jobs = project.GetRenderJobList() or []
    job = next((j for j in jobs if j.get("JobId") == job_id), None)
    if not job or _norm_path(job.get("TargetDir", "")) != _norm_path(str(target)):
        raise RuntimeError("render job settings did not verify")
    if not project.StartRendering([job_id], False):
        raise RuntimeError("Resolve did not start rendering")
    return {"verified": True, "job_id": job_id, "job": _json_safe(job)}


def _render_status(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    status = project.GetRenderJobStatus(params["job_id"]) or {}
    job = next((j for j in project.GetRenderJobList() or [] if j.get("JobId") == params["job_id"]), {})
    return {"status": _json_safe(status), "job": _json_safe(job), "rendering": bool(project.IsRenderingInProgress())}


def _render_stop(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    project.StopRendering()
    return {"verified": not bool(project.IsRenderingInProgress())}


def _timecode(frame: int, fps: float) -> str:
    rate = int(round(fps))
    h, rem = divmod(frame, rate * 3600)
    m, rem = divmod(rem, rate * 60)
    s, f = divmod(rem, rate)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _export_still(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    project.SetCurrentTimeline(timeline)
    resolve.OpenPage("color")
    fps = float((timeline.GetSettings() or {}).get("timelineFrameRate") or 30)
    absolute = int(timeline.GetStartFrame()) + params["frame"]
    if not timeline.SetCurrentTimecode(_timecode(absolute, fps)):
        raise RuntimeError("could not position the playhead for the still")
    target = Path(params["export_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    ok = bool(project.ExportCurrentFrameAsStill(str(target)))
    return {"verified": ok and target.is_file() and target.stat().st_size > 0, "path": str(target)}


def _duplicate_timeline(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    copy = timeline.DuplicateTimeline(params["new_name"])
    if not copy or str(copy.GetName()) != params["new_name"]:
        raise RuntimeError("timeline duplicate did not verify")
    return {"verified": True, "timeline_id": str(copy.GetUniqueId()), "name": params["new_name"]}


def _save_project(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    manager, project = _project(resolve)
    ok = bool(project.SaveProject()) if hasattr(project, "SaveProject") else bool(manager.SaveProject())
    return {"verified": ok}


def _get_transcription(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    clip = _media_by_source_path(project, params["source_path"])
    if not hasattr(clip, "GetTranscription"):
        return {"available": False}
    return {"available": True, "transcription": _json_safe(clip.GetTranscription() or {})}


def _create_project(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    manager = resolve.GetProjectManager()
    current = manager.GetCurrentProject()
    previous = current.GetName() if current else None
    if current:
        manager.SaveProject()
    project = manager.CreateProject(params["name"])
    if not project or project.GetName() != params["name"]:
        raise RuntimeError("disposable gate project was not created")
    return {"verified": True, "previous_project": previous, "project_name": params["name"]}


def _load_project(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    manager = resolve.GetProjectManager()
    project = manager.LoadProject(params["name"])
    return {"verified": bool(project) and project.GetName() == params["name"]}


def _delete_marker(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    timeline.DeleteMarkerByCustomData(params["custom_data"])
    remaining = [m for m in (timeline.GetMarkers() or {}).values() if isinstance(m, dict) and m.get("customData") == params["custom_data"]]
    return {"verified": not remaining}


def _delete_fusion_comp(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    item.DeleteFusionCompByName(params["comp_name"])
    return {"verified": params["comp_name"] not in list(item.GetFusionCompNameList() or [])}


def _reset_grade(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    graph = item.GetNodeGraph()
    ok = bool(graph.ResetAllGrades())
    luts = [graph.GetLUT(i) for i in range(1, int(graph.GetNumNodes() or 0) + 1)]
    return {"verified": ok and not any(luts), "luts": luts}


def _clear_clip_color(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, _, item = _item(resolve, params)
    item.ClearClipColor()
    return {"verified": not item.GetClipColor()}


def _import_timeline(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    if any(name == params["name"] for _, name, _ in _timeline_entries(project)):
        raise RuntimeError("refusing to import over an existing timeline name")
    timeline = project.GetMediaPool().ImportTimelineFromFile(params["path"], {"timelineName": params["name"], "importSourceClips": True})
    if not timeline or str(timeline.GetName()) != params["name"]:
        raise RuntimeError("timeline import did not verify")
    return {"verified": True, "timeline_id": str(timeline.GetUniqueId()), "items": [_item_state(timeline, t, i, it) for t, i, it in _all_items(timeline)]}


def _set_track_lock(resolve: Any, params: dict[str, Any]) -> dict[str, Any]:
    _, project = _project(resolve)
    timeline = _timeline_by_id(project, params["timeline_id"])
    if not timeline.GetName().startswith("VANTA_"):
        raise RuntimeError("track locks may only be changed on VANTA-owned timelines")
    timeline.SetTrackLock(params["track_type"], params["track_index"], params["locked"])
    return {"verified": bool(timeline.GetIsTrackLocked(params["track_type"], params["track_index"])) == params["locked"]}


OPERATIONS = {
    "import_timeline": _import_timeline,
    "set_track_lock": _set_track_lock,
    "delete_marker": _delete_marker,
    "delete_fusion_comp": _delete_fusion_comp,
    "reset_grade": _reset_grade,
    "clear_clip_color": _clear_clip_color,
    "status": lambda r, p: _status(r),
    "read_state": _read_state,
    "timeline_snapshot": _timeline_snapshot,
    "render_status": _render_status,
    "get_transcription": _get_transcription,
    "snapshot": _snapshot,
    "create_timeline": _create_timeline,
    "append_clip": _append_clip,
    "append_clips": _append_clips,
    "add_marker": _add_marker,
    "rollback_vanta_timelines": _rollback_vanta_timelines,
    "import_media": _import_media,
    "ensure_tracks": _ensure_tracks,
    "open_page": _open_page,
    "set_current_timeline": _set_current_timeline,
    "set_clip_color": _set_clip_color,
    "set_lut": _set_lut,
    "set_cdl": _set_cdl,
    "apply_drx": _apply_drx,
    "set_node_enabled": _set_node_enabled,
    "export_lut": _export_lut,
    "import_fusion_comp": _import_fusion_comp,
    "capture_drx_template": _capture_drx_template,
    "create_magic_mask": _create_magic_mask,
    "add_transition": _add_transition,
    "set_speed": _set_speed,
    "set_fades": _set_fades,
    "set_properties": _set_properties,
    "add_clip_mattes": _add_clip_mattes,
    "render_start": _render_start,
    "render_stop": _render_stop,
    "export_still": _export_still,
    "duplicate_timeline": _duplicate_timeline,
    "save_project": _save_project,
    "create_project": _create_project,
    "load_project": _load_project,
}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    writes = os.environ.get("VANTA_RESOLVE_WRITES") == "1" or os.environ.get("VANTA_RESOLVE_GATE_MUTATIONS") == "1"
    try:
        action, params = validate_request(request, allow_mutations=writes, output_root=os.environ.get("VANTA_OUTPUT_ROOT") or None)
    except ProtocolError as exc:
        return {"ok": False, "error": str(exc), "protocol_error": True}
    resolve, error = _connect()
    if error:
        return {"ok": True, "result": {"connected": False, "reason": error}}
    try:
        return {"ok": True, "result": _json_safe(OPERATIONS[action](resolve, params))}
    except Exception:
        # one reconnect attempt for a Resolve restart between calls (read-only actions only)
        if action in {"status", "read_state", "timeline_snapshot", "render_status"}:
            resolve, error = _connect(force=True)
            if not error:
                try:
                    return {"ok": True, "result": _json_safe(OPERATIONS[action](resolve, params))}
                except Exception as retry_exc:
                    exc = retry_exc
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=4)}


def main() -> int:
    for line in sys.stdin:
        request: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise TypeError("request must be a JSON object")
            response = handle(request)
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        response["id"] = request.get("id") if isinstance(request, dict) else None  # type: ignore[union-attr]
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
