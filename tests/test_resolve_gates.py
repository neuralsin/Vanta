from __future__ import annotations

from pathlib import Path

from vanta.resolve.adapter import ResolveAdapter, ResolveUnavailable
from vanta.resolve.gates import G1Fixture, run_g1


class G1FakeAdapter(ResolveAdapter):
    def __init__(self, project_name: str, baseline_name: str):
        self.project_name = project_name
        self.baseline_name = baseline_name
        self.timelines = {"baseline": baseline_name}
        self.items: dict[tuple[str, str, int], dict] = {}
        self.markers: dict[tuple[str, str], dict] = {}
        self.created: list[str] = []

    def capabilities(self):
        return {"connected": True, "verified": False}

    def read_state(self, target):
        scope = target["scope"]
        if scope == "project":
            return {
                "project_name": self.project_name,
                "timelines": [
                    {"timeline_id": timeline_id, "name": name}
                    for timeline_id, name in self.timelines.items()
                ],
            }
        if scope == "timeline":
            return {
                "timeline_id": target["timeline_id"],
                "name": self.timelines[target["timeline_id"]],
            }
        if scope == "timeline_item":
            return self.items[
                (target["timeline_id"], target["source_path"], target["timeline_start_frame"])
            ]
        if scope == "marker":
            return self.markers[(target["timeline_id"], target["custom_data"])]
        raise AssertionError(scope)

    def open_page(self, page):
        return {"verified": True}

    def snapshot(self, label, *, export_path):
        return {"snapshot_id": label, "verified": True, "snapshot_path": export_path}

    def create_timeline(self, name, *, snapshot_id):
        if name in self.timelines.values():
            raise ResolveUnavailable("timeline name already exists")
        timeline_id = "g1-created"
        self.timelines[timeline_id] = name
        self.created.append(timeline_id)
        return {"timeline_id": timeline_id, "name": name, "verified": True}

    def append_clip(self, timeline_id, source_path, source_in, source_out, timeline_start, track):
        self.items[(timeline_id, source_path, timeline_start)] = {
            "source_path": str(Path(source_path).resolve()),
            "source_in_frame": source_in,
            "source_out_frame": source_out,
            "timeline_start_frame": timeline_start,
            "track_index": track,
        }
        return {"accepted": True}

    def add_marker(self, timeline_id, frame, color, name, note, custom_data):
        self.markers[(timeline_id, custom_data)] = {
            "frame": frame,
            "color": color,
            "name": name,
            "note": note,
            "custom_data": custom_data,
        }
        return {"accepted": True}

    def render_proxy(self, timeline_id, output_path):
        raise AssertionError("not part of G1")

    def rollback(self, snapshot_id):
        for timeline_id in self.created:
            self.timelines.pop(timeline_id, None)
        return {"restored_snapshot_id": snapshot_id, "verified": True}


def test_g1_runner_requires_five_clips_verifies_each_and_rolls_back(tmp_path) -> None:
    project_name = "VANTA_GATE_g1"
    baseline_name = "BASELINE"
    sources = []
    for index in range(5):
        source = tmp_path / f"source-{index}.mov"
        source.write_bytes(b"fixture")
        sources.append(
            {
                "source_path": str(source),
                "source_in_frame": 0,
                "source_out_frame": 10,
                "timeline_start_frame": index * 10,
            }
        )
    fixture = G1Fixture(
        project_name=project_name,
        baseline_timeline_name=baseline_name,
        timeline_name="VANTA_GATE_g1_ASSEMBLY_V1",
        clips=sources,
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    adapter = G1FakeAdapter(project_name, baseline_name)
    result = run_g1(adapter, fixture, artifact_root=artifacts)
    assert result.status == "PASS_CANDIDATE"
    assert len(result.evidence["operations"]) == 5
    assert result.evidence["rollback"]["verified"] is True
    assert adapter.timelines == {"baseline": baseline_name}
