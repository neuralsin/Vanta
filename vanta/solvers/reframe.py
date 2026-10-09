"""Subject-follow reframing for aspect conversion (landscape camera footage -> 9:16).

The subject is located on sampled frames (largest/most persistent face, else
the centroid of local motion, else held), then the path is smoothed the way a
camera operator would follow: a dead zone so the frame does not chase small
movements, eased motion, and a capped pan speed. Positions convert to the
timeline pan/tilt convention of :class:`vanta.core.schemas.Reframe` (fraction of
timeline width/height the image moves; positive pan = image moves right).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from vanta.analysis.frame_vision import detect_faces

DEAD_ZONE = 0.12  # of the crop window: subject may drift this far before the frame follows
MAX_SPEED = 0.45  # timeline widths per second for discretionary moves (containment may exceed it)
MIN_TRAVEL = 0.03  # below this pan range a static reframe is used


@dataclass
class SubjectTrack:
    times_s: list[float]  # clip-relative seconds
    xs: list[float]  # normalised source coordinates (0..1)
    ys: list[float]
    method: list[str]


def locate_subjects(frames: list[np.ndarray], times_s: list[float], start: tuple[float, float] = (0.5, 0.5)) -> SubjectTrack:
    """Subject centre per sampled frame: faces first, then foreground saliency, else hold.

    Foreground = difference from the temporal median of the samples (the
    background of a locked-off or slowly moving shot), so the centroid sits on
    the subject itself rather than between its old and new positions.
    """
    small = []
    for frame in frames:
        h, w = frame.shape[:2]
        scale = 192 / max(h, w)
        grey = cv2.cvtColor(cv2.resize(frame, (max(8, int(w * scale)), max(8, int(h * scale))), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
        small.append(cv2.GaussianBlur(grey, (0, 0), 1.5).astype(np.float32))
    background = np.median(np.stack(small), axis=0) if len(small) >= 3 else None
    xs: list[float | None] = []
    ys: list[float | None] = []
    methods = []
    px, py = start
    for frame, grey in zip(frames, small, strict=True):
        faces = detect_faces(frame)
        if faces:
            # prefer large faces near the previous position (continuity beats a passer-by)
            best = max(faces, key=lambda f: f["w"] * f["h"] * (1.5 - min(1.0, abs(f["x"] + f["w"] / 2 - px) * 2)))
            px, py = best["x"] + best["w"] / 2, best["y"] + best["h"] * 0.6
            xs.append(px)
            ys.append(py)
            methods.append("face")
            continue
        if background is not None:
            fg = np.abs(grey - background)
            fg = np.clip(fg - np.percentile(fg, 75) - 4.0, 0, None)
            total = float(fg.sum())
            if total > fg.size * 0.05:
                h, w = fg.shape
                px = float((fg.sum(axis=0) * (np.arange(w) + 0.5)).sum() / total) / w
                py = float((fg.sum(axis=1) * (np.arange(h) + 0.5)).sum() / total) / h
                xs.append(px)
                ys.append(py)
                methods.append("foreground")
                continue
        xs.append(None)
        ys.append(None)
        methods.append("hold")
    # fill holds from the nearest located sample (leading holds take the first location)
    found = [i for i, v in enumerate(xs) if v is not None]
    if not found:
        return SubjectTrack(list(times_s), [start[0]] * len(frames), [start[1]] * len(frames), methods)
    fx = [xs[min(found, key=lambda j: abs(j - i))] if v is None else v for i, v in enumerate(xs)]
    fy = [ys[min(found, key=lambda j: abs(j - i))] if v is None else v for i, v in enumerate(ys)]
    return SubjectTrack(list(times_s), [float(np.clip(v, 0, 1)) for v in fx], [float(np.clip(v, 0, 1)) for v in fy], methods)


def _median3(values: np.ndarray) -> np.ndarray:
    if len(values) < 3:
        return values
    padded = np.concatenate([[values[0]], values, [values[-1]]])
    return np.median(np.stack([padded[:-2], padded[1:-1], padded[2:]]), axis=0)


def operator_path(positions: list[float], times_s: list[float], window: float, *, dead_zone: float = DEAD_ZONE, max_speed: float = MAX_SPEED, timeline_span: float = 1.0) -> np.ndarray:
    """Smoothed camera-centre path (normalised source coords) that follows like an operator.

    ``window`` is the crop window size as a fraction of the source axis; the
    dead zone and speed are expressed relative to it / the timeline span.
    """
    x = _median3(np.asarray(positions, dtype=np.float64))
    half = window / 2
    centre = float(np.clip(x[0], half, 1 - half))
    out = [centre]
    for i in range(1, len(x)):
        dt = max(1e-3, times_s[i] - times_s[i - 1])
        offset = x[i] - centre
        zone = dead_zone * window
        if abs(offset) > zone:
            step = (offset - np.sign(offset) * zone) * 0.65
            limit = max_speed * timeline_span * window * dt
            centre += float(np.clip(step, -limit, limit))
        centre = _contain(centre, x[i], half)
        out.append(centre)
    path = np.asarray(out)
    if len(path) >= 5:  # ease: light gaussian smoothing, endpoints kept
        kernel = np.exp(-0.5 * (np.arange(-3, 4) / 1.4) ** 2)
        kernel /= kernel.sum()
        padded = np.pad(path, 3, mode="edge")
        path = np.convolve(padded, kernel, mode="valid")
    # never lose the subject: containment overrides the speed cap and the easing
    return np.asarray([_contain(c, xi, half) for c, xi in zip(path, x, strict=True)])


def _contain(centre: float, subject: float, half: float, keep: float = 0.8) -> float:
    """Shift the window just enough to keep the subject inside its central ``keep`` share."""
    reach = half * keep
    centre = min(max(centre, subject - reach), subject + reach)
    return float(np.clip(centre, half, 1 - half))


def follow_keys(track: SubjectTrack, source_aspect: float, width: int, height: int, duration_s: float) -> tuple[list[tuple[float, float, float]], float, float]:
    """Keys ``(u, pan, tilt)`` (u = normalised clip time) plus static fallback (pan, tilt).

    Returns an empty key list when a static reframe is enough.
    """
    timeline_aspect = width / max(1, height)
    times = track.times_s
    if source_aspect > timeline_aspect:  # crop horizontally
        scaled_w = source_aspect * height
        window = width / scaled_w
        path = operator_path(track.xs, times, window, timeline_span=1.0)
        pans = (0.5 - path) * scaled_w / width
        limit = (scaled_w - width) / (2 * width)
        pans = np.clip(pans, -limit, limit)
        static = float(np.median(pans))
        if float(pans.max() - pans.min()) < MIN_TRAVEL:
            return [], round(static, 4), 0.0
        return [(round(t / max(duration_s, 1e-6), 4), round(float(p), 4), 0.0) for t, p in zip(times, pans, strict=True)], round(static, 4), 0.0
    scaled_h = width / source_aspect
    window = height / scaled_h
    path = operator_path(track.ys, times, window, timeline_span=1.0)
    tilts = np.clip((path - 0.5) * scaled_h / height, -(scaled_h - height) / (2 * height), (scaled_h - height) / (2 * height))
    static = float(np.median(tilts))
    if float(tilts.max() - tilts.min()) < MIN_TRAVEL:
        return [], 0.0, round(static, 4)
    return [(round(t / max(duration_s, 1e-6), 4), 0.0, round(float(v), 4)) for t, v in zip(times, tilts, strict=True)], 0.0, round(static, 4)


def value_at(keys: list[tuple[float, float, float]], u: float, axis: int) -> float:
    """Piecewise-linear key value (axis 1 = pan, 2 = tilt) at normalised time ``u``."""
    us = [k[0] for k in keys]
    return float(np.interp(u, us, [k[axis] for k in keys]))


def ffmpeg_expr(keys: list[tuple[float, float, float]], axis: int, duration_s: float, var: str = "t") -> str:
    """Nested piecewise-linear ffmpeg expression of the keyed value over clip seconds."""
    pts = [(k[0] * duration_s, k[axis]) for k in keys]
    expr = f"{pts[-1][1]:.5f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(pts, pts[1:], strict=False))):
        span = max(t1 - t0, 1e-4)
        expr = f"if(lt({var},{t1:.4f}),{v0:.5f}+({v1 - v0:.5f})*max(0,{var}-{t0:.4f})/{span:.4f},{expr})"
    return expr
