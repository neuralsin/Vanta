from __future__ import annotations

import pytest

from vanta.resolve.protocol import ProtocolError, validate_request


def test_mutating_actions_are_disabled_by_default() -> None:
    with pytest.raises(ProtocolError, match="mutation actions are disabled"):
        validate_request(
            {
                "action": "create_timeline",
                "params": {"name": "VANTA_test_ASSEMBLY_V1", "snapshot_id": "snap"},
            },
            allow_mutations=False,
        )


def test_protocol_rejects_code_and_unexpected_parameters_before_connection() -> None:
    with pytest.raises(ProtocolError, match="broker allowlist"):
        validate_request(
            {"action": "run_script", "params": {"code": "print(1)"}}, allow_mutations=True
        )
    with pytest.raises(ProtocolError, match="unexpected"):
        validate_request(
            {"action": "status", "params": {"script": "print(1)"}}, allow_mutations=True
        )


def test_append_contract_requires_complete_non_overlapping_frame_bounds() -> None:
    action, params = validate_request(
        {
            "action": "append_clip",
            "params": {
                "timeline_id": "timeline-1",
                "source_path": "D:/media/clip.mov",
                "source_in_frame": 0,
                "source_out_frame": 10,
                "timeline_start_frame": 0,
                "track_index": 1,
            },
        },
        allow_mutations=True,
    )
    assert action == "append_clip"
    assert params["source_out_frame"] == 10
    with pytest.raises(ProtocolError, match="greater"):
        validate_request(
            {
                "action": "append_clip",
                "params": {
                    "timeline_id": "timeline-1",
                    "source_path": "D:/media/clip.mov",
                    "source_in_frame": 10,
                    "source_out_frame": 10,
                    "timeline_start_frame": 0,
                    "track_index": 1,
                },
            },
            allow_mutations=True,
        )


def test_rollback_requires_exact_nonempty_unique_timeline_identity() -> None:
    with pytest.raises(ProtocolError, match="duplicates"):
        validate_request(
            {
                "action": "rollback_vanta_timelines",
                "params": {
                    "snapshot_id": "snap",
                    "baseline_current_timeline_id": "baseline",
                    "created_timeline_ids": ["vanta-1", "vanta-1"],
                },
            },
            allow_mutations=True,
        )
