"""Colour fidelity through every decode/encode path (OpenCV decodes all video as BT.601)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from vanta.analysis.media import (
    FrameReader,
    _tool,
    effective_matrix,
    grab_frame,
    is_full_range,
    make_proxy,
    probe,
    read_rgb_fixed,
)
from vanta.core.colormath import correct_decoded, matrix_correction, rgb_to_ycbcr_matrix
from vanta.core.schemas import EditDecisionGraph, Shot, SourceMedia, SpeedRamp

PATCHES = [(200, 40, 40), (40, 180, 60), (40, 60, 200), (220, 170, 130)]


def _clip(path: Path, w: int, h: int, *, matrix: str, tag: str | None, full: bool = False, frames: int = 24, fps: int = 24) -> Path:
    rgb = np.zeros((h, w, 3), np.uint8)
    for i, c in enumerate(PATCHES):
        rgb[:, i * w // 4 : (i + 1) * w // 4] = c
    raw = path.with_suffix(".rgb")
    raw.write_bytes(rgb.tobytes() * frames)
    rng = "pc" if full else "tv"
    pix = "yuvj420p" if full else "yuv420p"
    args = [_tool("ffmpeg"), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", str(raw), "-vf", f"scale=out_color_matrix={matrix}:out_range={rng},format={pix}", "-c:v", "libx264", "-crf", "4", "-pix_fmt", pix]
    if tag:
        args += ["-colorspace", tag, "-color_range", rng]
    subprocess.run([*args, str(path)], check=True)
    return path


def _err(rgb: np.ndarray) -> int:
    h, w = rgb.shape[:2]
    return max(max(abs(int(v) - c) for v, c in zip(rgb[h // 2, i * w // 4 + w // 8], patch, strict=True)) for i, patch in enumerate(PATCHES))


def test_matrix_correction_is_exact_and_invertible():
    rgb = np.random.default_rng(1).random((200, 3))
    for actual in ("bt709", "bt2020"):
        ycc = rgb @ rgb_to_ycbcr_matrix(actual).T
        as_601 = ycc @ np.linalg.inv(rgb_to_ycbcr_matrix("bt601")).T  # what OpenCV produces
        assert np.allclose(as_601 @ matrix_correction(actual).T, rgb, atol=1e-9)
    assert np.allclose(matrix_correction("bt601"), np.eye(3))
    grey = np.full((2, 2, 3), 128, np.uint8)
    assert np.array_equal(correct_decoded(grey, "bt709"), grey), "neutrals are untouched"


@pytest.mark.parametrize(
    ("name", "size", "matrix", "tag", "full"),
    [
        ("hd709_tagged", (1280, 720), "bt709", "bt709", False),
        ("hd709_untagged", (1280, 720), "bt709", None, False),
        ("sd601", (640, 480), "bt601", "smpte170m", False),
        ("hd709_full_range", (1280, 720), "bt709", "bt709", True),
        ("hdr2020", (1920, 1080), "bt2020", "bt2020nc", False),
    ],
)
def test_every_decode_path_returns_true_colour(tmp_path, name, size, matrix, tag, full):
    src = _clip(tmp_path / f"{name}.mp4", *size, matrix=matrix, tag=tag, full=full)
    info = probe(src)
    assert effective_matrix(info) == matrix and is_full_range(info) == full
    proxy = make_proxy(src, tmp_path / f"{name}_proxy.mp4", long_edge=max(size), matrix=effective_matrix(info), full_range=is_full_range(info))
    with FrameReader(proxy) as reader:
        assert _err(reader.read_index(3)) <= 5, "analysis proxy"
    assert _err(grab_frame(src, 0.2)) <= 5, "still grab"
    cap = cv2.VideoCapture(str(src))
    ok, bgr = cap.read()
    cap.release()
    assert ok and _err(read_rgb_fixed(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), src)) <= 5, "direct OpenCV read, corrected"


def _edg(src: Path, frames: int, **shot) -> EditDecisionGraph:
    info = probe(src)
    return EditDecisionGraph(
        project_id="prj_c",
        fps=24,
        width=info.width,
        height=info.height,
        timeline_name="VANTA_C_ASSEMBLY_V1",
        sources=[SourceMedia(id="src0", path=str(src), fps=24, duration_frames=frames)],
        shots=[Shot(id="shot_000", source_media_id="src0", source_in_frame=0, timeline_start_frame=0, confidence=0.9, **shot)],
    )


def test_preview_render_keeps_colour(tmp_path):
    from vanta.render.preview import contact_frames, render_preview

    src = _clip(tmp_path / "hd.mp4", 1280, 720, matrix="bt709", tag=None)
    edg = _edg(src, 24, source_out_frame=20)
    out = render_preview(edg, tmp_path / "preview.mp4", scale=1.0, include_audio=False, include_text=False)
    assert probe(out).color_space == "bt709", "previews are tagged Rec.709"
    assert _err(contact_frames(out, [0.3])[0]) <= 6


def test_speed_ramp_prerender_is_16bit_accurate_and_frame_exact(tmp_path):
    from vanta.render.prerender import render_speed_ramp

    src = _clip(tmp_path / "hd.mp4", 1280, 720, matrix="bt709", tag="bt709", frames=48)
    edg = _edg(src, 48, source_out_frame=22, speed_percent=round(22 / 30 * 100, 4), timeline_duration_frames=30, speed_ramp=SpeedRamp(curve=[(0.0, 100.0), (0.5, 50.0), (1.0, 100.0)], purpose="beat_impact", route="prerender"))
    out = render_speed_ramp(edg, edg.shots[0], tmp_path / "ramp.mov")
    info = probe(out)
    assert info.frame_count == 30 and info.pix_fmt == "yuv422p10le" and info.color_space == "bt709"
    assert _err(grab_frame(out, 0.5)) <= 5
