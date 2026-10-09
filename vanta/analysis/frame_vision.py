"""Lightweight deterministic vision: faces, saliency, composition, technical quality.

Faces use OpenCV's YuNet detector when its ONNX file is present (registered in
the model registry), otherwise the bundled Haar cascade. Everything here runs on
CPU in milliseconds per frame and never needs the GPU lease.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

_face_lock = threading.Lock()
_yunet = None
_haar = None


def _yunet_path() -> Path:
    from vanta.config import ROOT, get_settings

    name = "face_detection_yunet_2023mar.onnx"
    for folder in (get_settings().models_dir, ROOT / "data" / "models"):
        candidate = folder / "opencv" / name
        if candidate.is_file():
            return candidate
    return get_settings().models_dir / "opencv" / name


def detect_faces(rgb: np.ndarray, *, min_score: float = 0.7) -> list[dict]:
    """Return faces as normalised boxes ``{x,y,w,h,score,landmarks?}``."""
    global _yunet, _haar
    h, w = rgb.shape[:2]
    scale = 640 / max(h, w) if max(h, w) > 640 else 1.0
    img = cv2.resize(rgb, (int(w * scale), int(h * scale))) if scale != 1.0 else rgb
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    ih, iw = bgr.shape[:2]
    with _face_lock:
        path = _yunet_path()
        if _yunet is None and path.is_file():
            try:
                _yunet = cv2.FaceDetectorYN.create(str(path), "", (iw, ih), min_score, 0.3, 50)
            except cv2.error:
                _yunet = False
        if _yunet:
            _yunet.setInputSize((iw, ih))
            _, faces = _yunet.detect(bgr)
            out = []
            for row in faces if faces is not None else []:
                x, y, fw, fh = row[:4]
                out.append(
                    {
                        "x": float(x / iw),
                        "y": float(y / ih),
                        "w": float(fw / iw),
                        "h": float(fh / ih),
                        "score": float(row[14]),
                        "detector": "yunet",
                    }
                )
            return out
        if _haar is None:
            # OpenCV 5 moved Haar cascades out of the main module; without YuNet there is no detector
            cascade = getattr(cv2, "CascadeClassifier", None)
            data = getattr(cv2, "data", None)
            _haar = cascade(str(Path(data.haarcascades) / "haarcascade_frontalface_default.xml")) if cascade and data else False
        if _haar is False:
            return []
        grey = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        rects = _haar.detectMultiScale(grey, 1.1, 6, minSize=(int(iw * 0.05), int(iw * 0.05)))
        return [
            {
                "x": float(x / iw),
                "y": float(y / ih),
                "w": float(fw / iw),
                "h": float(fh / ih),
                "score": 0.75,
                "detector": "haar",
            }
            for x, y, fw, fh in (rects if len(rects) else [])
        ]


def saliency_map(rgb: np.ndarray, size: int = 128) -> np.ndarray:
    """Spectral-residual saliency (Hou & Zhang 2007), normalised to 0..1 at ``size`` px."""
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    h, w = grey.shape
    small = cv2.resize(grey, (size, max(8, int(size * h / w))), interpolation=cv2.INTER_AREA)
    spectrum = np.fft.fft2(small)
    log_amp = np.log(np.abs(spectrum) + 1e-6)
    phase = np.angle(spectrum)
    residual = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(residual + 1j * phase))) ** 2
    sal = cv2.GaussianBlur(sal, (0, 0), 2.5)
    sal -= sal.min()
    return sal / max(sal.max(), 1e-9)


@dataclass
class Composition:
    subject_x: float
    subject_y: float
    subject_size: float  # fraction of frame area covered by the salient subject
    thirds_score: float  # 1 = subject on a thirds intersection
    negative_space: float  # fraction of low-saliency area
    negative_space_side: str  # left | right | top | bottom | none
    shot_size: str  # extreme_close | close | medium_close | medium | wide | extreme_wide
    face_count: int
    faces: list[dict] = field(default_factory=list)
    background_complexity: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _shot_size(face_h: float | None, subject_size: float) -> str:
    if face_h is not None:
        if face_h > 0.45:
            return "extreme_close"
        if face_h > 0.25:
            return "close"
        if face_h > 0.14:
            return "medium_close"
        if face_h > 0.07:
            return "medium"
        if face_h > 0.03:
            return "wide"
        return "extreme_wide"
    if subject_size > 0.45:
        return "close"
    if subject_size > 0.2:
        return "medium"
    if subject_size > 0.06:
        return "wide"
    return "extreme_wide"


def composition(rgb: np.ndarray, faces: list[dict] | None = None) -> Composition:
    faces = detect_faces(rgb) if faces is None else faces
    sal = saliency_map(rgb)
    h, w = sal.shape
    for face in faces:
        x0, y0 = int(face["x"] * w), int(face["y"] * h)
        x1, y1 = int((face["x"] + face["w"]) * w), int((face["y"] + face["h"]) * h)
        sal[max(0, y0) : max(0, y1), max(0, x0) : max(0, x1)] += 1.0
    weights = sal / max(sal.sum(), 1e-9)
    ys, xs = np.mgrid[0:h, 0:w]
    sx = float((weights * xs).sum() / w)
    sy = float((weights * ys).sum() / h)
    mask = sal > max(0.35, float(np.percentile(sal, 80)))
    subject_size = float(mask.mean())
    thirds = [(1 / 3, 1 / 3), (2 / 3, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 2 / 3)]
    dist = min(np.hypot(sx - tx, sy - ty) for tx, ty in thirds)
    thirds_score = float(np.exp(-((dist / 0.12) ** 2)))
    low = sal < 0.15
    halves = {
        "left": low[:, : w // 2].mean(),
        "right": low[:, w // 2 :].mean(),
        "top": low[: h // 2].mean(),
        "bottom": low[h // 2 :].mean(),
    }
    side, value = max(halves.items(), key=lambda kv: kv[1])
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.resize(grey, (w * 2, h * 2)), 60, 160)
    largest_face = max((f["h"] for f in faces), default=None)
    return Composition(
        subject_x=round(sx, 4),
        subject_y=round(sy, 4),
        subject_size=round(subject_size, 4),
        thirds_score=round(thirds_score, 4),
        negative_space=round(float(low.mean()), 4),
        negative_space_side=side if value > 0.6 else "none",
        shot_size=_shot_size(largest_face, subject_size),
        face_count=len(faces),
        faces=faces,
        background_complexity=round(float(edges.mean() / 255.0), 4),
    )


@dataclass
class Quality:
    sharpness: float  # 0..1
    exposure_ok: float  # 0..1
    noise: float  # 0..1 (higher = noisier)
    clipped: float  # fraction clipped
    black_frame: bool
    score: float  # 0..1 overall usability

    def to_dict(self) -> dict:
        return asdict(self)


def quality(rgb: np.ndarray) -> Quality:
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = grey.shape
    scale = 720 / max(h, w) if max(h, w) > 720 else 1.0
    if scale != 1.0:
        grey = cv2.resize(grey, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    lap = cv2.Laplacian(grey, cv2.CV_32F).var()
    sharpness = float(1 - np.exp(-lap / 180.0))
    mean = grey.mean() / 255
    clipped = float(np.mean((grey <= 3) | (grey >= 252)))
    exposure_ok = float(np.exp(-(((mean - 0.45) / 0.3) ** 2)) * (1 - min(1.0, clipped * 3)))
    flat = cv2.GaussianBlur(grey, (5, 5), 0)
    noise_map = np.abs(grey.astype(np.float32) - flat.astype(np.float32))
    edges = cv2.Canny(grey, 50, 150) > 0
    noise_val = float(np.median(noise_map[~edges])) if (~edges).any() else 0.0
    noise = float(1 - np.exp(-noise_val / 4.0))
    black = bool(mean < 0.03 and grey.std() < 4)
    score = 0.0 if black else 0.45 * sharpness + 0.35 * exposure_ok + 0.2 * (1 - noise)
    return Quality(
        sharpness=round(sharpness, 4),
        exposure_ok=round(exposure_ok, 4),
        noise=round(noise, 4),
        clipped=round(clipped, 4),
        black_frame=black,
        score=round(float(score), 4),
    )
