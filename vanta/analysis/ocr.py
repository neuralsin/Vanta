"""On-screen text detection, tracking into text events, and typography description (manual 8.3).

Exact fonts are never identified. A font *class* (weight x width, plus
case/outline/shadow) is recorded and later mapped onto installed fonts.
"""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

_engine = None
_engine_lock = threading.Lock()


class OCRUnavailable(RuntimeError):
    pass


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except Exception as exc:
                raise OCRUnavailable(f"RapidOCR is not installed: {exc}") from exc
            _engine = RapidOCR()
        return _engine


@dataclass
class TextDetection:
    text: str
    score: float
    box: tuple[float, float, float, float]  # normalised x, y, w, h
    time_s: float


def detect_text(rgb: np.ndarray, time_s: float = 0.0, *, min_score: float = 0.6) -> list[TextDetection]:
    try:
        engine = _get_engine()
    except OCRUnavailable:
        return []
    h, w = rgb.shape[:2]
    scale = 960 / max(h, w) if max(h, w) > 960 else 1.0
    img = cv2.resize(rgb, (int(w * scale), int(h * scale))) if scale != 1.0 else rgb
    try:
        result, _ = engine(cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    except Exception:
        return []
    out: list[TextDetection] = []
    ih, iw = img.shape[:2]
    for points, text, score in result or []:
        score = float(score)
        text = str(text).strip()
        if score < min_score or not text:
            continue
        pts = np.asarray(points, dtype=np.float32)
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        out.append(
            TextDetection(
                text=text,
                score=score,
                box=(float(x0 / iw), float(y0 / ih), float((x1 - x0) / iw), float((y1 - y0) / ih)),
                time_s=time_s,
            )
        )
    return out


def normalise_text(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.casefold())


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1 = a[0] + a[2], a[1] + a[3]
    bx1, by1 = b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax1, bx1) - max(a[0], b[0]))
    ih = max(0.0, min(ay1, by1) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Typography:
    weight: str  # light | regular | bold | heavy
    width: str  # condensed | normal | wide
    case: str  # upper | lower | title | mixed
    fill_hex: str
    outline: bool
    shadow_or_backing: bool
    size_frac: float  # cap height / frame height
    font_class: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TextEvent:
    text: str
    start_s: float
    end_s: float
    box: tuple[float, float, float, float]
    detections: int
    mean_score: float
    typography: Typography | None = None
    animation: str = "static"  # static | slide | scale | fade | word_by_word | typewriter
    occlusion_observed: bool = False
    words: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def position(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2] / 2, self.box[1] + self.box[3] / 2)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["position"] = list(self.position)
        payload["duration_s"] = self.duration_s
        return payload


def track_text(
    detections: list[TextDetection], *, max_gap_s: float = 0.6, min_iou: float = 0.2
) -> list[TextEvent]:
    """Merge per-frame detections into events by text similarity and box overlap."""
    events: list[dict] = []
    for det in sorted(detections, key=lambda d: d.time_s):
        key = normalise_text(det.text)
        if not key:
            continue
        match = None
        for event in events:
            if det.time_s - event["last"] > max_gap_s:
                continue
            same_text = event["key"] == key or key in event["key"] or event["key"] in key
            if same_text or _iou(event["box"], det.box) > 0.6:
                if _iou(event["box"], det.box) >= min_iou or same_text:
                    match = event
                    break
        if match is None:
            events.append(
                {
                    "key": key,
                    "texts": [det.text],
                    "boxes": [det.box],
                    "times": [det.time_s],
                    "scores": [det.score],
                    "box": det.box,
                    "last": det.time_s,
                }
            )
        else:
            match["texts"].append(det.text)
            match["boxes"].append(det.box)
            match["times"].append(det.time_s)
            match["scores"].append(det.score)
            match["last"] = det.time_s
            if len(key) > len(match["key"]):
                match["key"] = key
            match["box"] = det.box
    out: list[TextEvent] = []
    for event in events:
        texts = event["texts"]
        boxes = np.asarray(event["boxes"])
        longest = max(texts, key=len)
        animation = _animation(boxes, [len(t) for t in texts])
        lengths = [len(normalise_text(t)) for t in texts]
        # occlusion: full text, then a markedly shorter reading, then full text again
        occluded = False
        full = max(lengths)
        for i in range(1, len(lengths) - 1):
            if lengths[i] < 0.7 * full and max(lengths[:i]) == full and max(lengths[i + 1 :]) == full:
                occluded = True
        out.append(
            TextEvent(
                text=longest,
                start_s=float(min(event["times"])),
                end_s=float(max(event["times"])),
                box=tuple(float(v) for v in np.median(boxes, axis=0)),
                detections=len(texts),
                mean_score=float(np.mean(event["scores"])),
                animation=animation,
                occlusion_observed=occluded,
                words=longest.split(),
            )
        )
    return out


