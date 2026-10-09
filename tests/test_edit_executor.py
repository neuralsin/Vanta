"""EDG -> Resolve executor against a protocol-faithful fake (orchestration semantics only).

These prove sequencing, verification, gating, fallbacks, rollback and undo.
They are not Resolve integration evidence (that is the live gate runner).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from vanta.analysis import synthetic
from vanta.analysis.media import probe
from vanta.core.schemas import (
    AutonomyMode,
    EditDecisionGraph,
    GradeSpec,
    RunMode,
    Shot,
    SourceMedia,
    TextLayer,
    Transition,
)
from vanta.live.run_control import RunControl
from vanta.resolve.adapter import ResolveAdapter, VerificationFailed
from vanta.resolve.edit_executor import EditExecutor, ExecutionBlocked
from vanta.resolve.ledger import Ledger
from vanta.solvers.grade_math import LookParams, apply_look
from vanta.solvers.lut import write_cube

VERSION = "21.1.1.10"


class FakeResolve(ResolveAdapter):
    def __init__(self, *, mismatch_on: int | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.timelines: dict[str, str] = {"tl_base": "Main"}
        self.items: dict[str, dict] = {}
        self.markers: dict[str, dict] = {}
        self.comps: dict[str, list[str]] = {}
        self.mismatch_on = mismatch_on
        self.appends = 0
        self.rolled_back = False

    def capabilities(self):
        return {"connected": True, "version_string": VERSION, "product": "DaVinci Resolve Studio"}

    def read_state(self, target):
        if target["scope"] == "marker":
            return self.markers.get(target["custom_data"], {"found": False})
        return {}

    def open_page(self, page):
        self.calls.append(("open_page", {"page": page}))
        return {"verified": True, "page": page}

    def snapshot(self, label, *, export_path):
        Path(export_path).write_bytes(b"drp")
        self.calls.append(("snapshot", {}))
        return {"snapshot_id": label, "verified": True, "snapshot_path": export_path}

    def create_timeline(self, name, *, snapshot_id, fps=None, width=None, height=None):
        self.timelines["tl_vanta"] = name
        self.calls.append(("create_timeline", {"name": name, "fps": fps, "width": width, "height": height}))
        return {"timeline_id": "tl_vanta", "name": name, "verified": True, "color_science": getattr(self, "color_science", "davinciYRGB")}

    def append_clip(self, timeline_id, source_path, source_in, source_out, timeline_start, track, media_type=None):
        self.appends += 1
        item_id = f"item_{self.appends}"
        self.items[item_id] = {"start": timeline_start, "in": source_in, "out": source_out, "track": track}
        self.calls.append(("append_clip", {"path": source_path, "in": source_in, "out": source_out, "start": timeline_start, "track": track, "media_type": media_type}))
        verified = self.mismatch_on != self.appends
        return {"item_id": item_id, "verified": verified, "timeline_start_frame": timeline_start, "source_in_frame": source_in, "source_out_frame": source_out if verified else source_out - 3, "end_convention": "inclusive"}

    def add_marker(self, timeline_id, frame, color, name, note, custom_data):
        self.markers[custom_data] = {"frame": frame, "color": color, "name": name, "note": note, "custom_data": custom_data}
        return {"accepted": True}

    def render_proxy(self, timeline_id, output_path):
        raise AssertionError("not used")

    def rollback(self, snapshot_id):
        self.rolled_back = True
        self.calls.append(("rollback", {}))
        return {"verified": True, "restored_snapshot_id": snapshot_id}

    def call(self, action, **params):
        self.calls.append((action, params))
        if action == "import_media":
            return {"verified": True, "imported": len(params["paths"]), "already_present": 0}
        if action == "ensure_tracks":
            return {"verified": True}
        if action == "set_lut":
            self.items[params["item_id"]]["lut"] = params["lut_path"]
            return {"verified": True, "observed_lut": params["lut_path"]}
        if action == "export_lut":
            shutil.copy(self.items[params["item_id"]]["lut"], params["export_path"])
            return {"verified": True, "path": params["export_path"]}
        if action == "import_fusion_comp":
            comps = self.comps.setdefault(params["item_id"], [])
            comps.append(params.get("comp_name") or f"Composition {len(comps) + 1}")
            return {"verified": True, "comp_name": comps[-1], "comp_count": len(comps)}
        if action == "add_transition":
            return {"verified": True, "duration": params["duration"]}
        if action == "apply_drx":
            return {"verified": True, "nodes": 8, "labels": list(getattr(self, "drx_labels", ["TECH", "BALANCE", "TONE", "LOOK", "SEP", "BG", "SKIN", "POLISH"]))}
        if action in {"set_cdl", "set_node_enabled"}:
            return {"accepted": True, "verified": False}
        if action == "create_magic_mask":
            ok = getattr(self, "mask_ok", True)
            return {"accepted": True, "verified": ok, "nodes_with_mask": [7] if ok else [1]}
        if action == "delete_fusion_comp":
            comps = self.comps.get(params["item_id"], [])
            if params["comp_name"] in comps:
                comps.remove(params["comp_name"])
            return {"verified": True}
        if action in {"set_clip_color", "save_project", "set_properties", "set_speed", "set_fades", "delete_marker", "reset_grade", "clear_clip_color"}:
            return {"verified": True}
        raise AssertionError(f"unexpected action {action}")


@pytest.fixture(scope="module")
def edg(tmp_path_factory) -> EditDecisionGraph:
    root = tmp_path_factory.mktemp("exec")
    clips = [synthetic.make_test_clip(root / f"c{i}.mp4", duration_s=2.0, size=(180, 320), pattern=p) for i, p in enumerate(["testsrc2", "smptebars", "mandelbrot"])]
    frames = probe(clips[0]).frame_count
    look = LookParams(contrast=1.1, saturation=0.9)
    lut = write_cube(root / "look.cube", lambda x: apply_look(x, look), size=17)
    shots = [Shot(id=f"shot_{i:03d}", source_media_id=f"src{i}", source_in_frame=5, source_out_frame=35, timeline_start_frame=30 * i, confidence=0.93, role="hook" if i == 0 else "build") for i in range(3)]
    return EditDecisionGraph(
        project_id="prj_test",
        fps=30,
        width=180,
        height=320,
        timeline_name="VANTA_Test_ASSEMBLY_V1",
        sources=[SourceMedia(id=f"src{i}", path=str(c), fps=30, duration_frames=frames) for i, c in enumerate(clips)],
        shots=shots,
        grades=[GradeSpec(shot_id=s.id, lut_path=str(lut), confidence=0.92, params=look.to_dict()) for s in shots],
        texts=[TextLayer(text="NIGHT RIDE", template="text_basic", start_frame=3, end_frame=40, x=0.5, y=0.4, shot_id="shot_000", confidence=0.9, purpose="hook title")],
        transitions=[Transition(from_shot="shot_001", to_shot="shot_002", kind="cross_dissolve", duration_frames=8, purpose="soften", confidence=0.9)],
    )


def _ledger(tmp_path: Path, passed: set[str]) -> Ledger:
    ledger = Ledger(path=tmp_path / "ledger.json")
    for gate in passed:
        ledger.record(gate, "PASS", {"summary": "test"}, version=VERSION)
    return ledger


def _executor(tmp_path, fake, ledger, events, *, autonomy=AutonomyMode.ASSISTED, approval=None, plan_approved=True):
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    return EditExecutor(fake, ledger, run_id="run_test", run_dir=run_dir, emit=events.append, control=RunControl(RunMode.TURBO, 0), autonomy=autonomy, approval=approval, plan_approved=plan_approved)


def test_full_execution_verifies_every_step_and_grades_by_export(tmp_path, edg):
    fake, events = FakeResolve(), []
    executor = _executor(tmp_path, fake, _ledger(tmp_path, {"G1", "G3", "G4", "G12"}), events)
    report = asyncio.run(executor.execute(edg, include_render=False))
    assert report.timeline_id == "tl_vanta" and report.failed == 0
    assert set(report.item_ids) == {"shot_000", "shot_001", "shot_002"}
    assert all(check["passed"] and check["mean_de"] < 1.0 for check in report.grade_checks.values())
    names = [c[0] for c in fake.calls]
    assert names.index("snapshot") < names.index("create_timeline") < names.index("append_clip")
    assert "add_transition" in names and "import_fusion_comp" in names
    created = next(c for c in fake.calls if c[0] == "create_timeline")[1]
    assert created == {"name": "VANTA_Test_ASSEMBLY_V1", "fps": "30", "width": 180, "height": 320}
    assert any(e.status.value == "verified" and e.action == "grade" for e in events)
    assert any(m["color"] == "Green" for m in fake.markers.values()), "breadcrumbs written"
    assert (tmp_path / "run" / "execution_report.json").is_file()


def test_ungated_operations_route_to_fallbacks(tmp_path, edg):
    fake, events = FakeResolve(), []
    executor = _executor(tmp_path, fake, _ledger(tmp_path, {"G1"}), events)
    report = asyncio.run(executor.execute(edg, include_render=False))
    names = [c[0] for c in fake.calls]
    assert "add_transition" not in names and "set_lut" not in names and "import_fusion_comp" not in names
    assert any("hard cut" in f for f in report.fallbacks)
    assert any("overlay" in f for f in report.fallbacks)
    overlay = next(c for c in fake.calls if c[0] == "append_clip" and c[1]["track"] == 2)
    assert overlay[1]["path"].endswith("overlay.mov") and Path(overlay[1]["path"]).is_file()


def test_read_back_mismatch_rolls_back_the_vanta_timeline(tmp_path, edg):
    fake, events = FakeResolve(mismatch_on=2), []
    executor = _executor(tmp_path, fake, _ledger(tmp_path, {"G1"}), events)
    with pytest.raises(VerificationFailed):
        asyncio.run(executor.execute(edg, include_render=False))
    assert fake.rolled_back
    assert any(e.status.value == "rolled_back" for e in events)


def test_structure_gate_is_required_before_any_write(tmp_path, edg):
    fake, events = FakeResolve(), []
    executor = _executor(tmp_path, fake, _ledger(tmp_path, set()), events)
    with pytest.raises(ExecutionBlocked):
        asyncio.run(executor.execute(edg, include_render=False))
    assert not fake.calls


def test_version_drift_invalidates_gates(tmp_path, edg):
    ledger = _ledger(tmp_path, {"G1"})
    ledger.resolve_version = "20.3.2"
    fake, events = FakeResolve(), []
    with pytest.raises(ExecutionBlocked):
        asyncio.run(_executor(tmp_path, fake, ledger, events).execute(edg, include_render=False))


def test_low_confidence_steps_need_approval_and_undo_reverts(tmp_path, edg):
    low = edg.model_copy(deep=True)
    low.shots[1].confidence = 0.6
    asked = []

    async def deny(step):
        asked.append(step.id)
        return False

    fake, events = FakeResolve(), []
    executor = _executor(tmp_path, fake, _ledger(tmp_path, {"G1", "G4"}), events, approval=deny)
    report = asyncio.run(executor.execute(low, include_render=False))
    assert asked and report.skipped >= 1
    assert "shot_001" not in report.item_ids
    before = len(report.undo_stack)
    entry = executor.undo_last()
    assert len(report.undo_stack) == before - 1 and entry["step"]


def test_layers_on_one_clip_are_chained_into_a_single_active_comp(tmp_path, edg):
    from vanta.core.schemas import EffectLayer

    stacked = edg.model_copy(deep=True)
    stacked.effects = [EffectLayer(template="cinematic_bloom", shot_id="shot_000", start_frame=0, end_frame=30, purpose="night practicals", confidence=0.9)]
    fake, events = FakeResolve(), []
    executor = _executor(tmp_path, fake, _ledger(tmp_path, {"G1", "G3", "G4", "G6", "G12"}), events)
    report = asyncio.run(executor.execute(stacked, include_render=False))
    item = report.item_ids["shot_000"]
    assert len(fake.comps[item]) == 1, "only one VANTA comp stays on the clip"
    imports = [c[1]["comp_path"] for c in fake.calls if c[0] == "import_fusion_comp" and c[1]["item_id"] == item]
    final = Path(imports[-1]).read_text(encoding="utf-8")
    assert "V0_BloomOver" in final and "V1_TitleOver" in final, "bloom first, then the title over it"
    assert [entry[0] for entry in report.comp_chains["shot_000"]] == [10, 20]
    # undo the last fusion layer: the clip returns to a single-layer comp
    while report.undo_stack and report.undo_stack[-1].get("action") not in {"replace_comp", "multi"}:
        report.undo_stack.pop()
    executor.undo_last()
    assert len(fake.comps[item]) == 1 and len(report.comp_chains["shot_000"]) == 1


def test_a_failing_decorative_layer_is_isolated_not_a_rollback(tmp_path, edg):
    from vanta.core.schemas import EffectLayer

    broken = edg.model_copy(deep=True)
    # a freeze without its required parameters cannot render: that layer fails, the build continues
    broken.effects = [EffectLayer(template="freeze_frame", shot_id="shot_001", start_frame=30, end_frame=60, purpose="hold", confidence=0.9)]
    fake, events = FakeResolve(), []
    executor = _executor(tmp_path, fake, _ledger(tmp_path, {"G1", "G3", "G4", "G12", "G13"}), events)
    report = asyncio.run(executor.execute(broken, include_render=False))
    assert not fake.rolled_back and report.failed == 1
    assert set(report.item_ids) == {"shot_000", "shot_001", "shot_002"} and len(report.grade_checks) == 3
    assert any("FAILED" in f and "freeze_frame" in f for f in report.fallbacks) or any(e.status.value == "failed" and "freeze" in (e.message or "").lower() for e in events)


def test_colour_managed_projects_never_get_a_double_camera_decode(tmp_path, edg):
    fake, events = FakeResolve(), []
    fake.color_science = "davinciYRGBColorManagedv2"
    executor = _executor(tmp_path, fake, _ledger(tmp_path, {"G1", "G3", "G4", "G12"}), events)
    report = asyncio.run(executor.execute(edg, include_render=False))
    assert "set_lut" not in [c[0] for c in fake.calls] and not report.grade_checks
    assert sum("colour management" in f for f in report.fallbacks) == 3


def _drx_setup(tmp_path, edg, *, gates=("G1", "G4", "G4b"), skin=0.0):
    template = tmp_path / "vanta_8node.drx"
    template.write_text("drx", encoding="utf-8")
    ledger = _ledger(tmp_path, set(gates))
    ledger.calibration["drx_template"] = {"path": str(template)}
    graded = edg.model_copy(deep=True)
    for g in graded.grades:
        g.look_lut_path = g.lut_path  # identity CDL + no decode: the tree reproduces the full LUT
        g.tech_cdl = {"Slope": "1 1 1", "Offset": "0 0 0", "Power": "1 1 1", "Saturation": "1.00000"}
        g.params = {**g.params, "skin_protect": skin}
    return ledger, graded


def test_eight_node_drx_route_is_used_and_export_verified(tmp_path, edg):
    ledger, graded = _drx_setup(tmp_path, edg)
    fake, events = FakeResolve(), []
    report = asyncio.run(_executor(tmp_path, fake, ledger, events).execute(graded, include_render=False))
    assert all(c["route"] == "drx_template" and c["passed"] for c in report.grade_checks.values())
    cdl = [c[1] for c in fake.calls if c[0] == "set_cdl"]
    looks = [c[1] for c in fake.calls if c[0] == "set_lut"]
    assert len(cdl) == 3 and all(c["node_index"] == 2 for c in cdl), "CDL on BALANCE"
    assert looks and all(c["node_index"] == 4 for c in looks), "look LUT on LOOK; no decode LUT for Rec.709 footage"
    assert not any(c[0] == "create_magic_mask" for c in fake.calls), "no Magic Mask without G6b"


def test_wrong_template_falls_back_to_the_single_verified_lut(tmp_path, edg):
    ledger, graded = _drx_setup(tmp_path, edg)
    fake, events = FakeResolve(), []
    fake.drx_labels = ["Corrector 1", "Corrector 2"]
    report = asyncio.run(_executor(tmp_path, fake, ledger, events).execute(graded, include_render=False))
    assert all(c["route"] == "lut_only" and c["passed"] for c in report.grade_checks.values())
    assert any(c[0] == "reset_grade" for c in fake.calls)
    assert any(c[0] == "set_lut" and c[1]["node_index"] == 1 for c in fake.calls)


def test_magic_mask_goes_on_skin_or_the_tree_is_rebuilt_without_it(tmp_path, edg):
    ledger, graded = _drx_setup(tmp_path, edg, gates=("G1", "G4", "G4b", "G6b"), skin=0.8)
    fake, events = FakeResolve(), []
    report = asyncio.run(_executor(tmp_path, fake, ledger, events).execute(graded, include_render=False))
    assert all(c.get("magic_mask") == "SKIN" for c in report.grade_checks.values())
    fake2, events2 = FakeResolve(), []
    fake2.mask_ok = False
    (tmp_path / "b").mkdir()
    report2 = asyncio.run(_executor(tmp_path / "b", fake2, ledger, events2).execute(graded, include_render=False))
    assert all(c["route"] == "drx_template" and "magic_mask" not in c for c in report2.grade_checks.values())
    assert any("Magic Mask not placed" in (e.message or "") for e in events2)
