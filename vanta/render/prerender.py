"""Pre-rendered media for gate fallbacks: speed ramps and the alpha overlay track.

Speed ramps (manual 12.4, G13 fallback) are rendered with DIS optical-flow
frame interpolation for slow sections, so a 100->45->100 ramp stays smooth
instead of stuttering. Output is 10-bit ProRes 422 HQ from a 16-bit pipeline
with the source's colour tags (edit-friendly, no generation loss). The overlay
track (titles/captions when Fusion is not gate-verified) is Rec.709 ProRes 4444
with alpha so it stays a separate, movable layer in Resolve.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from vanta.analysis.media import REC709_TAGS, _tool, effective_matrix, is_full_range, probe
from vanta.core.schemas import EditDecisionGraph, Shot
from vanta.render.overlay import write_overlay_sequence


def _speed_at(curve: list[tuple[float, float]], u: float) -> float:
    xs = [p[0] for p in curve]
    ys = [p[1] for p in curve]
    return float(np.interp(u, xs, ys)) / 100.0


def ramp_time_map(curve: list[tuple[float, float]], out_frames: int, src_frames_available: int) -> np.ndarray:
    """Source frame position (float) for each output frame, scaled to use the source span exactly."""
    speeds = np.asarray([_speed_at(curve, (i + 0.5) / out_frames) for i in range(out_frames)])
    positions = np.concatenate([[0.0], np.cumsum(speeds)[:-1]])
    total = positions[-1] + speeds[-1]
    scale = (src_frames_available - 1) / max(total, 1e-6)
    return positions * scale


def _interpolate(a: np.ndarray, b: np.ndarray, t: float, dis) -> np.ndarray:
    """Optical-flow in-between of two 16-bit RGB frames (flow on 8-bit grey, warp at full depth)."""
    if t < 0.02:
        return a
    if t > 0.98:
        return b
    ga = cv2.cvtColor((a >> 8).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor((b >> 8).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    flow_ab = dis.calc(ga, gb, None)
    flow_ba = dis.calc(gb, ga, None)
    h, w = ga.shape
    grid = np.dstack(np.meshgrid(np.arange(w), np.arange(h))).astype(np.float32)
    warp_a = cv2.remap(a, grid + flow_ab * t, None, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    warp_b = cv2.remap(b, grid + flow_ba * (1 - t), None, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return cv2.addWeighted(warp_a, 1 - t, warp_b, t, 0)


def _source_tags(info) -> list[str]:
    """Carry the source's colour tags so Resolve interprets the pre-render exactly like the original."""
    tags = []
    for flag, value in (("-color_primaries", info.color_primaries), ("-color_trc", info.color_transfer)):
        if value and value != "unknown":
            tags += [flag, value]
    return tags


def needs_conform(shot: Shot) -> bool:
    """Shots VANTA pre-renders: speed ramps and subject-follow reframes."""
    return bool(shot.speed_ramp) or bool(shot.reframe and shot.reframe.dynamic)


