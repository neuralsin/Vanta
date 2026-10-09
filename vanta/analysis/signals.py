"""One decoding pass that extracts every per-frame signal the analysers need.

Signals are computed on a small grey/HSV copy of each proxy frame so a 30 s
reel is processed in seconds. Optical flow is DIS (dense inverse search) on 192 px frames and is
summarised as a global similarity transform (pan, tilt, zoom, roll) plus the
residual (subject) motion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from vanta.analysis.media import FrameReader

FLOW_WIDTH = 192


@dataclass
class FrameSignals:
    fps: float
    width: int
    height: int
    luma: np.ndarray  # mean luma per frame (0..1)
    hist_diff: np.ndarray  # histogram distance to previous frame (0..1)
    pixel_diff: np.ndarray  # mean abs grey difference to previous frame (0..1)
    sharpness: np.ndarray  # variance of Laplacian (normalised)
    saturation: np.ndarray  # mean HSV saturation
    flow_mag: np.ndarray  # mean flow magnitude, fraction of frame width per frame
    pan: np.ndarray  # global dx per frame (fraction of width)
    tilt: np.ndarray  # global dy per frame (fraction of height)
    zoom: np.ndarray  # log scale change per frame
    roll: np.ndarray  # rotation (radians) per frame
    residual: np.ndarray  # non-global (subject) motion magnitude
    radial: np.ndarray  # mean outward flow (zoom-transition cue)

    @property
    def frame_count(self) -> int:
        return len(self.luma)

    def time(self, index: int) -> float:
        return index / self.fps

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            path,
            meta=np.asarray([self.fps, self.width, self.height]),
            **{k: getattr(self, k) for k in _ARRAYS},
        )

    @classmethod
    def load(cls, path: str | Path) -> FrameSignals:
        data = np.load(path)
        fps, width, height = data["meta"]
        return cls(fps=float(fps), width=int(width), height=int(height), **{k: data[k] for k in _ARRAYS})


_ARRAYS = (
    "luma",
    "hist_diff",
    "pixel_diff",
    "sharpness",
    "saturation",
    "flow_mag",
    "pan",
    "tilt",
    "zoom",
    "roll",
    "residual",
    "radial",
)


def _hist(hsv: np.ndarray) -> np.ndarray:
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [16, 4, 4], [0, 180, 0, 256, 0, 256])
    hist = hist.ravel().astype(np.float32)
    return hist / max(1.0, hist.sum())


def _global_motion(
    flow: np.ndarray, texture: np.ndarray | None = None
) -> tuple[float, float, float, float, float, float]:
    """Fit a similarity transform to a flow grid; return pan, tilt, zoom, roll, residual, radial.

    Only textured grid points are used: flow in flat regions is ~0 regardless of
    camera motion and would bias the fit toward "static".
    """
    h, w = flow.shape[:2]
    step = 6
    ys, xs = np.mgrid[step // 2 : h : step, step // 2 : w : step]
    ys, xs = ys.ravel(), xs.ravel()
    if texture is not None:
        strength = texture[ys, xs]
        keep = strength > max(8.0, float(np.percentile(strength, 50)))
        if keep.sum() >= 12:
            ys, xs = ys[keep], xs[keep]
    pts = np.stack([xs, ys], axis=-1).astype(np.float32)
    vec = flow[ys, xs]
    dst = pts + vec
    matrix, inliers = cv2.estimateAffinePartial2D(
        pts, dst, method=cv2.RANSAC, ransacReprojThreshold=1.5, maxIters=200
    )
    cx, cy = w / 2, h / 2
    rel = pts - np.array([cx, cy], dtype=np.float32)
    norm = np.linalg.norm(rel, axis=1) + 1e-6
    radial = float(np.mean(np.sum(vec * rel, axis=1) / norm) / w)
    if matrix is None:
        mag = float(np.mean(np.linalg.norm(vec, axis=1)) / w)
        return 0.0, 0.0, 0.0, 0.0, mag, radial
    a, b = matrix[0, 0], matrix[1, 0]
    scale = float(np.hypot(a, b))
    roll = float(np.arctan2(b, a))
    # translation of the frame centre
    centre = matrix @ np.array([cx, cy, 1.0])
    pan = float((centre[0] - cx) / w)
    tilt = float((centre[1] - cy) / h)
    predicted = (pts @ matrix[:, :2].T) + matrix[:, 2]
    residual = float(np.mean(np.linalg.norm(dst - predicted, axis=1)) / w)
    return pan, tilt, float(np.log(max(scale, 1e-6))), roll, residual, radial


def extract_signals(proxy_path: str | Path, *, progress=None) -> FrameSignals:
    with FrameReader(proxy_path) as reader:
        fps = reader.fps
        rows: dict[str, list[float]] = {k: [] for k in _ARRAYS}
        prev_hist = None
        prev_grey = None
        prev_small = None
        total = max(1, reader.frame_count)
        # DIS flow: OpenCV 5's Farneback under-reports motion on real footage
        dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        for index, rgb in reader.iterate():
            h, w = rgb.shape[:2]
            scale = FLOW_WIDTH / w
            small = cv2.resize(rgb, (FLOW_WIDTH, max(8, int(h * scale))), interpolation=cv2.INTER_AREA)
            grey = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
            hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
            hist = _hist(hsv)
            rows["luma"].append(float(grey.mean() / 255.0))
            rows["saturation"].append(float(hsv[..., 1].mean() / 255.0))
            lap = cv2.Laplacian(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), cv2.CV_32F)
            rows["sharpness"].append(float(lap.var() / 1000.0))
            if prev_hist is None:
                rows["hist_diff"].append(0.0)
                rows["pixel_diff"].append(0.0)
                for key in ("flow_mag", "pan", "tilt", "zoom", "roll", "residual", "radial"):
                    rows[key].append(0.0)
            else:
                rows["hist_diff"].append(float(0.5 * np.abs(hist - prev_hist).sum()))
                rows["pixel_diff"].append(
                    float(np.mean(cv2.absdiff(grey, prev_grey)) / 255.0)
                )
                flow = dis.calc(prev_small, grey, None)
                mag = float(np.mean(np.linalg.norm(flow, axis=-1)) / FLOW_WIDTH)
                gx = cv2.Sobel(prev_small, cv2.CV_32F, 1, 0, ksize=3)
                gy = cv2.Sobel(prev_small, cv2.CV_32F, 0, 1, ksize=3)
                texture = cv2.GaussianBlur(np.hypot(gx, gy), (5, 5), 0)
                pan, tilt, zoom, roll, residual, radial = _global_motion(flow, texture)
                rows["flow_mag"].append(mag)
                rows["pan"].append(pan)
                rows["tilt"].append(tilt)
                rows["zoom"].append(zoom)
                rows["roll"].append(roll)
                rows["residual"].append(residual)
                rows["radial"].append(radial)
            prev_hist, prev_grey, prev_small = hist, grey, grey
            if progress and index % 30 == 0:
                progress(min(1.0, index / total))
        width, height = reader.width, reader.height
    if not rows["luma"]:
        raise ValueError(f"no frames decoded from {proxy_path}")
    return FrameSignals(
        fps=fps,
        width=width,
        height=height,
        **{k: np.asarray(v, dtype=np.float32) for k, v in rows.items()},
    )