def _animation(boxes: np.ndarray, lengths: list[int]) -> str:
    if len(boxes) < 3:
        return "static"
    centres = boxes[:, :2] + boxes[:, 2:] / 2
    travel = float(np.linalg.norm(centres[-1] - centres[0]))
    widths = boxes[:, 2]
    growth = float(widths[-1] / max(widths[0], 1e-6))
    if lengths[-1] > lengths[0] * 1.5 and travel < 0.03:
        return "typewriter" if lengths[0] <= 3 else "word_by_word"
    if travel > 0.05:
        return "slide"
    if growth > 1.25 or growth < 0.8:
        return "scale"
    return "static"


def describe_typography(rgb: np.ndarray, box: tuple[float, float, float, float], text: str) -> Typography:
    """Estimate weight/width/fill/outline from pixels inside a text box."""
    h, w = rgb.shape[:2]
    x0, y0 = int(box[0] * w), int(box[1] * h)
    x1, y1 = int((box[0] + box[2]) * w), int((box[1] + box[3]) * h)
    crop = rgb[max(0, y0) : max(y0 + 2, y1), max(0, x0) : max(x0 + 2, x1)]
    grey = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # text is the minority class
    if binary.mean() > 127:
        binary = 255 - binary
    glyph = binary > 0
    fill = crop[glyph].mean(axis=0) if glyph.any() else np.array([255, 255, 255])
    dist = cv2.distanceTransform((glyph * 255).astype(np.uint8), cv2.DIST_L2, 3)
    stroke = float(2 * np.percentile(dist[glyph], 90)) if glyph.any() else 1.0
    box_h = max(1, crop.shape[0])
    stroke_ratio = stroke / box_h
    weight = (
        "heavy"
        if stroke_ratio > 0.2
        else ("bold" if stroke_ratio > 0.13 else ("regular" if stroke_ratio > 0.07 else "light"))
    )
    chars = max(1, len(text.replace(" ", "")))
    char_aspect = (crop.shape[1] / chars) / box_h
    width = "condensed" if char_aspect < 0.45 else ("wide" if char_aspect > 0.8 else "normal")
    letters = [c for c in text if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        case = "upper"
    elif letters and all(c.islower() for c in letters):
        case = "lower"
    elif text.istitle():
        case = "title"
    else:
        case = "mixed"
    ring = cv2.dilate(glyph.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2).astype(bool) & ~glyph
    ring_luma = float(grey[ring].mean()) if ring.any() else 128.0
    fill_luma = float(grey[glyph].mean()) if glyph.any() else 128.0
    outline = abs(fill_luma - ring_luma) > 90
    background = ~cv2.dilate(glyph.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=3).astype(bool)
    backing = bool(background.any() and float(grey[background].std()) < 12)
    return Typography(
        weight=weight,
        width=width,
        case=case,
        fill_hex="#" + "".join(f"{int(v):02x}" for v in fill[:3]),
        outline=bool(outline),
        shadow_or_backing=backing,
        size_frac=round(box[3], 4),
        font_class=f"{width} {weight} sans".replace("normal ", ""),
    )
