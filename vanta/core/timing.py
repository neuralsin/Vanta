"""Deterministic frame arithmetic shared by EDG validation and compilers.

The first Resolve slice deliberately refuses implicit mixed-frame-rate conversion.
Rounding source-frame boundaries is a content edit, so it must be represented by a
future, gate-tested retime solver rather than happen as a compiler side effect.
"""

from __future__ import annotations

from fractions import Fraction


class TimingError(ValueError):
    """Raised when an EDG cannot be represented without an implicit timing change."""


def fps_fraction(value: float) -> Fraction:
    """Convert common video rates to their exact rational representation."""
    if value <= 0:
        raise TimingError("frame rate must be positive")
    for nominal, exact in (
        (23.976, Fraction(24_000, 1_001)),
        (29.97, Fraction(30_000, 1_001)),
        (59.94, Fraction(60_000, 1_001)),
    ):
        if abs(value - nominal) < 0.002:
            return exact
    return Fraction(str(value)).limit_denominator(100_000)


def frame_time(frames: int, fps: float | Fraction) -> Fraction:
    if frames < 0:
        raise TimingError("frame count must not be negative")
    rate = fps if isinstance(fps, Fraction) else fps_fraction(fps)
    return Fraction(frames * rate.denominator, rate.numerator)


def source_frames_to_timeline_frames(
    source_frames: int,
    source_fps: float,
    timeline_fps: float,
) -> int:
    """Return an exact timeline-frame count or fail instead of silently rounding."""
    duration = frame_time(source_frames, source_fps)
    timeline_rate = fps_fraction(timeline_fps)
    timeline_frames = duration * timeline_rate
    if timeline_frames.denominator != 1:
        raise TimingError(
            "mixed frame rates need an explicit, gate-tested retime decision; "
            "the Phase 1 assembler will not round source-frame boundaries"
        )
    return timeline_frames.numerator


def frame_time_string(frames: int, fps: float | Fraction) -> str:
    value = frame_time(frames, fps)
    return f"{value.numerator}/{value.denominator}s"
