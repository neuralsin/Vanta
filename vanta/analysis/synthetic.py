"""Deterministic synthetic media for golden tests, demos and the calibration set.

Manual 8.6: "apply 10 known looks to your own footage, re-encode reel-style, and
confirm the estimators recover them". :func:`apply_known_look` and
:func:`make_calibration_set` implement that procedure on any source clip.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from vanta.analysis.media import _tool, run_tool

_FONT = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf"


def _ffmpeg_font(path: Path) -> str:
    # drawtext needs the drive colon escaped on Windows
    return str(path).replace("\\", "/").replace(":", "\\:")


def make_test_clip(
    output: str | Path,
    *,
    duration_s: float = 4.0,
    size: tuple[int, int] = (1080, 1920),
    fps: int = 30,
    pattern: str = "testsrc2",
    tint: tuple[float, float, float] | None = None,
    text: str | None = None,
    pan_px_per_s: float = 0.0,
    with_audio_hz: float | None = None,
) -> Path:
    """Generate a clip from an FFmpeg lavfi source with optional tint, motion and text."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    src_w = width + int(abs(pan_px_per_s) * duration_s) + 2
    source = f"{pattern}=size={src_w}x{height}:rate={fps}"
    if pattern.startswith("color="):
        source = f"{pattern}:size={src_w}x{height}:rate={fps}"
    filters = []
    if pan_px_per_s:
        filters.append(f"crop={width}:{height}:x='min(t*{abs(pan_px_per_s)},{src_w - width})':y=0")
    else:
        filters.append(f"crop={width}:{height}:0:0")
    if tint:
        r, g, b = tint
        filters.append(f"colorchannelmixer=rr={r}:gg={g}:bb={b}")
    if text:
        filters.append(
            f"drawtext=fontfile='{_ffmpeg_font(_FONT)}':text='{text}':fontcolor=white:"
            f"fontsize={max(24, height // 14)}:x=(w-text_w)/2:y=h*0.38:borderw=4:bordercolor=black"
        )
    args = [_tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", source]
    if with_audio_hz:
        args += ["-f", "lavfi", "-i", f"sine=frequency={with_audio_hz}:duration={duration_s}"]
    args += ["-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    args += ["-pix_fmt", "yuv420p", "-t", f"{duration_s}"]
    if with_audio_hz:
        args += ["-c:a", "aac"]
    args.append(str(output))
    run_tool(args, timeout=300)
    return output


def make_click_track(
    output: str | Path,
    *,
    bpm: float = 120.0,
    duration_s: float = 20.0,
    sample_rate: int = 44100,
    beats_per_bar: int = 4,
    drop_at_s: float | None = None,
    offset_s: float = 0.0,
) -> Path:
    """Write a drum-like click track with accented downbeats and an optional energy drop.

    The beat grid is known exactly, so beat/downbeat estimators can be tested
    against the manual's +/-40 ms acceptance criterion.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    n = int(duration_s * sample_rate)
    signal = np.zeros(n, dtype=np.float64)
    rng = np.random.default_rng(7)
    period = 60.0 / bpm
    t = offset_s
    beat = 0
    while t < duration_s - 0.05:
        start = int(t * sample_rate)
        length = int(0.09 * sample_rate)
        env = np.exp(-np.linspace(0, 9, length))
        is_down = beat % beats_per_bar == 0
        after_drop = drop_at_s is not None and t >= drop_at_s
        freq = 55.0 if is_down else 180.0
        tone = np.sin(2 * np.pi * freq * np.arange(length) / sample_rate)
        noise = rng.normal(0, 1, length) * (0.25 if is_down else 0.45)
        amp = (1.0 if is_down else 0.55) * (1.0 if after_drop or drop_at_s is None else 0.35)
        end = min(n, start + length)
        signal[start:end] += (amp * env * (tone + noise))[: end - start]
        t += period
        beat += 1
    # sustained pad so loudness/sections are measurable
    pad_t = np.arange(n) / sample_rate
    pad = 0.08 * np.sin(2 * np.pi * 220 * pad_t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.05 * pad_t))
    if drop_at_s is not None:
        pad *= np.where(pad_t >= drop_at_s, 2.2, 0.6)
    signal += pad
    signal /= max(1e-9, np.abs(signal).max()) / 0.89
    sf.write(str(output), signal.astype(np.float32), sample_rate, subtype="PCM_16")
    return output


def concat_clips(clips: list[Path], output: str | Path, *, fps: int = 30) -> Path:
    """Hard-cut concatenation (re-encoded) used to build synthetic reference reels."""
    output = Path(output)
    args = [_tool("ffmpeg"), "-y", "-v", "error"]
    for clip in clips:
        args += ["-i", str(clip)]
    inputs = "".join(f"[{i}:v]" for i in range(len(clips)))
    args += [
        "-filter_complex",
        f"{inputs}concat=n={len(clips)}:v=1:a=0,fps={fps}[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    run_tool(args, timeout=600)
    return output


def mux_audio(video: Path, audio: Path, output: str | Path) -> Path:
    output = Path(output)
    run_tool(
        [
            _tool("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        timeout=300,
    )
    return output


# --------------------------------------------------------------------------- looks
@dataclass(frozen=True)
class KnownLook:
    """A parametric look with ground-truth parameters for calibration."""

    name: str
    exposure: float = 0.0  # stops
    contrast: float = 1.0
    saturation: float = 1.0
    shadow_tint: tuple[float, float, float] = (0.0, 0.0, 0.0)
    highlight_tint: tuple[float, float, float] = (0.0, 0.0, 0.0)
    lift: float = 0.0

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        from vanta.solvers.grade_math import LookParams, apply_look

        params = LookParams(
            exposure=self.exposure,
            contrast=self.contrast,
            saturation=self.saturation,
            shadow_tint=self.shadow_tint,
            highlight_tint=self.highlight_tint,
            lift=self.lift,
        )
        return apply_look(rgb, params)


CALIBRATION_LOOKS: tuple[KnownLook, ...] = (
    KnownLook("neutral"),
    KnownLook("teal_orange", contrast=1.15, shadow_tint=(-0.03, 0.0, 0.04), highlight_tint=(0.04, 0.01, -0.03)),
    KnownLook("night_cyan", exposure=-0.6, contrast=1.2, saturation=0.85, shadow_tint=(-0.04, 0.01, 0.05)),
    KnownLook("warm_film", contrast=0.92, lift=0.04, highlight_tint=(0.04, 0.02, -0.02)),
    KnownLook("bleach_bypass", contrast=1.3, saturation=0.55),
    KnownLook("vivid", saturation=1.35, contrast=1.08),
    KnownLook("matte_fade", contrast=0.85, lift=0.07, saturation=0.9),
    KnownLook("crushed_cool", contrast=1.25, exposure=-0.3, shadow_tint=(-0.02, 0.0, 0.03)),
    KnownLook("golden_hour", exposure=0.2, saturation=1.15, highlight_tint=(0.05, 0.025, -0.04)),
    KnownLook("mono_green", saturation=0.7, shadow_tint=(-0.01, 0.03, -0.01), highlight_tint=(0.0, 0.02, 0.0)),
)


def apply_known_look(
    source: str | Path,
    output: str | Path,
    look: KnownLook,
    *,
    reel_encode: bool = True,
    size: int = 33,
) -> Path:
    """Render ``look`` onto ``source`` through a generated 3D LUT, then re-encode reel-style."""
    from vanta.solvers.lut import write_cube

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    lut_path = output.with_suffix(".cube")
    write_cube(lut_path, look.apply, size=size, title=look.name)
    lut_arg = str(lut_path).replace("\\", "/").replace(":", "\\:")
    args = [_tool("ffmpeg"), "-y", "-v", "error", "-i", str(source), "-vf", f"lut3d='{lut_arg}'"]
    if reel_encode:
        # Instagram-like: 1080 wide, 30 fps, aggressive H.264
        args += ["-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "27", "-maxrate", "3.5M"]
        args += ["-bufsize", "7M"]
    else:
        args += ["-c:v", "libx264", "-crf", "16"]
    args += ["-pix_fmt", "yuv420p", "-an", str(output)]
    run_tool(args, timeout=900)
    return output


def make_calibration_set(
    source: str | Path, out_dir: str | Path, looks: tuple[KnownLook, ...] = CALIBRATION_LOOKS
) -> list[tuple[KnownLook, Path]]:
    out_dir = Path(out_dir)
    return [
        (look, apply_known_look(source, out_dir / f"calib_{look.name}.mp4", look)) for look in looks
    ]


LookFn = Callable[[np.ndarray], np.ndarray]
