"""Subject-follow reframe, preview effect stand-ins, grading QC checks, LUT precision, outdated analyses."""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from vanta.analysis.media import _tool, grab_frame, probe
from vanta.core.schemas import EditDecisionGraph, EffectLayer, GradeSpec, Reframe, Shot, SourceMedia
from vanta.solvers.reframe import (
    _contain,
    ffmpeg_expr,
    follow_keys,
    locate_subjects,
    operator_path,
    value_at,
)

W, H = 1280, 720


def _encode(frames: list[np.ndarray], path: Path, fps: int = 24) -> Path:
    h, w = frames[0].shape[:2]
    raw = path.with_suffix(".rgb")
    raw.write_bytes(np.stack(frames).tobytes())
    subprocess.run([_tool("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", str(raw), "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-c:v", "libx264", "-crf", "8", "-colorspace", "bt709", "-color_range", "tv", str(path)], check=True)
    return path


def _moving_square(n: int = 72) -> list[np.ndarray]:
    frames = []
    for i in range(n):
        f = np.full((H, W, 3), 40, np.uint8)
        x = int(100 + (W - 300) * i / (n - 1))
        f[300:420, x : x + 100] = 230
        frames.append(f)
    return frames


def _subject_x(img: np.ndarray) -> float | None:
    cols = np.where((img[..., 1] > 150).any(axis=0))[0]
    return float(cols.mean() / img.shape[1]) if len(cols) else None


# ------------------------------------------------------------------ tracking


def test_subject_is_located_on_the_subject_not_between_positions():
    frames = _moving_square()
    track = locate_subjects(frames[::6], [i / 24 for i in range(0, 72, 6)])
    truth = [(100 + (W - 300) * i / 71 + 50) / W for i in range(0, 72, 6)]
    assert max(abs(a - b) for a, b in zip(track.xs, truth, strict=True)) < 0.04
    assert set(track.method) == {"foreground"}


def test_operator_holds_still_inside_the_dead_zone_and_never_loses_the_subject():
    times = [i * 0.2 for i in range(20)]
    jitter = [0.5 + 0.01 * np.sin(i) for i in range(20)]
    assert np.ptp(operator_path(jitter, times, window=0.3)) < 1e-6, "small subject drift does not move the frame"
    sprint = list(np.linspace(0.1, 0.9, 20))
    path = operator_path(sprint, times, window=0.3)
    assert all(abs(c - x) <= 0.15 * 0.8 + 1e-6 or c in (0.15, 0.85) for c, x in zip(path, sprint, strict=True))
    assert _contain(0.5, 0.9, 0.15) == 0.78


def test_follow_keys_static_when_the_subject_stays_put():
    still = locate_subjects([np.full((H, W, 3), 60, np.uint8)] * 6, [i * 0.5 for i in range(6)], start=(0.7, 0.5))
    keys, pan, _ = follow_keys(still, W / H, 1080, 1920, 3.0)
    assert keys == [] and pan < 0, "subject right of centre -> image moves left"


def test_piecewise_expression_matches_the_keys():
    keys = [(0.0, 0.5, 0.0), (0.5, -0.2, 0.0), (1.0, 0.1, 0.0)]
    expr = ffmpeg_expr(keys, 1, 4.0)
    for t in (0.0, 1.0, 2.0, 3.0, 4.0):
        expected = value_at(keys, t / 4.0, 1)
        got = eval(expr.replace("if(", "_if(").replace("lt(", "_lt("), {"_if": lambda c, a, b: a if c else b, "_lt": lambda a, b: a < b, "max": max, "t": t})
        assert abs(got - expected) < 1e-3


def test_subject_follow_prerender_and_preview_keep_the_subject_in_frame(tmp_path):
    from vanta.render.prerender import render_speed_ramp
    from vanta.render.preview import contact_frames, render_preview

    src = _encode(_moving_square(), tmp_path / "moving.mp4")
    cap = cv2.VideoCapture(str(src))
    frames = [cv2.cvtColor(cap.read()[1], cv2.COLOR_BGR2RGB) for _ in range(72)]
    track = locate_subjects(frames[::6], [i / 24 for i in range(0, 72, 6)])
    keys, pan, tilt = follow_keys(track, W / H, 1080, 1920, 3.0)
    assert keys, "a subject crossing the frame needs a follow"
    edg = EditDecisionGraph(
        project_id="p", fps=24, width=1080, height=1920, timeline_name="VANTA_F_ASSEMBLY_V1",
        sources=[SourceMedia(id="s0", path=str(src), fps=24, duration_frames=72)],
        shots=[Shot(id="shot_000", source_media_id="s0", source_in_frame=0, source_out_frame=72, timeline_start_frame=0, confidence=0.9, reframe=Reframe(pan=pan, tilt=tilt, keys=keys))],
    )
    out = render_speed_ramp(edg, edg.shots[0], tmp_path / "follow.mov")
    info = probe(out)
    assert (info.width, info.height, info.frame_count) == (1080, 1920, 72)
    seen = [_subject_x(grab_frame(out, t)) for t in (0.1, 0.8, 1.5, 2.2)]
    assert all(x is not None and 0.05 < x < 0.95 for x in seen), seen
    preview = render_preview(edg, tmp_path / "preview.mp4", scale=0.5, include_audio=False, include_text=False)
    pv = [_subject_x(f) for f in contact_frames(preview, [0.1, 0.8, 1.5, 2.2])]
    assert all(a is not None and b is not None and abs(a - b) < 0.06 for a, b in zip(seen, pv, strict=True)), "preview frames like the pre-render"


# ----------------------------------------------------------- preview effects


def test_preview_renders_fusion_effect_stand_ins(tmp_path):
    from vanta.render.preview import contact_frames, render_preview

    src = _encode([np.full((H, W, 3), (120, 110, 100), np.uint8)] * 24, tmp_path / "flat.mp4")
    fx = [
        EffectLayer(template=t, shot_id="shot_000", start_frame=0, end_frame=24, params=p, purpose="t", confidence=0.9)
        for t, p in (("camera_shake", {}), ("parallax", {"zoom_0": 1.0, "zoom_1": 1.08}), ("cinematic_bloom", {}), ("speed_blur", {"amount": 0.06}), ("freeze_frame", {"freeze_at": 12, "freeze_at_p1": 13, "freeze_at_p4": 16}), ("letterbox", {"bar": 0.2562}))
    ]
    edg = EditDecisionGraph(
        project_id="p", fps=24, width=W, height=H, timeline_name="VANTA_X_ASSEMBLY_V1",
        sources=[SourceMedia(id="s0", path=str(src), fps=24, duration_frames=24)],
        shots=[Shot(id="shot_000", source_media_id="s0", source_in_frame=0, source_out_frame=24, timeline_start_frame=0, confidence=0.9)],
        effects=fx,
    )
    out = render_preview(edg, tmp_path / "fx.mp4", scale=1.0, include_audio=False, include_text=False)
    assert probe(out).frame_count == 24
    frame = contact_frames(out, [0.5])[0]
    bar = int(H * 0.2562 / 2)
    assert frame[: bar - 4].max() < 24 and frame[-(bar - 4) :].max() < 24, "2.39:1 scope bars"
    assert abs(int(frame[H // 2, W // 2, 0]) - 120) <= 6, "picture colour survives the effect chain"


# --------------------------------------------------------------- grading QC


def test_qc_flags_continuity_jumps_and_channel_clipping(tmp_path):
    from vanta.qc.levels import l4_style

    calm = np.full((H, W, 3), (110, 105, 100), np.uint8)
    hot = calm.copy()
    hot[:] = (230, 200, 150)  # same section, much brighter and warmer
    hot[:, : W // 4] = (255, 0, 0)  # saturated red flattened at the top of the channel
    render = _encode([calm] * 24 + [hot] * 24, tmp_path / "render.mp4")
    shots = [Shot(id=f"shot_00{i}", source_media_id="s0", source_in_frame=0, source_out_frame=24, timeline_start_frame=24 * i, confidence=0.9) for i in range(2)]
    edg = EditDecisionGraph(
        project_id="p", fps=24, width=W, height=H, timeline_name="VANTA_Q_ASSEMBLY_V1",
        sources=[SourceMedia(id="s0", path=str(render), fps=24, duration_frames=48)],
        shots=shots, grades=[GradeSpec(shot_id=s.id, confidence=0.9) for s in shots],
    )
    findings = {f.kind: f for f in l4_style(edg, render)}
    assert "continuity" in findings and findings["continuity"].target_id == "shot_001"
    assert findings["continuity"].suggested.get("exposure", 0) < 0, "repair darkens the jump"
    assert "gamut_clipping" in findings and findings["gamut_clipping"].suggested["saturation"] < 0


def test_critic_turns_measured_suggestions_into_grade_patches():
    from vanta.core.schemas import QCFinding
    from vanta.reasoning.critic import plan_repairs

    edg = EditDecisionGraph(
        project_id="p", fps=24, width=W, height=H, timeline_name="VANTA_Q_ASSEMBLY_V1",
        sources=[SourceMedia(id="s0", path="x.mp4", fps=24, duration_frames=48)],
        shots=[Shot(id="shot_000", source_media_id="s0", source_in_frame=0, source_out_frame=24, timeline_start_frame=0, confidence=0.9)],
        grades=[GradeSpec(shot_id="shot_000", confidence=0.9)],
    )
    finding = QCFinding(level=4, kind="skin_tone", severity=0.6, confidence=0.7, description="skin off", target_id="shot_000", repair="ADJUST_GRADE", suggested={"skin_protect": 1.0, "intensity": 0.85})
    plan = plan_repairs(edg, [finding])
    patch = next(p for p in plan.patches if p.dimension == "grade")
    assert patch.changes == {"skin_protect": 1.0, "intensity": 0.85}


# ------------------------------------------------------------ LUT + library


def test_log_footage_gets_65_point_luts():
    from vanta.solvers.grade import lut_size_for

    assert lut_size_for("rec709") == 33 and lut_size_for("slog3") == 65 and lut_size_for("hlg") == 65


def test_outdated_picture_analyses_are_flagged_but_music_is_not():
    from vanta.analysis.pipeline import ANALYSIS_VERSION, is_outdated

    assert is_outdated({"kind": "footage", "analysis_status": "done", "analysis_version": "analysis-v3"})
    assert not is_outdated({"kind": "footage", "analysis_status": "done", "analysis_version": ANALYSIS_VERSION})
    assert not is_outdated({"kind": "music", "analysis_status": "done", "analysis_version": "analysis-v3"})
    assert not is_outdated({"kind": "reference", "analysis_status": "pending", "analysis_version": None})