def render_speed_ramp(edg: EditDecisionGraph, shot: Shot, out_path: Path) -> Path:
    """Optical-flow speed ramp and/or subject-follow reframe at full source quality.

    Frames are decoded by ffmpeg with frame-accurate seeking into 16-bit RGB
    using the source's real Y'CbCr matrix and range, streamed (two frames in
    memory, so 4K log footage fits the RAM budget), and written as ProRes 422 HQ
    10-bit with the source matrix/primaries/transfer so the grade LUT (camera
    decode + look) applies exactly as it would to the original clip.
    """
    if not needs_conform(shot):
        raise ValueError(f"{shot.id} has no speed ramp or subject-follow")
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path
    src = edg.source(shot.source_media_id)
    info = probe(src.path)
    src_fps = src.fps or info.fps or edg.fps
    matrix, full = effective_matrix(info), is_full_range(info)
    w, h = info.display_size
    w, h = w // 2 * 2, h // 2 * 2
    follow = shot.reframe if shot.reframe and shot.reframe.dynamic else None
    if follow:
        # decode straight to the fill size of the timeline (plus reframe zoom); the follow crops from it
        fill = max(edg.width / w, edg.height / h) * follow.zoom
        w, h = max(edg.width, int(np.ceil(w * fill / 2)) * 2), max(edg.height, int(np.ceil(h * fill / 2)) * 2)
    out_frames = edg.timeline_duration(shot)
    n_src = max(2, shot.source_out_frame - shot.source_in_frame + (1 if shot.speed_ramp else 0))
    if shot.speed_ramp:
        mapping = ramp_time_map(shot.speed_ramp.curve, out_frames, n_src)
    else:  # plain conform: nearest source frame, exactly like an unramped clip
        mapping = np.floor(np.arange(out_frames) * (n_src / max(1, out_frames)) + 1e-6)
    ow, oh = (edg.width, edg.height) if follow else (w, h)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    decoder = subprocess.Popen(
        [_tool("ffmpeg"), "-v", "error", "-ss", f"{shot.source_in_frame / src_fps:.6f}", "-i", src.path, "-frames:v", str(n_src), "-an",
         "-vf", f"scale={w}:{h}:in_color_matrix={matrix}:in_range={'pc' if full else 'tv'},format=rgb48le", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"],
        stdout=subprocess.PIPE,
        creationflags=flags,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.mov")
    encoder = subprocess.Popen(
        [_tool("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{ow}x{oh}", "-r", f"{edg.fps:g}", "-i", "-",
         "-vf", f"scale=out_color_matrix={matrix}:out_range=tv,format=yuv422p10le", "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
         "-colorspace", {"bt709": "bt709", "bt601": "smpte170m", "bt2020": "bt2020nc"}[matrix], "-color_range", "tv", *_source_tags(info), str(tmp)],
        stdin=subprocess.PIPE,
        creationflags=flags,
    )
    size = w * h * 3 * 2

    def frames():
        while True:
            buf = decoder.stdout.read(size)  # type: ignore[union-attr]
            if len(buf) < size:
                return
            yield np.frombuffer(buf, dtype=np.uint16).reshape(h, w, 3)

    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    stream = frames()
    window: list[np.ndarray] = []  # frames [base, base + 1]
    base = 0
    try:
        for pos in mapping:
            i = int(np.floor(pos))
            while base + len(window) <= i + 1:
                nxt = next(stream, None)
                if nxt is None:
                    break
                window.append(nxt)
                if len(window) > 2:
                    window.pop(0)
                    base += 1
            if not window:
                raise RuntimeError(f"could not decode source frames for {shot.id}")
            a = window[min(max(0, i - base), len(window) - 1)]
            b = window[min(max(0, i - base) + 1, len(window) - 1)]
            frame = _interpolate(a, b, float(pos - i), dis) if shot.speed_ramp else a
            if follow:  # the track was measured in source time: key the crop on the source position
                frame = _follow_crop(frame, follow, float(pos) / max(1, n_src - 1), ow, oh)
            encoder.stdin.write(np.ascontiguousarray(frame).tobytes())  # type: ignore[union-attr]
    finally:
        if encoder.stdin:
            encoder.stdin.close()
        encoder.wait(timeout=900)
        decoder.kill()
        decoder.wait(timeout=30)
    if encoder.returncode != 0:
        raise RuntimeError(f"speed ramp encode failed for {shot.id}")
    tmp.replace(out_path)
    return out_path


def _follow_crop(frame: np.ndarray, reframe, u: float, ow: int, oh: int) -> np.ndarray:
    """Sub-pixel crop of the fill-scaled frame at the keyed pan/tilt (same sign convention as Resolve)."""
    from vanta.solvers.reframe import value_at

    h, w = frame.shape[:2]
    pan, tilt = value_at(reframe.keys, u, 1), value_at(reframe.keys, u, 2)
    x0 = float(np.clip((w - ow) / 2 - pan * ow, 0, w - ow))
    y0 = float(np.clip((h - oh) / 2 + tilt * oh, 0, h - oh))
    m = np.float32([[1, 0, -x0], [0, 1, -y0]])
    return cv2.warpAffine(frame, m, (ow, oh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def render_overlay_movie(edg: EditDecisionGraph, out_path: Path, *, progress=None) -> Path | None:
    """ProRes 4444 + alpha overlay of titles/captions at full timeline resolution."""
    if not edg.texts and not edg.captions:
        return None
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path
    seq_dir = out_path.with_suffix("")
    pattern, total = write_overlay_sequence(edg, seq_dir, edg.width, edg.height, progress=progress)
    if total == 0:
        return None
    tmp = out_path.with_suffix(".tmp.mov")
    subprocess.run(
        [_tool("ffmpeg"), "-y", "-v", "error", "-framerate", f"{edg.fps:g}", "-i", str(pattern), "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuva444p10le", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", *REC709_TAGS, str(tmp)],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tmp.replace(out_path)
    for png in seq_dir.glob("overlay_*.png"):
        png.unlink(missing_ok=True)
    try:
        seq_dir.rmdir()
    except OSError:
        pass
    return out_path
