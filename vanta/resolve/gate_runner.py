"""Automated Resolve compatibility check (manual 7, gates G1-G16).

Runs in a *disposable* project ``VANTA_GATE_<stamp>`` that it creates itself,
with synthetic media whose ground truth is known, then reloads the user's
previous project. Each gate records PASS/PARTIAL/FAIL with evidence and the
calibration it measured (source-end convention, record-frame origin, Text+
size factor). The user must confirm before it runs (it switches projects).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from vanta.analysis import synthetic
from vanta.analysis.media import FrameReader, probe
from vanta.compile.fcpxml import compile_edg_to_fcpxml
from vanta.compile.fusion import load_registry, render_template
from vanta.config import get_settings
from vanta.core.schemas import EditDecisionGraph, Shot, SourceMedia
from vanta.resolve.adapter import ResolveScriptingAdapter, ResolveUnavailable
from vanta.resolve.ledger import Ledger, load_ledger
from vanta.solvers.grade_math import LookParams, apply_look
from vanta.solvers.lut import read_cube, transform_error, write_cube

Progress = Callable[[str, str], None]


@dataclass
class GateResult:
    gate: str
    status: str  # PASS | PARTIAL | FAIL | NOT_RUN
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateRun:
    project: str
    results: list[GateResult]
    previous_project: str | None
    version: str | None
    folder: str


def _media(folder: Path) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    clips = []
    patterns = ["testsrc2", "smptebars", "mandelbrot", "rgbtestsrc", "testsrc"]
    for i, pattern in enumerate(patterns):
        clips.append(synthetic.make_test_clip(folder / f"gate_clip_{i}.mp4", duration_s=4.0, size=(1080, 1920), fps=30, pattern=pattern, text=f"GATE {i}", with_audio_hz=220 + 110 * i))
    music = synthetic.make_click_track(folder / "gate_music.wav", bpm=120, duration_s=12, drop_at_s=6.0)
    look = LookParams(contrast=1.15, saturation=0.85, shadow_tint=(-0.02, 0.0, 0.03), highlight_tint=(0.03, 0.01, -0.02))
    lut = write_cube(folder / "gate_look.cube", lambda x: apply_look(x, look), size=33, title="gate")
    matte_dir = folder / "mattes"
    matte_dir.mkdir(exist_ok=True)
    import cv2

    mattes = []
    for f in range(120):
        img = np.zeros((1920, 1080), np.uint8)
        cv2.circle(img, (540 + int(200 * np.sin(f / 20)), 960), 300, 255, -1)
        path = matte_dir / f"matte_{f:05d}.png"
        cv2.imwrite(str(path), cv2.merge([img, img, img, img]))
        mattes.append(str(path))
    return {"clips": [str(c) for c in clips], "music": str(music), "lut": str(lut), "mattes": mattes, "matte_dir": str(matte_dir)}


def run_gates(*, progress: Progress | None = None, include: set[str] | None = None) -> GateRun:
    settings = get_settings()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder = settings.data_dir / "gates" / stamp
    say = progress or (lambda gate, msg: None)
    ledger: Ledger = load_ledger()
    adapter = ResolveScriptingAdapter(settings.resolve_root, allow_writes=True, output_root=settings.data_dir, timeout_s=45)
    status = adapter.capabilities()
    if not status.get("connected"):
        raise ResolveUnavailable(status.get("reason", "Resolve is not running / Local scripting disabled"))
    version = status.get("version_string")
    ledger.product = status.get("product")
    results: list[GateResult] = []
    want = include or {"G1", "G2", "G3", "G4", "G4b", "G6", "G6b", "G10", "G11", "G12", "G13", "G15", "G16"}
    say("setup", "Generating synthetic gate media with known ground truth")
    media = _media(folder / "media")
    project_name = f"VANTA_GATE_{stamp}"
    say("setup", f"Creating disposable project {project_name}")
    created = adapter.call("create_project", name=project_name)
    previous = created.get("previous_project")
    try:
        imported = adapter.call("import_media", paths=media["clips"] + [media["music"]], folder_name="VANTA_GATE_MEDIA")
        adapter.register_disposable_session("gate", project_name)

        def new_timeline(name: str) -> str:
            result = adapter.create_timeline(name, snapshot_id="gate", fps="30", width=1080, height=1920)
            return result["timeline_id"]

        # ---------------------------------------------------------- G10
        if "G10" in want:
            results.append(GateResult("G10", "PASS" if version else "FAIL", f"Resolve {version} ({status.get('product')}) recorded; executor refuses other versions until re-gated", {"version": version, "product": status.get("product"), "studio": status.get("studio")}))
        # ---------------------------------------------------------- G1
        tl = None
        items: dict[int, str] = {}
        if "G1" in want or {"G3", "G4", "G4b", "G6", "G12", "G13"} & want:
            say("G1", "Build: 5 clips, source-frame trims, trackIndex, markers, idempotence")
            tl = new_timeline("VANTA_GATE_G1_ASSEMBLY_V1")
            evidence: dict[str, Any] = {"imported": imported, "clips": []}
            ok = True
            cursor = 0
            for i, clip in enumerate(media["clips"]):
                src_in, src_out = 10 + i * 3, 70 + i * 3
                res = adapter.append_clip(tl, clip, src_in, src_out, cursor, 1, media_type=1)
                items[i] = res["item_id"]
                evidence["clips"].append(res)
                ok = ok and bool(res.get("verified"))
                cursor += src_out - src_in
            adapter.add_marker(tl, 15, "Blue", "AP:gate", "gate marker", "vanta:gate:m1")
            marker = adapter.read_state({"scope": "marker", "timeline_id": tl, "custom_data": "vanta:gate:m1"})
            again = adapter.add_marker(tl, 15, "Blue", "AP:gate", "gate marker", "vanta:gate:m1")
            markers = adapter.read_state({"scope": "markers", "timeline_id": tl}).get("markers", {})
            idempotent = bool(again.get("idempotent")) and sum(1 for m in markers.values() if m.get("customData") == "vanta:gate:m1") == 1
            ok = ok and marker.get("frame") == 15 and idempotent
            convention = {c.get("end_convention") for c in evidence["clips"]}
            ledger.calibration["source_end_convention"] = next(iter(convention)) if len(convention) == 1 else "mixed"
            evidence.update(marker=marker, idempotent=idempotent, end_convention=list(convention))
            results.append(GateResult("G1", "PASS" if ok else "FAIL", "exact trims/positions/markers and no duplicate on re-run" if ok else "read-back mismatch (see evidence)", evidence))
        # ---------------------------------------------------------- G2
        if "G2" in want:
            say("G2", "Interchange: FCPXML import with exact durations")
            try:
                info = probe(media["clips"][0])
                edg = EditDecisionGraph(
                    project_id="gate",
                    fps=30,
                    timeline_name="VANTA_GATE_G2_ASSEMBLY_V1",
                    sources=[SourceMedia(id=f"c{i}", path=c, fps=30, duration_frames=info.frame_count) for i, c in enumerate(media["clips"][:3])],
                    shots=[Shot(id=f"s{i}", source_media_id=f"c{i}", source_in_frame=5, source_out_frame=50, timeline_start_frame=45 * i) for i in range(3)],
                )
                xml = compile_edg_to_fcpxml(edg, folder / "g2.fcpxml")
                res = adapter.call("import_timeline", path=str(xml), name="VANTA_GATE_G2_ASSEMBLY_V1")
                durations = [it["duration"] for it in res["items"] if it["track_type"] == "video"]
                ok = durations == [45, 45, 45]
                results.append(GateResult("G2", "PASS" if ok else "PARTIAL", f"imported durations {durations}", {"items": res["items"]}))
            except Exception as exc:
                results.append(GateResult("G2", "FAIL", f"FCPXML import failed: {exc}"))
        # ---------------------------------------------------------- G4
        if "G4" in want and tl:
            say("G4", "Grade primitives: SetLUT, GetLUT read-back, ExportLUT < 1 dE")
            try:
                res = adapter.call("set_lut", timeline_id=tl, item_id=items[0], node_index=1, lut_path=media["lut"])
                export = adapter.call("export_lut", timeline_id=tl, item_id=items[0], export_path=str(folder / "g4_export.cube"), size=33)
                err = transform_error(read_cube(folder / "g4_export.cube").apply, read_cube(media["lut"]).apply) if export.get("verified") else {"mean_de": None}
                ok = bool(res.get("verified")) and err.get("mean_de") is not None and err["mean_de"] < 1.0
                results.append(GateResult("G4", "PASS" if ok else "FAIL", f"GetLUT={res.get('observed_lut')}, export mean dE={err.get('mean_de')}", {"set": res, "error": err}))
            except Exception as exc:
                results.append(GateResult("G4", "FAIL", str(exc)))
        # ---------------------------------------------------------- G4b
        if "G4b" in want and tl:
            template = (ledger.calibration.get("drx_template") or {}).get("path")
            if not template or not Path(template).is_file():
                results.append(GateResult("G4b", "BLOCKED", "no 8-node template captured yet (System > Capture 8-node grade template)"))
            else:
                say("G4b", "8-node DRX: apply template, labels, LOOK LUT + BALANCE CDL, ExportLUT < 1 dE")
                try:
                    from vanta.solvers.grade import NODE_LABELS

                    item = items[1] if len(items) > 1 else items[0]
                    applied = adapter.call("apply_drx", timeline_id=tl, item_id=item, drx_path=template, grade_mode=0)
                    labels = [str(x or "").strip().upper() for x in applied.get("labels", [])]
                    labels_ok = labels[: len(NODE_LABELS)] == list(NODE_LABELS)
                    adapter.call("set_cdl", timeline_id=tl, item_id=item, node_index=2, slope="1 1 1", offset="0 0 0", power="1 1 1", saturation=1.0)
                    look = adapter.call("set_lut", timeline_id=tl, item_id=item, node_index=4, lut_path=media["lut"])
                    adapter.call("export_lut", timeline_id=tl, item_id=item, export_path=str(folder / "g4b_export.cube"), size=33)
                    err = transform_error(read_cube(folder / "g4b_export.cube").apply, read_cube(media["lut"]).apply) if (folder / "g4b_export.cube").is_file() else {"mean_de": None}
                    ok = labels_ok and bool(look.get("verified")) and err.get("mean_de") is not None and err["mean_de"] < 1.0
                    results.append(GateResult("G4b", "PASS" if ok else "FAIL", f"labels {labels}; LOOK LUT read-back {look.get('verified')}; export mean dE={err.get('mean_de')}", {"labels": labels, "error": err}))
                    if "G6b" in want and ok:
                        say("G6b", "Magic Mask: tracked on the SKIN node only (GetToolsInNode proof)")
                        mask = adapter.call("create_magic_mask", timeline_id=tl, item_id=item, node_index=7, mode="BI")
                        results.append(GateResult("G6b", "PASS" if mask.get("verified") else "FAIL", f"accepted={mask.get('accepted')}, nodes with mask {mask.get('nodes_with_mask')}", mask))
                except Exception as exc:
                    results.append(GateResult("G4b", "FAIL", str(exc)))
        # ---------------------------------------------------------- G12 / G13
        if "G12" in want and tl:
            say("G12", "Transitions API: Cross Dissolve exact duration")
            try:
                res = adapter.call("add_transition", timeline_id=tl, item_id=items[2], type="Cross Dissolve", position="start", alignment="center", duration=10)
                results.append(GateResult("G12", "PASS" if res.get("verified") else "PARTIAL", f"transition duration {res.get('duration')}", res))
            except Exception as exc:
                results.append(GateResult("G12", "FAIL", str(exc)))
        if "G13" in want and tl:
            say("G13", "Speed API: constant speed")
            try:
                res = adapter.call("set_speed", timeline_id=tl, item_id=items[3], percentage=50.0)
                results.append(GateResult("G13", "PARTIAL" if res.get("verified") else "FAIL", "constant speed verified; ramps use the pre-render route" if res.get("verified") else f"speed read-back {res.get('observed')}", res))
            except Exception as exc:
                results.append(GateResult("G13", "FAIL", str(exc)))
        # ---------------------------------------------------------- G6
        if "G6" in want and tl:
            say("G6", "Mattes: AddClipMattesToMediaPool")
            try:
                res = adapter.call("add_clip_mattes", source_path=media["clips"][4], matte_paths=media["mattes"][:120])
                results.append(GateResult("G6", "PARTIAL" if res.get("verified") else "FAIL", "mattes attached to the media-pool clip (frame alignment needs a visual check)" if res.get("verified") else "matte list empty", res))
            except Exception as exc:
                results.append(GateResult("G6", "FAIL", str(exc)))
        # ---------------------------------------------------------- G3
        if "G3" in want and tl:
            say("G3", "Fusion text: template import, render one frame, OCR round trip")
            try:
                registry = load_registry()
                comp = render_template(registry["text_basic"], {"text": "VANTA GATE", "size": 0.09, "center": [0.5, 0.55], "in_frame": 0, "out_frame": 59}, folder / "g3.comp", width=1080, height=1920, duration_frames=60)
                imp = adapter.call("import_fusion_comp", timeline_id=tl, item_id=items[1], comp_path=str(comp))
                render = adapter.call("render_start", timeline_id=tl, target_dir=str(folder / "g3"), custom_name="g3", width=1080, height=1920, mark_in=60 + 25, mark_out=60 + 35, quality="High")
                deadline = time.monotonic() + 240
                while time.monotonic() < deadline:
                    time.sleep(1)
                    st = adapter.call("render_status", job_id=render["job_id"]).get("status", {})
                    if st.get("JobStatus") in {"Complete", "Failed", "Cancelled"}:
                        break
                files = sorted((folder / "g3").glob("g3*"))
                seen = []
                if files:
                    from vanta.analysis.ocr import detect_text, normalise_text

                    with FrameReader(files[-1]) as reader:
                        frame = reader.read_index(5)
                    found = detect_text(frame) if frame is not None else []
                    seen = [d.text for d in found]
                    hit = next((d for d in found if "vantagate" in normalise_text(d.text)), None)
                    ok = hit is not None
                    centre_err = abs((hit.box[1] + hit.box[3] / 2) - 0.45) if hit else None
                    results.append(GateResult("G3", "PASS" if ok and centre_err is not None and centre_err < 0.02 else ("PARTIAL" if ok else "FAIL"), f"OCR read {seen}; vertical centre error {centre_err}", {"import": imp, "ocr": seen, "centre_error": centre_err}))
                    if hit:
                        ledger.calibration["text_size_factor_observed_cap"] = hit.box[3]
                else:
                    results.append(GateResult("G3", "FAIL", "render produced no file", {"import": imp}))
            except Exception as exc:
                results.append(GateResult("G3", "FAIL", str(exc)))
        # ---------------------------------------------------------- G11
        if "G11" in want:
            say("G11", "Live-follow: timed page switches")
            timings = []
            for page in ("edit", "color", "fusion", "edit", "color", "deliver", "edit", "color", "fusion", "edit") * 2:
                t0 = time.monotonic()
                res = adapter.open_page(page)
                timings.append({"page": page, "verified": res.get("verified"), "seconds": round(time.monotonic() - t0, 3)})
            within = sum(1 for t in timings if t["verified"] and t["seconds"] <= 1.0)
            results.append(GateResult("G11", "PARTIAL" if within >= 18 else "FAIL", f"{within}/20 page switches verified within 1 s (screen repaint needs a recording check)", {"timings": timings}))
        # ---------------------------------------------------------- G15
        if "G15" in want:
            from vanta.solvers.text import scan_fonts

            fonts = scan_fonts()
            results.append(GateResult("G15", "PARTIAL", f"{len(fonts)} fonts and {len(load_registry())} VANTA templates registered; Resolve effects/macros are listed by the registry scan", {"fonts": len(fonts)}))
        # ---------------------------------------------------------- G16
        if "G16" in want and tl:
            say("G16", "Failure injection: locked track, missing media, out-of-root render")
            checks = {}
            adapter.call("set_track_lock", timeline_id=tl, track_type="video", track_index=1, locked=True)
            try:
                adapter.call("ensure_tracks", timeline_id=tl, video_tracks=1, audio_tracks=1)
                checks["locked_track_refused"] = False
            except ResolveUnavailable as exc:
                checks["locked_track_refused"] = "locked" in str(exc)
            adapter.call("set_track_lock", timeline_id=tl, track_type="video", track_index=1, locked=False)
            try:
                adapter.append_clip(tl, str(folder / "does_not_exist.mp4"), 0, 10, 0, 1)
                checks["missing_media_refused"] = False
            except ResolveUnavailable:
                checks["missing_media_refused"] = True
            try:
                adapter.call("render_start", timeline_id=tl, target_dir="C:/Windows/Temp/vanta_should_refuse", custom_name="x", width=540, height=960)
                checks["out_of_root_render_refused"] = False
            except ResolveUnavailable:
                checks["out_of_root_render_refused"] = True
            ok = all(checks.values())
            results.append(GateResult("G16", "PARTIAL" if ok else "FAIL", "clean refusals for locked track, missing media and unsafe output; Resolve-restart and modal-dialog cases need a manual run", checks))
        for gate in ("G3b", "G5", "G8", "G9", "G14"):
            results.append(GateResult(gate, "NOT_RUN", {"G3b": "needs a real subject matte over text", "G5": "needs the hand-built 8-node DRX template", "G8": "needs speech footage", "G9": "benchmarks deferred to the end of the build", "G14": "needs a handheld clip with an external camera solve"}[gate]))
    finally:
        if previous:
            say("cleanup", f"Reloading your project {previous}")
            try:
                adapter.call("load_project", name=previous)
            except ResolveUnavailable:
                pass
        adapter.close()
    for result in results:
        ledger.record(result.gate, result.status, {"summary": result.summary, **{k: v for k, v in result.evidence.items() if k != "clips"}}, version=version)
    ledger.save()
    (folder / "gate_results.json").write_text(json.dumps([r.__dict__ for r in results], indent=2, default=str), encoding="utf-8")
    return GateRun(project_name, results, previous, version, str(folder))
