"""Per-shot camera/subject motion summaries and in-shot speed effects (OpenCV flow based)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from vanta.analysis.signals import FrameSignals


@dataclass
class MotionSummary:
    camera_move: str  # static | pan_left | pan_right | tilt_up | tilt_down | push_in | pull_out | roll | handheld | tracking
    intensity: float  # 0..1
    pan_per_s: float
    tilt_per_s: float
    zoom_per_s: float
    roll_per_s: float
    shake: float
    subject_motion: float
    stability: float  # 0..1 (1 = locked off)
    speed_events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _smooth(values: np.ndarray, k: int) -> np.ndarray:
    if len(values) < 3 or k <= 1:
        return values
    k = min(k, len(values))
    kernel = np.ones(k) / k
    return np.convolve(values, kernel, mode="same")


def summarise_motion(sig: FrameSignals, start: int, end: int) -> MotionSummary:
    fps = sig.fps
    lo = min(end - 1, start + 2)  # skip flow across the cut
    sl = slice(lo, end)
    n = max(1, end - lo)
    pan = sig.pan[sl]
    tilt = sig.tilt[sl]
    zoom = sig.zoom[sl]
    roll = sig.roll[sl]
    pan_s = float(np.sum(pan) / n * fps)
    tilt_s = float(np.sum(tilt) / n * fps)
    zoom_s = float(np.sum(zoom) / n * fps)
    roll_s = float(np.sum(roll) / n * fps)
    k = max(3, int(fps * 0.3))
    shake = float(np.std(pan - _smooth(pan, k)) + np.std(tilt - _smooth(tilt, k))) * fps
    residual = float(np.mean(sig.residual[sl])) if n else 0.0
    flow = float(np.mean(sig.flow_mag[sl])) if n else 0.0
    intensity = float(1 - np.exp(-flow * fps / 0.35))
    stability = float(np.exp(-shake / 0.04))
    move = "static"
    if shake > 0.06:
        move = "handheld"
    if abs(zoom_s) > 0.06 and abs(zoom_s) >= max(abs(pan_s), abs(tilt_s)):
        move = "push_in" if zoom_s > 0 else "pull_out"
    elif abs(pan_s) > 0.05 and abs(pan_s) >= abs(tilt_s):
        # flow moves content opposite to the camera
        move = "pan_left" if pan_s > 0 else "pan_right"
        if residual < 0.002 and intensity > 0.3:
            move = "tracking"
    elif abs(tilt_s) > 0.05:
        move = "tilt_up" if tilt_s > 0 else "tilt_down"
    elif abs(roll_s) > 0.15:
        move = "roll"
    return MotionSummary(
        camera_move=move,
        intensity=round(intensity, 4),
        pan_per_s=round(pan_s, 5),
        tilt_per_s=round(tilt_s, 5),
        zoom_per_s=round(zoom_s, 5),
        roll_per_s=round(roll_s, 5),
        shake=round(shake, 5),
        subject_motion=round(residual * fps, 5),
        stability=round(stability, 4),
        speed_events=detect_speed_events(sig, start, end),
    )


def detect_speed_events(sig: FrameSignals, start: int, end: int) -> list[dict]:
    """Freeze frames, speed ramps and digital punch-ins inside one shot."""
    fps = sig.fps
    events: list[dict] = []
    lo = start + 2
    if end - lo < int(0.6 * fps):
        return events
    diff = sig.pixel_diff[lo:end]
    flow = _smooth(sig.flow_mag[lo:end], max(3, int(fps * 0.2)))
    moving = np.median(flow) > 0.002 or np.max(flow) > 0.006
    # freeze frame: near-zero difference sustained >= 0.4 s inside a moving shot
    frozen = diff < 0.0015
    run = 0
    for i, value in enumerate(frozen):
        run = run + 1 if value else 0
        if run == int(0.4 * fps) and moving:
            events.append(
                {
                    "type": "freeze_frame",
                    "start_s": (lo + i - run + 1) / fps,
                    "confidence": 0.7,
                }
            )
    # speed ramp: smooth flow drops/rises by > 3x for >= 0.3 s while direction persists
    if moving and len(flow) > int(fps):
        hi_level = np.percentile(flow, 85)
        lo_level = np.percentile(flow, 15)
        if lo_level > 0 and hi_level / max(lo_level, 1e-5) > 3.0:
            slow = flow < (lo_level + hi_level) / 4
            if slow.sum() >= int(0.3 * fps) and (~slow).sum() >= int(0.3 * fps):
                pan_sign = np.sign(np.sum(sig.pan[lo:end]))
                consistent = abs(np.mean(np.sign(sig.pan[lo:end]) == pan_sign)) > 0.6
                pivot = int(np.argmax(slow)) + lo
                events.append(
                    {
                        "type": "speed_ramp",
                        "start_s": pivot / fps,
                        "ratio": float(hi_level / max(lo_level, 1e-5)),
                        "confidence": 0.55 if consistent else 0.4,
                    }
                )
    # digital punch-in: > 6 % scale change within 3 frames
    zoom = sig.zoom[lo:end]
    if len(zoom) >= 3:
        burst = np.convolve(zoom, np.ones(3), mode="valid")
        idx = int(np.argmax(np.abs(burst)))
        if abs(burst[idx]) > 0.06:
            events.append(
                {
                    "type": "punch_in" if burst[idx] > 0 else "punch_out",
                    "start_s": (lo + idx) / fps,
                    "scale_change": float(np.exp(burst[idx]) - 1),
                    "confidence": 0.6,
                }
            )
    return events
