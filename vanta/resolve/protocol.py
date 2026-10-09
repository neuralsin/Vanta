"""Closed, declarative request protocol for the Resolve embedded-Python broker.

Data only: a planner never sends source text, Fusion graphs, method names or
free-form dictionaries across this boundary. Every action has a fixed
parameter schema; unknown actions/parameters are rejected before Resolve is
contacted. This module is imported by Resolve's own Python, so it must stay
dependency-free.
"""

from __future__ import annotations

import ntpath
import os
from typing import Any

READ_ACTIONS = frozenset({"status", "read_state", "timeline_snapshot", "render_status", "get_transcription"})

PAGES = ("media", "cut", "edit", "fusion", "color", "fairlight", "deliver")
CLIP_COLORS = ("Orange", "Apricot", "Yellow", "Lime", "Olive", "Green", "Teal", "Navy", "Blue", "Purple", "Violet", "Pink", "Tan", "Beige", "Brown", "Chocolate")
MARKER_COLORS = ("Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose", "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream")
TRANSITION_TYPES = ("Cross Dissolve", "Dip To Color Dissolve", "Additive Dissolve", "Blur Dissolve", "Smooth Cut")
PROPERTY_KEYS = ("Pan", "Tilt", "ZoomX", "ZoomY", "ZoomGang", "RotationAngle", "AnchorPointX", "AnchorPointY", "Opacity", "FlipX", "FlipY", "CropLeft", "CropRight", "CropTop", "CropBottom", "AudioVolume", "AudioVolumeEnabled", "DynamicZoomEnabled")
READ_SCOPES = ("project", "timeline", "timeline_item", "marker", "timeline_items", "markers", "media_pool", "item", "grade", "page", "render_jobs")


class ProtocolError(ValueError):
    """Raised before the embedded Resolve process is contacted."""


# field type specs: (kind, required, extra)
def S(required: bool = True, **kw: Any) -> dict:
    return {"kind": "str", "required": required, **kw}


def I(required: bool = True, minimum: int = 0, maximum: int | None = None) -> dict:  # noqa: E743
    return {"kind": "int", "required": required, "min": minimum, "max": maximum}


def F(required: bool = True, minimum: float | None = None, maximum: float | None = None) -> dict:
    return {"kind": "float", "required": required, "min": minimum, "max": maximum}


def B(required: bool = True) -> dict:
    return {"kind": "bool", "required": required}


def E(choices: tuple, required: bool = True) -> dict:
    return {"kind": "enum", "required": required, "choices": choices}


def P(required: bool = True, *, exists: bool = False, output: bool = False, suffix: tuple[str, ...] | None = None) -> dict:
    return {"kind": "path", "required": required, "exists": exists, "output": output, "suffix": suffix}


def L(item: dict, required: bool = True, min_items: int = 1, max_items: int = 500, unique: bool = False) -> dict:
    return {"kind": "list", "required": required, "item": item, "min": min_items, "max": max_items, "unique": unique}


def Obj(fields: dict) -> dict:
    return {"kind": "object", "required": True, "fields": fields}


ITEM = {"timeline_id": S(), "item_id": S()}
CLIP = Obj(
    {
        "source_path": P(exists=False),
        "source_in_frame": I(),
        "source_out_frame": I(minimum=1),
        "timeline_start_frame": I(),
        "track_index": I(minimum=1),
        "media_type": I(False, 1, 2),
    }
)

