"""Automated QC (manual 17): L0 schema, L1 timeline, L2 frames, L3 audio, L4 style.

L5 (editorial VLM rubric) lives in ``vanta.reasoning.critic`` and is a warning,
never an authority. Blocking findings stop the run before final render.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from vanta.analysis.color import frame_stats
from vanta.analysis.frame_vision import detect_faces
from vanta.analysis.media import read_rgb_fixed
from vanta.compile.fusion import load_registry
from vanta.core.schemas import EditDecisionGraph, QCFinding, QCResult
from vanta.solvers.text import SAFE_ZONES, contrast_ratio, estimate_box


def l0_schema(edg: EditDecisionGraph, *, rule_ids: set[str] | None = None, fonts: set[str] | None = None) -> list[QCFinding]:
    findings = []
    try:
        EditDecisionGraph.model_validate(edg.model_dump())
    except Exception as exc:
        findings.append(QCFinding(level=0, kind="schema", severity=1, confidence=1, description=f"EDG failed validation: {exc}", blocking=True))
    for src in edg.sources:
        if not Path(src.path).is_file():
            findings.append(QCFinding(level=0, kind="missing_media", severity=1, confidence=1, description=f"source media missing: {src.path}", blocking=True, target_id=src.id))
    for grade in edg.grades:
        if grade.lut_path and not Path(grade.lut_path).is_file():
            findings.append(QCFinding(level=0, kind="missing_lut", severity=0.9, confidence=1, description=f"LUT missing for {grade.shot_id}", blocking=True, target_id=grade.shot_id))
    registry = load_registry()
    for layer in [*edg.texts, *edg.effects]:
        if layer.template not in registry:
            findings.append(QCFinding(level=0, kind="unknown_template", severity=1, confidence=1, description=f"template {layer.template} is not registered", blocking=True, target_id=layer.id))
    for t in edg.transitions:
        if t.template and t.template not in registry:
            findings.append(QCFinding(level=0, kind="unknown_template", severity=1, confidence=1, description=f"transition template {t.template} is not registered", blocking=True, target_id=t.id))
    if fonts is not None:
        for layer in edg.texts:
            if layer.font and layer.font not in fonts:
                findings.append(QCFinding(level=0, kind="missing_font", severity=0.8, confidence=1, description=f"font {layer.font} is not installed", blocking=True, target_id=layer.id, repair="MOVE_TEXT"))
    if rule_ids is not None:
        for grade in edg.grades:
            missing = [r for r in grade.provenance.rules if r not in rule_ids]
            if missing:
                findings.append(QCFinding(level=0, kind="unknown_rule", severity=0.3, confidence=1, description=f"{grade.shot_id} cites unknown rules {missing}", target_id=grade.shot_id))
    for matte in edg.mattes:
        if matte.method != "pending" and not (Path(matte.directory) / (matte.pattern % 0)).is_file():
            findings.append(QCFinding(level=0, kind="missing_matte", severity=0.9, confidence=1, description=f"matte frames missing for {matte.shot_id}", blocking=True, target_id=matte.id))
    return findings


def l1_timeline(edg: EditDecisionGraph, *, expected_duration_s: float | None = None) -> list[QCFinding]:
    findings = []
    shots = edg.ordered_shots(1)
    cursor = 0
    for shot in shots:
        if shot.timeline_start_frame > cursor:
            findings.append(QCFinding(level=1, kind="gap", severity=0.9, confidence=1, description=f"gap of {shot.timeline_start_frame - cursor} frames before {shot.id}", blocking=True, affected_range_s=(cursor / edg.fps, shot.timeline_start_frame / edg.fps), target_id=shot.id, repair="RETIME_CUT"))
        length = edg.timeline_duration(shot)
        if length < 6:
            findings.append(QCFinding(level=1, kind="flash_frame", severity=0.7, confidence=1, description=f"{shot.id} is only {length} frames", blocking=True, target_id=shot.id, repair="RETIME_CUT"))
        cursor = shot.timeline_start_frame + length
    if expected_duration_s:
        diff = abs(cursor / edg.fps - expected_duration_s)
        if diff > 1.5 / edg.fps:
            findings.append(QCFinding(level=1, kind="duration", severity=min(1.0, diff / 2), confidence=1, description=f"timeline is {cursor / edg.fps:.2f}s, expected {expected_duration_s:.2f}s", blocking=diff > 0.5))
    if edg.music:
        src = edg.source(edg.music.source_media_id)
        rate = src.fps or edg.fps
        music_len = ((edg.music.source_out_frame or src.duration_frames or 0) - edg.music.source_in_frame) / rate
        if music_len + 0.05 < cursor / edg.fps:
            findings.append(QCFinding(level=1, kind="music_short", severity=0.6, confidence=1, description="music bed ends before the picture"))
        if not edg.music.licence_confirmed and not edg.music.guide_only:
            findings.append(QCFinding(level=1, kind="music_rights", severity=0.8, confidence=1, description="music licence not confirmed; final render blocked until confirmed", blocking=False, target_id=edg.music.source_media_id))
    for matte in edg.mattes:
        if matte.review_required and not matte.approved:
            findings.append(QCFinding(level=1, kind="matte_review", severity=0.5, confidence=matte.confidence, description=f"matte for {matte.shot_id} needs review (confidence {matte.confidence:.2f})", target_id=matte.id))
    return findings


def _ocr_match(frame: np.ndarray, text: str) -> tuple[bool, str]:
    from vanta.analysis.ocr import OCRUnavailable, detect_text, normalise_text

    try:
        found = detect_text(frame, min_score=0.5)
    except OCRUnavailable:
        return True, "OCR unavailable"
    want = normalise_text(text)
    joined = normalise_text("".join(d.text for d in found))
    hits = [normalise_text(d.text) for d in found]
    ok = want in joined or any(want in h or h in want and len(h) >= max(3, len(want) - 2) for h in hits)
    return ok, " | ".join(d.text for d in found)[:120]


def l2_frames(edg: EditDecisionGraph, render: Path, *, platform: str = "instagram_reel", check_ocr: bool = True) -> list[QCFinding]:
    findings = []
    cap = cv2.VideoCapture(str(render))
    if not cap.isOpened():
        return [QCFinding(level=2, kind="render_unreadable", severity=1, confidence=1, description=f"cannot read render {render}", blocking=True)]
    fps = cap.get(cv2.CAP_PROP_FPS) or edg.fps
    zone = SAFE_ZONES.get(platform, SAFE_ZONES["instagram_reel"])

    def frame_at(timeline_frame: int) -> np.ndarray | None:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(timeline_frame / edg.fps * fps))
        ok, bgr = cap.read()
        return read_rgb_fixed(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), render) if ok else None

    for layer in edg.texts:
        mid = (layer.start_frame + layer.end_frame) // 2
        frame = frame_at(mid)
        if frame is None:
            continue
        h, w = frame.shape[:2]
        if check_ocr:
            ok, seen = _ocr_match(frame, layer.text)
            if not ok:
                findings.append(QCFinding(level=2, kind="ocr_mismatch", severity=0.85, confidence=0.8, description=f"rendered text does not read as '{layer.text}' (OCR saw: {seen or 'nothing'})", blocking=True, affected_range_s=(layer.start_frame / edg.fps, layer.end_frame / edg.fps), target_id=layer.id, repair="MOVE_TEXT"))
        box, _ = estimate_box(layer.text, layer.size, w / h)
        x0, x1 = layer.x - box.w / 2, layer.x + box.w / 2
        y0, y1 = layer.y - box.h / 2, layer.y + box.h / 2
        if y0 < zone.top or y1 > 1 - zone.bottom or x0 < zone.left or x1 > 1 - zone.right:
            findings.append(QCFinding(level=2, kind="unsafe_text", severity=0.6, confidence=0.9, description=f"'{layer.text}' enters the {platform} UI zone", blocking=True, target_id=layer.id, repair="MOVE_TEXT", affected_range_s=(layer.start_frame / edg.fps, layer.end_frame / edg.fps)))
        if not layer.behind_subject:
            for face in detect_faces(frame):
                ix = max(0.0, min(x1, face["x"] + face["w"]) - max(x0, face["x"]))
                iy = max(0.0, min(y1, face["y"] + face["h"]) - max(y0, face["y"]))
                if ix * iy > 0.05 * face["w"] * face["h"]:
                    findings.append(QCFinding(level=2, kind="text_over_face", severity=0.8, confidence=0.85, description=f"'{layer.text}' overlaps a face", blocking=True, target_id=layer.id, repair="MOVE_TEXT", affected_range_s=(layer.start_frame / edg.fps, layer.end_frame / edg.fps)))
                    break
        crop = frame[int(max(0, y0) * h) : int(min(1, y1) * h), int(max(0, x0) * w) : int(min(1, x1) * w)]
        if crop.size:
            ratio = contrast_ratio(layer.color, cv2.GaussianBlur(crop, (0, 0), 6))
            if ratio < 2.0 and not layer.outline:
                findings.append(QCFinding(level=2, kind="low_contrast_text", severity=0.6, confidence=0.7, description=f"'{layer.text}' contrast {ratio:.1f}:1", target_id=layer.id, repair="MOVE_TEXT"))
    for shot in edg.ordered_shots(1):
        mid = shot.timeline_start_frame + edg.timeline_duration(shot) // 2
        frame = frame_at(mid)
        if frame is None:
            findings.append(QCFinding(level=2, kind="missing_frames", severity=1, confidence=1, description=f"render has no frame for {shot.id}", blocking=True, target_id=shot.id))
            continue
        stats = frame_stats(frame)
        if stats.luma_mean < 0.02:
            findings.append(QCFinding(level=2, kind="black_frame", severity=0.9, confidence=0.9, description=f"{shot.id} renders black (offline media?)", blocking=True, target_id=shot.id))
        clipped = stats.clip_low + stats.clip_high
        if clipped > 0.08:
            findings.append(QCFinding(level=2, kind="clipping", severity=min(1.0, clipped * 4), confidence=0.85, description=f"{shot.id}: {clipped * 100:.1f}% pixels clipped", blocking=clipped > 0.2, target_id=shot.id, repair="ADJUST_GRADE", affected_range_s=(shot.timeline_start_frame / edg.fps, edg.shot_end(shot) / edg.fps)))
    for matte in edg.mattes:
        if matte.method != "pending" and matte.confidence < 0.6:
            findings.append(QCFinding(level=2, kind="matte_flicker", severity=0.5, confidence=0.7, description=f"matte for {matte.shot_id} is unstable ({matte.confidence:.2f})", target_id=matte.id))
    cap.release()
    return findings


def l3_audio(edg: EditDecisionGraph, render: Path, *, target_lufs: float = -14.0, beat_times: list[float] | None = None) -> tuple[list[QCFinding], dict]:
    import soundfile as sf

    findings: list[QCFinding] = []
    metrics: dict = {}
    try:
        from vanta.analysis.media import extract_audio

        wav = extract_audio(render, render.with_suffix(".qc.wav"), sample_rate=48000, mono=False)
        data, rate = sf.read(str(wav), dtype="float32", always_2d=True)
    except Exception as exc:
        if edg.music or any(s.audio_enabled for s in edg.shots):
            findings.append(QCFinding(level=3, kind="no_audio", severity=0.9, confidence=0.9, description=f"render has no readable audio ({exc})", blocking=True))
        return findings, metrics
    import pyloudnorm

    meter = pyloudnorm.Meter(rate)
    lufs = float(meter.integrated_loudness(data)) if len(data) > rate else float("nan")
    metrics["integrated_lufs"] = lufs
    import soxr

    peak = float(np.max(np.abs(soxr.resample(data, rate, rate * 4)))) if len(data) else 0.0
    peak_db = 20 * np.log10(max(peak, 1e-9))
    metrics["true_peak_dbtp"] = peak_db
    clip = float(np.mean(np.abs(data) >= 0.999))
    metrics["clipping"] = clip
    if np.isfinite(lufs) and abs(lufs - target_lufs) > 1.5:
        findings.append(QCFinding(level=3, kind="loudness", severity=min(1.0, abs(lufs - target_lufs) / 6), confidence=0.95, description=f"integrated loudness {lufs:.1f} LUFS vs target {target_lufs:.0f}", repair="ADJUST_MUSIC"))
    if peak_db > -1.0:
        findings.append(QCFinding(level=3, kind="true_peak", severity=0.7, confidence=0.95, description=f"true peak {peak_db:.1f} dBTP above -1", repair="ADJUST_MUSIC"))
    if clip > 1e-4:
        findings.append(QCFinding(level=3, kind="audio_clipping", severity=0.9, confidence=0.95, description=f"{clip * 100:.3f}% samples clipped", blocking=True, repair="ADJUST_MUSIC"))
    mono = data.mean(axis=1)
    block = int(rate * 0.4)
    if len(mono) > block * 4:
        levels = np.asarray([20 * np.log10(max(np.sqrt(np.mean(mono[i : i + block] ** 2)), 1e-9)) for i in range(0, len(mono) - block, block)])
        silent = float(np.mean(levels < -55))
        metrics["silence_fraction"] = silent
        if silent > 0.3 and edg.music:
            findings.append(QCFinding(level=3, kind="silence", severity=0.6, confidence=0.8, description=f"{silent * 100:.0f}% of the edit is near-silent"))
        jumps = np.abs(np.diff(levels))
        if jumps.size and jumps.max() > 14:
            at = float(np.argmax(jumps) * 0.4)
            findings.append(QCFinding(level=3, kind="level_jump", severity=0.5, confidence=0.7, description=f"sudden level change of {jumps.max():.0f} dB", affected_range_s=(at, at + 0.8)))
    if beat_times:
        from vanta.analysis.audio import beat_alignment_error

        cuts = [s.timeline_start_frame / edg.fps for s in edg.ordered_shots(1)[1:] if s.beat_aligned]
        if cuts:
            err = beat_alignment_error(cuts, np.asarray(beat_times))
            metrics["cut_to_beat"] = err
            if err["within_40ms"] < 0.9:
                findings.append(QCFinding(level=3, kind="cut_to_beat", severity=0.5, confidence=0.9, description=f"only {err['within_40ms'] * 100:.0f}% of beat-aligned cuts land within 40 ms", repair="RETIME_CUT"))
    return findings, metrics


SKIN_LINE_HUE = 50.0  # Lab hue of the vectorscope skin line (degrees)


def l4_style(edg: EditDecisionGraph, render: Path) -> list[QCFinding]:
    """Rendered look vs target, plus grading craft: skin fidelity, shot-to-shot continuity, gamut."""
    findings = []
    cap = cv2.VideoCapture(str(render))
    fps = cap.get(cv2.CAP_PROP_FPS) or edg.fps
    measured: dict[str, object] = {}
    for shot in edg.ordered_shots(1):
        mid = shot.timeline_start_frame + edg.timeline_duration(shot) // 2
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(mid / edg.fps * fps))
        ok, bgr = cap.read()
        if ok:
            rgb = read_rgb_fixed(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), render)
            faces = [(f["x"], f["y"], f["w"], f["h"]) for f in detect_faces(rgb)]
            measured[shot.id] = (rgb, frame_stats(rgb, face_boxes=faces or None), bool(faces))
    cap.release()
    span = {s.id: (s.timeline_start_frame / edg.fps, edg.shot_end(s) / edg.fps) for s in edg.ordered_shots(1)}
    for grade in edg.grades:
        if grade.shot_id not in measured:
            continue
        rgb, stats, has_face = measured[grade.shot_id]
        # skin must sit near the skin line whatever the look (manual: skin is protected)
        if has_face and stats.skin_hue is not None and stats.skin_fraction > 0.002:
            off = stats.skin_hue - SKIN_LINE_HUE
            if abs(off) > 16:
                findings.append(QCFinding(level=4, kind="skin_tone", severity=min(1.0, abs(off) / 40), confidence=0.7, description=f"{grade.shot_id}: skin sits {abs(off):.0f}° {'toward yellow/green' if off > 0 else 'toward magenta/red'} of the skin line", target_id=grade.shot_id, repair="ADJUST_GRADE", affected_range_s=span[grade.shot_id], suggested={"skin_protect": 1.0, "intensity": 0.85}))
        # channel (gamut) clipping: saturated colours flattened at the top of one channel
        x = rgb[::4, ::4].astype(np.float32) / 255
        flat = float(np.mean((x.max(axis=-1) >= 0.975) & (x.max(axis=-1) - x.min(axis=-1) > 0.35)))  # clipped channels decode at ~0.988 after compression
        if flat > 0.03:
            findings.append(QCFinding(level=4, kind="gamut_clipping", severity=min(1.0, flat * 8), confidence=0.75, description=f"{grade.shot_id}: {flat * 100:.1f}% of pixels clip a colour channel (flat, posterised colour)", target_id=grade.shot_id, repair="ADJUST_GRADE", affected_range_s=span[grade.shot_id], suggested={"saturation": -0.12, "exposure": -0.08}))
    # continuity: neighbouring shots in the same section should not jump in exposure or white balance
    ordered = [s for s in edg.ordered_shots(1) if s.id in measured]
    graded = {g.shot_id for g in edg.grades if not g.locked}
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        if prev.role != cur.role or cur.id not in graded:
            continue
        a, b = measured[prev.id][1], measured[cur.id][1]
        d_exp = b.luma_p50 - a.luma_p50
        d_warm = b.mid_b - a.mid_b
        if abs(d_exp) > 0.16 or abs(d_warm) > 9:
            change = {}
            if abs(d_exp) > 0.16:
                change["exposure"] = round(float(np.clip(-d_exp * 1.6, -0.5, 0.5)), 3)
            if abs(d_warm) > 9:
                change["warmth"] = round(float(np.clip(-d_warm / 10, -1.5, 1.5)), 3)
            findings.append(QCFinding(level=4, kind="continuity", severity=min(1.0, abs(d_exp) * 2 + abs(d_warm) / 25), confidence=0.65, description=f"{prev.id} -> {cur.id}: exposure jumps {d_exp:+.2f} and warmth {d_warm:+.1f} inside one section", target_id=cur.id, repair="ADJUST_GRADE", affected_range_s=span[cur.id], suggested=change))
    for grade in edg.grades:
        if grade.shot_id not in measured or not grade.target_stats:
            continue
        stats = measured[grade.shot_id][1]
        dc = abs(stats.contrast - grade.target_stats.get("contrast", stats.contrast))
        sep = (stats.high_b - stats.shadow_b) - grade.target_stats.get("separation", stats.high_b - stats.shadow_b)
        if dc > 0.12 or abs(sep) > 6:
            findings.append(QCFinding(level=4, kind="style_distance", severity=min(1.0, dc * 3 + abs(sep) / 20), confidence=0.6, description=f"{grade.shot_id}: rendered look drifts from target (Δcontrast {dc:.2f}, Δseparation {sep:.1f})", target_id=grade.shot_id, repair="ADJUST_GRADE"))
    return findings


def run_qc(
    edg: EditDecisionGraph,
    render: Path | None,
    *,
    run_id: str,
    iteration: int,
    expected_duration_s: float | None,
    platform: str,
    target_lufs: float,
    fonts: set[str] | None = None,
    rule_ids: set[str] | None = None,
    render_kind: str = "preview",
) -> QCResult:
    findings = l0_schema(edg, rule_ids=rule_ids, fonts=fonts) + l1_timeline(edg, expected_duration_s=expected_duration_s)
    metrics: dict = {"render_kind": render_kind}
    if render and render.is_file():
        findings += l2_frames(edg, render, platform=platform)
        audio_findings, audio_metrics = l3_audio(edg, render, target_lufs=target_lufs, beat_times=edg.music.beat_grid_s if edg.music else None)
        findings += audio_findings
        metrics.update(audio_metrics)
        findings += l4_style(edg, render)
    blocking = any(f.blocking for f in findings)
    return QCResult(run_id=run_id, status="blocked" if blocking else ("needs_revision" if findings else "pass"), findings=findings, blocking=blocking, iteration=min(iteration, 3), artifact=str(render) if render else None, metrics=metrics)
