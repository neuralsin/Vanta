"""VANTA's own preview renderer (FFmpeg) — the steer preview and the QC fallback.

This is *not* a substitute for Resolve: it approximates transitions and ramps
so the user can see roughly what the edit will look like before Resolve is
touched, and so QC can still run when Resolve is unavailable (results are
labelled ``preview`` and never satisfy a Resolve gate). Originals are only read.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from vanta.analysis.media import REC709_TAGS, _tool, file_matrix, read_rgb_fixed, run_tool
from vanta.core.colormath import correct_decoded
from vanta.core.schemas import EditDecisionGraph, Shot
from vanta.render.overlay import render_overlay_frame, render_text_layer


def _esc(path: str | Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _shot_filter(edg: EditDecisionGraph, shot: Shot, width: int, height: int, lut: str | None) -> str:
    src = edg.source(shot.source_media_id)
    parts = []
    reframe = shot.reframe
    zoom = reframe.zoom if reframe else 1.0
    matrix, full = file_matrix(src.path)
    # decode with the source's real matrix/range into 16-bit RGB: the LUT (camera decode + look) needs precision
    parts.append(f"scale={int(width * zoom)}:{int(height * zoom)}:force_original_aspect_ratio=increase:in_color_matrix={matrix}:in_range={'pc' if full else 'tv'}")
    parts.append("format=rgb48le")
    pan = reframe.pan if reframe else 0.0
    tilt = reframe.tilt if reframe else 0.0
    # image moves right by pan*W => the crop window moves left; clamp inside the scaled frame
    if reframe is not None and reframe.dynamic:
        from vanta.solvers.reframe import ffmpeg_expr

        clip_s = edg.timeline_duration(shot) / edg.fps * (shot.speed_percent / 100)  # crop runs before setpts: source time
        pan_e, tilt_e = ffmpeg_expr(reframe.keys, 1, clip_s), ffmpeg_expr(reframe.keys, 2, clip_s)
        ox = f"max(0,min(iw-{width},(iw-{width})/2-({pan_e})*{width}))"
        oy = f"max(0,min(ih-{height},(ih-{height})/2+({tilt_e})*{height}))"
    else:
        ox = f"max(0,min(iw-{width},(iw-{width})/2-({pan:.5f})*{width}))"
        oy = f"max(0,min(ih-{height},(ih-{height})/2+({tilt:.5f})*{height}))"
    parts.append(f"crop={width}:{height}:'{ox}':'{oy}'")
    if lut:
        parts.append(f"lut3d='{_esc(lut)}'")
    speed = shot.speed_percent / 100
    if abs(speed - 1) > 1e-3:
        parts.append(f"setpts=PTS/{speed:.5f}")
    else:
        parts.append("setpts=PTS-STARTPTS")
    parts.append(f"fps={edg.fps:g}")
    parts.extend(_effect_filters(edg, shot, width, height)[0])
    if shot.fade_in_frames:
        parts.append(f"fade=t=in:st=0:d={shot.fade_in_frames / edg.fps:.3f}")
    return ",".join(parts)


def _effect_filters(edg: EditDecisionGraph, shot: Shot, width: int, height: int) -> tuple[list[str], list[str]]:
    """ffmpeg stand-ins for the Fusion effects on this shot: (picture effects, finishing layers).

    They keep the 16-bit RGB chain (verified colour-neutral) so the preview and
    the preview-based QC see what Resolve will render. Subject glow/outline need
    the Fusion matte merge and appear only in the Resolve build.
    """
    picture: list[str] = []
    finishing: list[str] = []
    fps = edg.fps
    s0 = shot.timeline_start_frame
    clip_frames = edg.timeline_duration(shot)
    for k, effect in enumerate(e for e in edg.effects if e.shot_id == shot.id):
        a = max(0, effect.start_frame - s0) / fps
        b = max(a + 1 / fps, min(clip_frames, effect.end_frame - s0) / fps)
        win = f"between(t,{a:.4f},{b:.4f})"
        prog = f"min(1,max(0,(t-{a:.4f})/{b - a:.4f}))"
        p = effect.params
        if effect.template == "letterbox":
            bar = float(p.get("bar", 0.24))
            finishing.append(f"crop=iw:{int(height * (1 - bar)) // 2 * 2}:0:{int(height * bar / 2)},pad={width}:{height}:0:(oh-ih)/2:black")
        elif effect.template == "camera_shake":
            fade = f"(1-{prog})"
            picture.append(f"scale={int(width * 1.06) // 2 * 2}:{int(height * 1.06) // 2 * 2},crop={width}:{height}:'(iw-ow)/2+if({win},ow*0.012*{fade}*sin(t*53),0)':'(ih-oh)/2+if({win},oh*0.012*{fade}*cos(t*47),0)'")
        elif effect.template == "parallax":
            z0, z1 = float(p.get("zoom_0", 1.0)), float(p.get("zoom_1", 1.08))
            x0, x1 = float(p.get("pan_x0", 0.5)), float(p.get("pan_x1", 0.52))
            y0, y1 = float(p.get("pan_y0", 0.5)), float(p.get("pan_y1", 0.5))
            n0, n = int(a * fps), max(1, int((b - a) * fps))
            pr = f"min(1,max(0,(on-{n0})/{n}))"
            picture.append(
                f"zoompan=z='{z0}+({z1 - z0:.5f})*{pr}':x='max(0,min(iw-iw/zoom,iw*({x0}+({x1 - x0:.5f})*{pr})-iw/zoom/2))':y='max(0,min(ih-ih/zoom,ih*({y0}+({y1 - y0:.5f})*{pr})-ih/zoom/2))':d=1:s={width}x{height}:fps={fps:g}"
            )
        elif effect.template == "cinematic_bloom":
            thr = float(p.get("threshold", 0.6))
            sigma = float(p.get("radius", 25)) * width / 1920 / 2
            keep = f"'if(gt(val,maxval*{thr}),val,0)'"
            picture.append(f"split[fxa{k}][fxb{k}];[fxb{k}]lutrgb=r={keep}:g={keep}:b={keep},format=gbrp16le,gblur=sigma={sigma:.2f},format=rgb48le[fxg{k}];[fxa{k}][fxg{k}]blend=all_mode=screen:all_opacity={float(p.get('strength', 0.35)):.3f}:enable='{win}',format=rgb48le")
        elif effect.template == "speed_blur":
            radius = max(1, int(float(p.get("amount", 0.06)) * width / 4))
            picture.append(f"format=gbrp16le,dblur=angle={float(p.get('angle', 0.0)):.1f}:radius={radius}:enable='{win}',format=rgb48le")
        elif effect.template == "freeze_frame" and "freeze_at" in p:
            at = int(p["freeze_at"])
            flash = "'min(maxval,val*1.6)'"
            picture.append(f"loop=loop={max(1, clip_frames - at)}:size=1:start={at},lutrgb=r={flash}:g={flash}:b={flash}:enable='between(n,{at + 1},{at + 3})'")
    return picture, finishing


def _transition_filters(edg: EditDecisionGraph, shot: Shot, duration_s: float) -> list[str]:
    """Approximate stylised transitions on the incoming/outgoing halves."""
    out = []
    for t in edg.transitions:
        d = t.duration_frames / edg.fps / 2
        if t.to_shot == shot.id:
            if t.kind in {"flash", "dip_to_white"}:
                out.append(f"eq=brightness='0.6*max(0,1-t/{d:.3f})'")
            elif t.kind in {"dip_to_black", "cross_dissolve"}:
                out.append(f"fade=t=in:st=0:d={d:.3f}")
            elif t.kind == "zoom_punch":
                out.append(f"zoompan=z='if(lt(on,{int(d * edg.fps)}),1.35-0.35*on/{max(1, int(d * edg.fps))},1)':d=1:s={{W}}x{{H}}:fps={edg.fps:g}")
        if t.from_shot == shot.id:
            start = max(0.0, duration_s - d)
            if t.kind in {"flash", "dip_to_white"}:
                out.append(f"eq=brightness='0.6*max(0,(t-{start:.3f})/{d:.3f})'")
            elif t.kind in {"dip_to_black", "cross_dissolve"}:
                out.append(f"fade=t=out:st={start:.3f}:d={d:.3f}")
    return out


def render_preview(
    edg: EditDecisionGraph,
    out_path: Path,
    *,
    scale: float = 0.5,
    include_text: bool = True,
    include_audio: bool = True,
    loudness_lufs: float = -14.0,
    progress: Callable[[str, float], None] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Path:
    width = int(edg.width * scale) // 2 * 2
    height = int(edg.height * scale) // 2 * 2
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="vanta_preview_"))
    ffmpeg = _tool("ffmpeg")
    try:
        segments = []
        shots = edg.ordered_shots(1)
        cursor = 0
        for i, shot in enumerate(shots):
            if cancelled():
                raise RuntimeError("preview cancelled")
            if shot.timeline_start_frame > cursor:
                gap = work / f"gap_{i:03d}.mp4"
                run_tool([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={edg.fps:g}", "-frames:v", str(shot.timeline_start_frame - cursor), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", *REC709_TAGS, str(gap)])
                segments.append(gap)
            src = edg.source(shot.source_media_id)
            src_fps = src.fps or edg.fps
            start_s = shot.source_in_frame / src_fps
            dur_src_s = (shot.source_out_frame - shot.source_in_frame) / src_fps
            tl_frames = edg.timeline_duration(shot)
            grade = edg.grade_for(shot.id)
            vf = _shot_filter(edg, shot, width, height, grade.lut_path if grade and grade.lut_path and Path(grade.lut_path).is_file() else None)
            extra = [f.replace("{W}", str(width)).replace("{H}", str(height)) for f in _transition_filters(edg, shot, tl_frames / edg.fps)]
            if extra:
                vf += "," + ",".join(extra)
            finishing = _effect_filters(edg, shot, width, height)[1]
            if finishing:
                vf += ",format=rgb48le," + ",".join(finishing)
            vf += ",format=rgb48le,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
            seg = work / f"seg_{i:03d}.mp4"
            run_tool(
                [ffmpeg, "-y", "-v", "error", "-ss", f"{start_s:.4f}", "-t", f"{dur_src_s + 0.2:.4f}", "-i", src.path, "-vf", vf, "-frames:v", str(tl_frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", *REC709_TAGS, str(seg)],
                timeout=600,
            )
            segments.append(seg)
            cursor = shot.timeline_start_frame + tl_frames
            if progress:
                progress("shots", (i + 1) / max(1, len(shots)))
        concat_list = work / "concat.txt"
        concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments), encoding="utf-8")
        video = work / "video.mp4"
        run_tool([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(video)], timeout=600)
        composite = video
        if include_text and (edg.texts or edg.captions):
            composite = _composite_overlays(edg, video, work, width, height, progress)
        final_inputs = [ffmpeg, "-y", "-v", "error", "-i", str(composite)]
        if include_audio:
            audio = _mix_audio(edg, work, loudness_lufs)
            if audio:
                final_inputs += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        final_inputs += ["-c:v", "copy", "-movflags", "+faststart", str(out_path)]
        tmp_out = out_path.with_suffix(".tmp.mp4")
        final_inputs[-1] = str(tmp_out)
        run_tool(final_inputs, timeout=600)
        os.replace(tmp_out, out_path)
        if progress:
            progress("done", 1.0)
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _matte_for(edg: EditDecisionGraph, matte_id: str | None, frame_offset: int) -> np.ndarray | None:
    if not matte_id:
        return None
    matte = next((m for m in edg.mattes if m.id == matte_id), None)
    if not matte or matte.method == "pending":
        return None
    path = Path(matte.directory) / (matte.pattern % max(0, min(matte.frame_count - 1, frame_offset)))
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    return img[..., -1] if img.ndim == 3 else img


def _composite_overlays(edg: EditDecisionGraph, video: Path, work: Path, width: int, height: int, progress) -> Path:
    """Decode frames, draw titles/captions (and behind-subject text through mattes), re-encode."""
    ffmpeg = _tool("ffmpeg")
    out = work / "composite.mp4"
    cap = cv2.VideoCapture(str(video))
    import subprocess

    proc = subprocess.Popen(
        [ffmpeg, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", f"{edg.fps:g}", "-i", "-", "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", *REC709_TAGS, str(out)],
        stdin=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    frame_idx = 0
    total = edg.duration_frames
    shots = edg.ordered_shots(1)
    try:
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            rgb = correct_decoded(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), "bt709")  # our own Rec.709 segments
            base = Image.fromarray(rgb).convert("RGBA")
            for layer in edg.texts:
                if not layer.behind_subject:
                    continue
                txt = render_text_layer(layer, frame_idx, width, height, edg.fps)
                if txt is None:
                    continue
                shot = next((s for s in shots if s.timeline_start_frame <= frame_idx < edg.shot_end(s)), None)
                matte = _matte_for(edg, layer.matte_id, frame_idx - (shot.timeline_start_frame if shot else 0))
                composed = Image.alpha_composite(base, txt)
                if matte is not None:
                    m = cv2.resize(matte, (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255
                    arr = np.array(composed).astype(np.float32)
                    orig = np.array(base).astype(np.float32)
                    arr = arr * (1 - m[..., None]) + orig * m[..., None]
                    composed = Image.fromarray(arr.astype(np.uint8))
                base = composed
            overlay = render_overlay_frame(edg, frame_idx, width, height)
            if overlay is not None:
                base = Image.alpha_composite(base, overlay)
            proc.stdin.write(np.array(base.convert("RGB")).tobytes())  # type: ignore[union-attr]
            frame_idx += 1
            if progress and frame_idx % 20 == 0:
                progress("overlay", frame_idx / max(1, total))
    finally:
        cap.release()
        if proc.stdin:
            proc.stdin.close()
        proc.wait(timeout=600)
    if proc.returncode != 0:
        raise RuntimeError("overlay composite encode failed")
    return out


def _mix_audio(edg: EditDecisionGraph, work: Path, lufs: float) -> Path | None:
    ffmpeg = _tool("ffmpeg")
    inputs: list[str] = []
    filters: list[str] = []
    labels_music: list[str] = []
    labels_dialogue: list[str] = []
    duration = edg.duration_frames / edg.fps
    idx = 0
    if edg.music and not edg.music.guide_only:
        src = edg.source(edg.music.source_media_id)
        rate = src.fps or edg.fps
        start = edg.music.source_in_frame / rate
        inputs += ["-ss", f"{start:.4f}", "-t", f"{duration + 0.5:.4f}", "-i", src.path]
        fade = max(0.05, edg.music.fade_out_frames / edg.fps)
        filters.append(f"[{idx}:a]aresample=48000,volume={10 ** (edg.music.gain_db / 20):.4f},afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.3f}[m{idx}]")
        labels_music.append(f"[m{idx}]")
        idx += 1
    for shot in edg.ordered_shots(1):
        if not shot.audio_enabled:
            continue
        src = edg.source(shot.source_media_id)
        rate = src.fps or edg.fps
        start = shot.source_in_frame / rate
        dur = edg.timeline_duration(shot) / edg.fps
        delay_ms = int(shot.timeline_start_frame / edg.fps * 1000)
        inputs += ["-ss", f"{start:.4f}", "-t", f"{dur:.4f}", "-i", src.path]
        filters.append(f"[{idx}:a]aresample=48000,afade=t=in:d=0.01,afade=t=out:st={max(0.0, dur - 0.02):.3f}:d=0.02,adelay={delay_ms}|{delay_ms}[d{idx}]")
        labels_dialogue.append(f"[d{idx}]")
        idx += 1
    labels_sfx: list[str] = []
    for cue in edg.sfx:
        src = edg.source(cue.source_media_id)
        rate = src.fps or edg.fps
        start = cue.source_in_frame / rate
        dur = (cue.source_out_frame - cue.source_in_frame) / rate
        delay_ms = int(cue.timeline_frame / edg.fps * 1000)
        inputs += ["-ss", f"{start:.4f}", "-t", f"{dur:.4f}", "-i", src.path]
        filters.append(f"[{idx}:a]aresample=48000,volume={10 ** (cue.gain_db / 20):.4f},adelay={delay_ms}|{delay_ms}[s{idx}]")
        labels_sfx.append(f"[s{idx}]")
        idx += 1
    if not idx:
        return None
    graph = []
    if labels_dialogue:
        graph.append(f"{''.join(labels_dialogue)}amix=inputs={len(labels_dialogue)}:normalize=0[dia]")
    if labels_music and labels_dialogue and edg.music and edg.music.duck_under_dialogue_db < 0:
        graph.append("[dia]asplit=2[dia1][dia2]")
        graph.append(f"{labels_music[0]}[dia1]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=300[duck]")
        graph.append("[duck][dia2]amix=inputs=2:normalize=0[mix]")
    elif labels_music and labels_dialogue:
        graph.append(f"{labels_music[0]}[dia]amix=inputs=2:normalize=0[mix]")
    elif labels_music:
        graph.append(f"{labels_music[0]}anull[mix]")
    else:
        graph.append("[dia]anull[mix]")
    if labels_sfx:
        graph.append("[mix]" + "".join(labels_sfx) + f"amix=inputs={len(labels_sfx) + 1}:normalize=0[mix2]")
    else:
        graph.append("[mix]anull[mix2]")
    graph.append(f"[mix2]loudnorm=I={lufs}:TP=-1.0:LRA=11,aresample=48000,aformat=channel_layouts=stereo,atrim=0:{duration:.4f}[out]")
    out = work / "mix.m4a"
    run_tool([ffmpeg, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(filters + graph), "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(out)], timeout=600)
    return out


def contact_frames(video: Path, times_s: list[float]) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frames = []
    for t in times_s:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if ok:
            frames.append(read_rgb_fixed(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), video))
    cap.release()
    return frames
