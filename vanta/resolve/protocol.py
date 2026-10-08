"""Closed request protocol for the Resolve embedded-Python broker.

The protocol intentionally contains data only. A planner never sends Resolve
source text, Fusion graphs, method names, or arbitrary dictionaries through this
boundary.
"""

from __future__ import annotations

from typing import Any

READ_ACTIONS = frozenset({"status", "read_state"})
MUTATION_ACTIONS = frozenset(
    {"snapshot", "create_timeline", "append_clip", "add_marker", "rollback_vanta_timelines"}
)
ALL_ACTIONS = READ_ACTIONS | MUTATION_ACTIONS


class ProtocolError(ValueError):
    """Raised before the embedded Resolve process is contacted."""


def _exact_keys(params: dict[str, Any], allowed: set[str], required: set[str]) -> None:
    extra = set(params) - allowed
    missing = required - set(params)
    if extra:
        raise ProtocolError("unexpected parameter(s): " + ", ".join(sorted(extra)))
    if missing:
        raise ProtocolError("missing parameter(s): " + ", ".join(sorted(missing)))


def _nonempty_string(params: dict[str, Any], key: str) -> None:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{key} must be a nonempty string")


def _integer(params: dict[str, Any], key: str, *, minimum: int = 0) -> None:
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolError(f"{key} must be an integer >= {minimum}")


def validate_request(
    request: dict[str, Any], *, allow_mutations: bool
) -> tuple[str, dict[str, Any]]:
    """Return a validated fixed action and params or reject it before connection."""
    action = request.get("action")
    if action not in ALL_ACTIONS:
        raise ProtocolError(f"action is not in the VANTA broker allowlist: {action!r}")
    if action in MUTATION_ACTIONS and not allow_mutations:
        raise ProtocolError(
            "mutation actions are disabled until a disposable live gate run explicitly enables them"
        )
    params = request.get("params", {})
    if not isinstance(params, dict):
        raise ProtocolError("params must be an object")
    if action == "status":
        _exact_keys(params, set(), set())
    elif action == "read_state":
        _exact_keys(
            params,
            {
                "scope",
                "timeline_id",
                "source_path",
                "source_in_frame",
                "timeline_start_frame",
                "track_index",
                "custom_data",
            },
            {"scope"},
        )
        scope = params["scope"]
        if scope not in {"project", "timeline", "timeline_item", "marker"}:
            raise ProtocolError("read_state scope is not supported")
        if scope in {"timeline", "timeline_item", "marker"}:
            _nonempty_string(params, "timeline_id")
        if scope == "timeline_item":
            _nonempty_string(params, "source_path")
            _integer(params, "source_in_frame")
            _integer(params, "timeline_start_frame")
            _integer(params, "track_index", minimum=1)
        if scope == "marker":
            _nonempty_string(params, "custom_data")
    elif action == "snapshot":
        _exact_keys(params, {"label", "export_path"}, {"label", "export_path"})
        _nonempty_string(params, "label")
        _nonempty_string(params, "export_path")
    elif action == "create_timeline":
        _exact_keys(params, {"name", "snapshot_id"}, {"name", "snapshot_id"})
        _nonempty_string(params, "name")
        _nonempty_string(params, "snapshot_id")
    elif action == "append_clip":
        _exact_keys(
            params,
            {
                "timeline_id",
                "source_path",
                "source_in_frame",
                "source_out_frame",
                "timeline_start_frame",
                "track_index",
            },
            {
                "timeline_id",
                "source_path",
                "source_in_frame",
                "source_out_frame",
                "timeline_start_frame",
                "track_index",
            },
        )
        _nonempty_string(params, "timeline_id")
        _nonempty_string(params, "source_path")
        _integer(params, "source_in_frame")
        _integer(params, "source_out_frame", minimum=1)
        _integer(params, "timeline_start_frame")
        _integer(params, "track_index", minimum=1)
        if params["source_out_frame"] <= params["source_in_frame"]:
            raise ProtocolError("source_out_frame must be greater than source_in_frame")
    elif action == "add_marker":
        _exact_keys(
            params,
            {"timeline_id", "frame", "color", "name", "note", "custom_data"},
            {"timeline_id", "frame", "color", "name", "note", "custom_data"},
        )
        _nonempty_string(params, "timeline_id")
        _integer(params, "frame")
        for key in ("color", "name", "note", "custom_data"):
            _nonempty_string(params, key)
    elif action == "rollback_vanta_timelines":
        _exact_keys(
            params,
            {"snapshot_id", "created_timeline_ids", "baseline_current_timeline_id"},
            {"snapshot_id", "created_timeline_ids", "baseline_current_timeline_id"},
        )
        _nonempty_string(params, "snapshot_id")
        _nonempty_string(params, "baseline_current_timeline_id")
        ids = params["created_timeline_ids"]
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(item, str) or not item for item in ids)
        ):
            raise ProtocolError("created_timeline_ids must be a nonempty list of stable IDs")
        if len(ids) != len(set(ids)):
            raise ProtocolError("created_timeline_ids must not contain duplicates")
    return action, params