SCHEMAS: dict[str, dict[str, dict]] = {
    "status": {},
    "read_state": {
        "scope": E(READ_SCOPES),
        "timeline_id": S(False),
        "source_path": S(False),
        "source_in_frame": I(False),
        "timeline_start_frame": I(False),
        "track_index": I(False, 1),
        "custom_data": S(False),
        "item_id": S(False),
        "node_index": I(False, 1),
        "track_type": E(("video", "audio", "subtitle"), False),
    },
    "timeline_snapshot": {"timeline_id": S()},
    "render_status": {"job_id": S()},
    "get_transcription": {"source_path": P(exists=True)},
    # ----------------------------------------------------------- mutations
    "snapshot": {"label": S(), "export_path": P(output=True, suffix=(".drp",))},
    "create_timeline": {"name": S(prefix="VANTA_"), "snapshot_id": S(), "fps": S(False), "width": I(False, 16, 16384), "height": I(False, 16, 16384)},
    "append_clip": {
        "timeline_id": S(),
        "source_path": S(),
        "source_in_frame": I(),
        "source_out_frame": I(minimum=1),
        "timeline_start_frame": I(),
        "track_index": I(minimum=1),
        "media_type": I(False, 1, 2),
    },
    "append_clips": {"timeline_id": S(), "clips": L(CLIP)},
    "add_marker": {"timeline_id": S(), "frame": I(), "color": E(MARKER_COLORS), "name": S(), "note": S(), "custom_data": S()},
    "rollback_vanta_timelines": {"snapshot_id": S(), "created_timeline_ids": L(S(), unique=True), "baseline_current_timeline_id": S()},
    "import_media": {"paths": L(P(exists=True), max_items=200), "folder_name": S(prefix="VANTA_")},
    "ensure_tracks": {"timeline_id": S(), "video_tracks": I(minimum=1, maximum=20), "audio_tracks": I(minimum=1, maximum=20)},
    "open_page": {"page": E(PAGES)},
    "set_current_timeline": {"timeline_id": S()},
    "set_clip_color": {**ITEM, "color": E(CLIP_COLORS)},
    "set_lut": {**ITEM, "node_index": I(minimum=1, maximum=64), "lut_path": P(exists=True, suffix=(".cube",))},
    "set_cdl": {**ITEM, "node_index": I(minimum=1, maximum=64), "slope": S(), "offset": S(), "power": S(), "saturation": F(minimum=0, maximum=4)},
    "apply_drx": {**ITEM, "drx_path": P(exists=True, suffix=(".drx",)), "grade_mode": I(minimum=0, maximum=2)},
    "set_node_enabled": {**ITEM, "node_index": I(minimum=1, maximum=64), "enabled": B()},
    "capture_drx_template": {"export_dir": P(output=True), "prefix": S(prefix="VANTA_")},
    "create_magic_mask": {**ITEM, "node_index": I(minimum=1, maximum=64), "mode": E(("F", "B", "BI"))},
    "export_lut": {**ITEM, "export_path": P(output=True, suffix=(".cube",)), "size": E((17, 33, 65))},
    "import_fusion_comp": {**ITEM, "comp_path": P(exists=True, suffix=(".comp", ".setting")), "comp_name": S(required=False, prefix="VANTA_")},
    "add_transition": {**ITEM, "type": E(TRANSITION_TYPES), "position": E(("start", "end")), "alignment": E(("left", "center", "right")), "duration": I(minimum=1, maximum=240)},
    "set_speed": {**ITEM, "percentage": F(minimum=1.0, maximum=1000.0)},
    "set_fades": {**ITEM, "fade_in": I(), "fade_out": I()},
    "set_properties": {**ITEM, "properties": {"kind": "props", "required": True}},
    "add_clip_mattes": {"source_path": S(), "matte_paths": L(P(exists=True), max_items=20000)},
    "render_start": {
        "timeline_id": S(),
        "target_dir": P(output=True),
        "custom_name": S(),
        "width": I(minimum=16, maximum=8192),
        "height": I(minimum=16, maximum=8192),
        "format": S(False),
        "codec": S(False),
        "quality": S(False),
        "mark_in": I(False),
        "mark_out": I(False),
        "preset": S(False),
    },
    "render_stop": {},
    "export_still": {"timeline_id": S(), "frame": I(), "export_path": P(output=True, suffix=(".png", ".jpg", ".tif", ".dpx"))},
    "duplicate_timeline": {"timeline_id": S(), "new_name": S(prefix="VANTA_")},
    "save_project": {},
    "delete_marker": {"timeline_id": S(), "custom_data": S(prefix="vanta:")},
    "delete_fusion_comp": {**ITEM, "comp_name": S()},
    "reset_grade": {**ITEM},
    "clear_clip_color": {**ITEM},
    "import_timeline": {"path": P(exists=True, suffix=(".fcpxml", ".xml", ".otio", ".edl")), "name": S(prefix="VANTA_")},
    "set_track_lock": {"timeline_id": S(), "track_type": E(("video", "audio")), "track_index": I(minimum=1), "locked": B()},
    "create_project": {"name": S(prefix="VANTA_GATE_")},
    "load_project": {"name": S()},
}
MUTATION_ACTIONS = frozenset(set(SCHEMAS) - READ_ACTIONS)
ALL_ACTIONS = frozenset(SCHEMAS)


def _is_local_absolute(value: str) -> bool:
    if value.startswith(("\\\\", "//")):
        return False  # no network shares: scripting stays local
    return ntpath.isabs(value) or value.startswith("/")


def _norm(value: str) -> str:
    return os.path.normcase(os.path.abspath(value))


