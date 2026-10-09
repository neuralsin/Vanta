"""Frame-level effect detectors that deterministic CV can confirm (manual 8.2).

Each detector is conservative and returns a measured value plus a confidence;
re-encoded reels destroy grain and soften edges, so those detectors cap their
confidence accordingly.
"""

from __future__ import annotations

import cv2
import numpy as np


def vignette(frames: list[np.ndarray]) -> dict | None:
    ratios = []
    for rgb in frames:
        grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
        h, w = grey.shape
        cy, cx = h // 2, w // 2
        centre = np.median(grey[cy - h // 6 : cy + h // 6, cx - w // 6 : cx + w // 6])
        corners = [grey[: h // 8, : w // 8], grey[: h // 8, -w // 8 :], grey[-h // 8 :, : w // 8], grey[-h // 8 :, -w // 8 :]]
        corner = np.median([np.median(c) for c in corners])
        if centre > 0.08:
            ratios.append(corner / centre)
    if len(ratios) < 2:
        return None
    ratio = float(np.median(ratios))
    consistency = float(np.mean(np.asarray(ratios) < 0.8))
    if ratio < 0.78 and consistency > 0.6:
        return {"type": "vignette", "strength": round(1 - ratio, 3), "confidence": round(0.5 + 0.4 * consistency * min(1, (0.8 - ratio) * 4), 3)}
    return None


def letterbox(frames: list[np.ndarray]) -> dict | None:
    hits = []
    for rgb in frames:
        grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        rows = grey.mean(axis=1)
        h = len(rows)
        top = np.argmax(rows > 12) if (rows > 12).any() else 0
        bottom = h - np.argmax(rows[::-1] > 12) if (rows > 12).any() else h
        hits.append((top / h, (h - bottom) / h))
    tops, bottoms = zip(*hits, strict=True) if hits else ((), ())
    if hits and np.median(tops) > 0.04 and np.median(bottoms) > 0.04:
        return {"type": "letterbox", "bar_fraction": round(float(np.median(tops) + np.median(bottoms)), 3), "confidence": 0.85}
    return None


def glow(frames: list[np.ndarray]) -> dict | None:
    """Bloom: highlights surrounded by a wide soft luminance halo."""
    scores = []
    for rgb in frames:
        grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
        bright = (grey > 0.92).astype(np.uint8)
        if bright.mean() < 0.002 or bright.mean() > 0.25:
            continue
        ring_near = cv2.dilate(bright, np.ones((7, 7), np.uint8)) - bright
        ring_far = cv2.dilate(bright, np.ones((31, 31), np.uint8)) - cv2.dilate(bright, np.ones((15, 15), np.uint8))
        background = grey[(cv2.dilate(bright, np.ones((61, 61), np.uint8)) == 0)]
        if not ring_far.any() or background.size == 0:
            continue
        halo = (grey[ring_far > 0].mean() - np.median(background)) / max(grey[ring_near > 0].mean(), 1e-3)
        scores.append(halo)
    if len(scores) < 2:
        return None
    value = float(np.median(scores))
    if value > 0.25:
        return {"type": "glow", "halo": round(value, 3), "confidence": round(min(0.8, 0.4 + value), 3)}
    return None


def grain(frames: list[np.ndarray]) -> dict | None:
    levels = []
    for rgb in frames:
        grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        hp = grey - cv2.GaussianBlur(grey, (0, 0), 1.2)
        flat = cv2.GaussianBlur(np.abs(cv2.Laplacian(grey, cv2.CV_32F)), (0, 0), 4) < 6
        if flat.mean() < 0.1:
            continue
        levels.append(float(hp[flat].std()))
    if len(levels) < 2:
        return None
    level = float(np.median(levels))
    if level > 2.2:
        # platform compression removes most grain: confidence stays modest
        return {"type": "grain", "level": round(level, 3), "confidence": round(min(0.6, 0.3 + level / 10), 3)}
    return None


def chromatic_aberration(frames: list[np.ndarray]) -> dict | None:
    shifts = []
    for rgb in frames:
        h, w = rgb.shape[:2]
        for ys, xs in ((slice(0, h // 4), slice(0, w // 4)), (slice(-h // 4, None), slice(-w // 4, None))):
            r = rgb[ys, xs, 0].astype(np.float32)
            b = rgb[ys, xs, 2].astype(np.float32)
            if r.std() < 8 or b.std() < 8:
                continue
            (dx, dy), response = cv2.phaseCorrelate(r, b)
            if response > 0.2:
                shifts.append(np.hypot(dx, dy))
    if len(shifts) < 2:
        return None
    shift = float(np.median(shifts))
    if 0.8 < shift < 12:
        return {"type": "chromatic_aberration", "shift_px": round(shift, 2), "confidence": round(min(0.7, 0.35 + shift / 10), 3)}
    return None


def split_screen(frames: list[np.ndarray]) -> dict | None:
    """Persistent long straight seams (split screen) or an inset rectangle (picture-in-picture)."""
    seams = []
    for rgb in frames:
        grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(grey, 40, 120)
        h, w = edges.shape
        cols = edges.mean(axis=0) / 255
        rows = edges.mean(axis=1) / 255
        v = [x / w for x in np.nonzero(cols > 0.75)[0] if 0.1 * w < x < 0.9 * w]
        hz = [y / h for y in np.nonzero(rows > 0.75)[0] if 0.1 * h < y < 0.9 * h]
        seams.append((round(np.median(v), 2) if v else None, round(np.median(hz), 2) if hz else None))
    vertical = [s[0] for s in seams if s[0] is not None]
    horizontal = [s[1] for s in seams if s[1] is not None]
    if len(vertical) >= max(2, len(frames) * 0.6):
        return {"type": "split_screen", "orientation": "vertical", "position": float(np.median(vertical)), "confidence": 0.65}
    if len(horizontal) >= max(2, len(frames) * 0.6):
        kind = "split_screen" if 0.35 < np.median(horizontal) < 0.65 else "picture_in_picture"
        return {"type": kind, "orientation": "horizontal", "position": float(np.median(horizontal)), "confidence": 0.55}
    return None


DETECTORS = (vignette, letterbox, glow, grain, chromatic_aberration, split_screen)


def detect_frame_effects(frames: list[np.ndarray]) -> list[dict]:
    found = []
    for detector in DETECTORS:
        try:
            result = detector(frames)
        except cv2.error:
            result = None
        if result:
            found.append(result)
    return found
