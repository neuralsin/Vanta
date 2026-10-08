from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vanta.core.executor import ExecutionPlan, PlannedAction, PlanRejected, execute_plan
from vanta.core.schemas import (
    AutonomyMode,
    EditDecisionGraph,
    RunMode,
    Shot,
    SourceMedia,
    VantaEvent,
)
from vanta.resolve.adapter import ResolveAdapter, VerificationFailed


class FakeAdapter(ResolveAdapter):
    """Protocol-shaped fake. It proves VANTA's orchestration, never Resolve API behaviour."""

    def __init__(self, *, mismatch: bool = False):
        self.state: dict[str, object] = {}
        self.snapshot_id = "snap-test"
        self.mismatch = mismatch
        self.rollback_calls = 0
        self.created_timeline_ids: list[str] = []

    def capabilities(self):
        return {"connected": True, "verified": True}

    def read_state(self, target):
        if target.get("scope") == "snapshot":
            return {"restored_snapshot_id": self.snapshot_id}
        if self.mismatch:
            return {"name": "unexpected"}
        return dict(self.state)

    def open_page(self, page):
        return {"verified": True, "page": page}

    def create_timeline(self, name, *, snapshot_id):
        assert snapshot_id == self.snapshot_id
        self.created_timeline_ids.append("tl-vanta-1")
        self.state = {"name": name}
        return {"timeline_id": "tl-vanta-1"}

    def append_clip(self, timeline_id, source_path, source_in, source_out, timeline_start, track):
        assert timeline_id == "tl-vanta-1"
        self.state = {
            "source_path": str(Path(source_path).resolve()),
            "source_in_frame": source_in,
            "source_out_frame": source_out,
            "timeline_start_frame": timeline_start,
            "track_index": track,
        }
        return {"accepted": True}

    def add_marker(self, timeline_id, frame, color, name, note, custom_data):
        assert timeline_id == "tl-vanta-1"
        self.state = {
            "frame": frame,
            "color": color,
            "name": name,
            "note": note,
            "custom_data": custom_data,
        }
        return {"accepted": True}

    def render_proxy(self, timeline_id, output_path):
        raise AssertionError("render proxy must not be reachable in the corrected Phase 1 executor")

    def snapshot(self, label, *, export_path):
        assert label.startswith("vanta_pre_")
        assert export_path.endswith(".drp")
        return {"snapshot_id": self.snapshot_id, "verified": True}

    def rollback(self, snapshot_id):
        assert snapshot_id == self.snapshot_id
        self.rollback_calls += 1
        self.state.clear()
        self.created_timeline_ids.clear()
        return {"verified": True}


def capabilities():
    return {
        "resolve": {"installed": True, "version": "21.1.1"},
        "gates": {"G10": {"status": "PASS"}, "G1": {"status": "PASS"}},
    }


def make_plan(
    tmp_path, *, autonomy: AutonomyMode = AutonomyMode.ASSISTED, mismatch_action: str | None = None
):
    media = tmp_path / "source.mov"
    media.write_bytes(b"read-only fixture")
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    name = "VANTA_test_ASSEMBLY_V1"
    graph = EditDecisionGraph(
        project_id="p",
        fps=30,
        timeline_name=name,
        sources=[SourceMedia(id="source", path=str(media), duration_frames=100, fps=30)],
        shots=[
            Shot(
                id="shot-1",
                source_media_id="source",
                source_in_frame=0,
                source_out_frame=30,
                timeline_start_frame=0,
            )
        ],
    )
    source_path = str(media.resolve())
    actions = [
        PlannedAction(
            id="step_create",
            action="create_timeline",
            stage="assembly",
            target={"timeline_ref": "assembly"},
            parameters={"timeline_ref": "assembly", "name": name},
            expected={"name": name},
            confidence=0.95,
            page_hint="edit",
        ),
        PlannedAction(
            id="step_append",
            action="append_clip",
            stage="assembly",
            target={"shot_id": "shot-1"},
            parameters={
                "timeline_ref": "assembly",
                "source_id": "source",
                "source_in_frame": 0,
                "source_out_frame": 30,
                "timeline_start_frame": 0,
                "track_index": 1,
            },
            expected={
                "source_path": source_path,
                "source_in_frame": 0,
                "source_out_frame": 30,
                "timeline_start_frame": 0,
                "track_index": 1,
            },
            confidence=0.95,
            page_hint="edit",
        ),
        PlannedAction(
            id="step_marker",
            action="add_marker",
            stage="assembly",
            target={"timeline_ref": "assembly"},
            parameters={
                "timeline_ref": "assembly",
                "frame": 0,
                "color": "Green",
                "name": "VANTA: shot-1",
                "note": "verified source trim",
            },
            expected={
                "frame": 0,
                "color": "Green",
                "name": "VANTA: shot-1",
                "note": "verified source trim",
                "custom_data": "vanta:run_executor_test:step_marker",
            },
            confidence=0.95,
            page_hint="edit",
        ),
    ]
    if mismatch_action:
        actions[-1].action = mismatch_action
    return ExecutionPlan(
        run_id="run_executor_test",
        project_id="p",
        artifact_root=str(artifact_root),
        autonomy=autonomy,
        run_mode=RunMode.TURBO,
        edg=graph,
        actions=actions,
    )