def _check(name: str, spec: dict, value: Any, *, output_root: str | None) -> Any:
    kind = spec["kind"]
    if kind == "str":
        if not isinstance(value, str) or not value.strip():
            raise ProtocolError(f"{name} must be a nonempty string")
        if len(value) > 4000:
            raise ProtocolError(f"{name} is too long")
        prefix = spec.get("prefix")
        if prefix and not value.startswith(prefix):
            raise ProtocolError(f"{name} must start with {prefix} (VANTA-owned objects only)")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or value < spec["min"] or (spec.get("max") is not None and value > spec["max"]):
            limit = f">= {spec['min']}" + (f" and <= {spec['max']}" if spec.get("max") is not None else "")
            raise ProtocolError(f"{name} must be an integer {limit}")
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProtocolError(f"{name} must be a number")
        if spec.get("min") is not None and value < spec["min"] or spec.get("max") is not None and value > spec["max"]:
            raise ProtocolError(f"{name} outside allowed range")
        return float(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ProtocolError(f"{name} must be a boolean")
        return value
    if kind == "enum":
        if value not in spec["choices"]:
            raise ProtocolError(f"{name} must be one of {list(spec['choices'])}")
        return value
    if kind == "path":
        if not isinstance(value, str) or not _is_local_absolute(value):
            raise ProtocolError(f"{name} must be an absolute local path")
        if spec.get("suffix") and not value.casefold().endswith(tuple(s.casefold() for s in spec["suffix"])):
            raise ProtocolError(f"{name} must end with {spec['suffix']}")
        if spec.get("exists") and not os.path.exists(value):
            raise ProtocolError(f"{name} does not exist: {value}")
        if spec.get("output"):
            if output_root and not _norm(value).startswith(_norm(output_root)):
                raise ProtocolError(f"{name} must be inside the VANTA output root (never over source media)")
        return value
    if kind == "list":
        if not isinstance(value, list) or not spec["min"] <= len(value) <= spec["max"]:
            raise ProtocolError(f"{name} must be a list of {spec['min']}..{spec['max']} items")
        out = [_check(f"{name}[{i}]", spec["item"], item, output_root=output_root) for i, item in enumerate(value)]
        if spec.get("unique") and len({repr(v) for v in out}) != len(out):
            raise ProtocolError(f"{name} must not contain duplicates")
        return out
    if kind == "object":
        if not isinstance(value, dict):
            raise ProtocolError(f"{name} must be an object")
        return _validate_fields(value, spec["fields"], output_root=output_root, where=name)
    if kind == "props":
        if not isinstance(value, dict) or not value:
            raise ProtocolError(f"{name} must be a nonempty object")
        unknown = set(value) - set(PROPERTY_KEYS)
        if unknown:
            raise ProtocolError(f"unexpected property key(s): {sorted(unknown)}")
        for key, item in value.items():
            if isinstance(item, (bool, int, float)):
                continue
            raise ProtocolError(f"property {key} must be numeric or boolean")
        return value
    raise ProtocolError(f"unknown schema kind {kind}")


def _validate_fields(params: dict, fields: dict, *, output_root: str | None, where: str = "params") -> dict:
    extra = set(params) - set(fields)
    if extra:
        raise ProtocolError(f"unexpected parameter(s) in {where}: " + ", ".join(sorted(extra)))
    missing = {k for k, spec in fields.items() if spec.get("required", True) and k not in params}
    if missing:
        raise ProtocolError("missing parameter(s): " + ", ".join(sorted(missing)))
    out = {}
    for key, value in params.items():
        out[key] = _check(key, fields[key], value, output_root=output_root)
    for clip_like in ("source_in_frame",):
        if clip_like in out and "source_out_frame" in out and out["source_out_frame"] <= out["source_in_frame"]:
            raise ProtocolError("source_out_frame must be greater than source_in_frame")
    return out


def validate_request(request: dict[str, Any], *, allow_mutations: bool, output_root: str | None = None) -> tuple[str, dict[str, Any]]:
    """Return a validated fixed action and params or reject it before connection."""
    action = request.get("action")
    if action not in ALL_ACTIONS:
        raise ProtocolError(f"action is not in the VANTA broker allowlist: {action!r}")
    if action in MUTATION_ACTIONS and not allow_mutations:
        raise ProtocolError("mutation actions are disabled for this broker session (no write permission was granted)")
    params = request.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolError("params must be an object")
    clean = _validate_fields(params, SCHEMAS[action], output_root=output_root)
    if action == "read_state":
        scope = clean["scope"]
        needs_timeline = {"timeline", "timeline_item", "marker", "timeline_items", "markers", "item", "grade"}
        if scope in needs_timeline and "timeline_id" not in clean:
            raise ProtocolError("timeline_id must be a nonempty string")
        if scope == "timeline_item":
            for key in ("source_path", "source_in_frame", "timeline_start_frame", "track_index"):
                if key not in clean:
                    raise ProtocolError(f"missing parameter(s): {key}")
        if scope == "marker" and "custom_data" not in clean:
            raise ProtocolError("custom_data must be a nonempty string")
        if scope in {"item", "grade"} and "item_id" not in clean:
            raise ProtocolError("item_id must be a nonempty string")
    if action == "set_fades" and (clean["fade_in"] > 10000 or clean["fade_out"] > 10000):
        raise ProtocolError("fade duration is implausible")
    return action, clean