def test_executor_binds_logical_timeline_source_path_and_marker_identity(tmp_path):
    adapter = FakeAdapter()
    events: list[VantaEvent] = []
    result = asyncio.run(
        execute_plan(
            make_plan(tmp_path),
            adapter,
            capabilities=capabilities(),
            project_root=tmp_path,
            event_sink=events.append,
        )
    )
    assert result["verified_steps"] == 3
    assert [(event.action, event.status.value) for event in events] == [
        ("create_snapshot", "running"),
        ("create_snapshot", "verified"),
        ("create_timeline", "planned"),
        ("create_timeline", "running"),
        ("create_timeline", "verified"),
        ("append_clip", "planned"),
        ("append_clip", "running"),
        ("append_clip", "verified"),
        ("add_marker", "planned"),
        ("add_marker", "running"),
        ("add_marker", "verified"),
    ]
    assert adapter.rollback_calls == 0


def test_executor_rolls_back_only_after_a_write_when_readback_disagrees(tmp_path):
    adapter = FakeAdapter(mismatch=True)
    events: list[VantaEvent] = []
    with pytest.raises(VerificationFailed):
        asyncio.run(
            execute_plan(
                make_plan(tmp_path),
                adapter,
                capabilities=capabilities(),
                project_root=tmp_path,
                event_sink=events.append,
            )
        )
    assert adapter.rollback_calls == 1
    assert any(event.status.value == "rolled_back" for event in events)


def test_executor_rejects_unverified_adapter_before_snapshot_or_write(tmp_path):
    adapter = FakeAdapter()
    adapter.capabilities = lambda: {"connected": False, "verified": False}
    with pytest.raises(Exception, match="not live and gate-verified"):
        asyncio.run(
            execute_plan(
                make_plan(tmp_path),
                adapter,
                capabilities=capabilities(),
                project_root=tmp_path,
                event_sink=lambda _: None,
            )
        )
    assert adapter.rollback_calls == 0


def test_executor_rejects_raw_or_unimplemented_operations(tmp_path):
    plan = make_plan(tmp_path, mismatch_action="render_proxy")
    with pytest.raises(PlanRejected, match="unrecognized or unimplemented"):
        asyncio.run(
            execute_plan(
                plan,
                FakeAdapter(),
                capabilities=capabilities(),
                project_root=tmp_path,
                event_sink=lambda _: None,
            )
        )


def test_auto_requires_approval_below_point_nine(tmp_path):
    plan = make_plan(tmp_path, autonomy=AutonomyMode.AUTO)
    plan.actions[0].confidence = 0.80
    with pytest.raises(PlanRejected, match="Approval gate rejected"):
        asyncio.run(
            execute_plan(
                plan,
                FakeAdapter(),
                capabilities=capabilities(),
                project_root=tmp_path,
                event_sink=lambda _: None,
            )
        )
