"""Deterministic colour statistics for shots and frames (manual 8.6, 11.3, 11.7).

Statistics favour robust percentiles on mid-tones and treat saturation as
relative, because platform re-encoding destroys grain and shifts chroma.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from vanta.core.colormath import chroma_hue, luma, mccamy_cct, rgb_to_lab, skin_weight, to_float

LOOK_KEYS = (
    "luma_p05",
    "luma_p50",
    "luma_p95",
    "contrast",
    "black_point",
    "shadow_a",
    "shadow_b",
    "mid_a",
    "mid_b",
    "high_a",
    "high_b",
    "chroma_mean",
    "chroma_shadow",
    "chroma_high",
)


@dataclass
class ColorStats:
    luma_mean: float
    luma_p01: float
    luma_p05: float
    luma_p25: float
    luma_p50: float
    luma_p75: float
    luma_p95: float
    luma_p99: float
    contrast: float  # p95 - p05 luma
    black_point: float  # p01 luma
    white_point: float  # p99 luma
    clip_low: float  # fraction of near-black pixels
    clip_high: float  # fraction of near-white pixels
    shadow_a: float
    shadow_b: float
    mid_a: float
    mid_b: float
    high_a: float
    high_b: float
    chroma_mean: float
    chroma_p90: float
    chroma_shadow: float
    chroma_mid: float
    chroma_high: float
    cct_k: float
    skin_fraction: float
    skin_hue: float | None
    skin_chroma: float | None
    dominant_colors: list[dict[str, float]] = field(default_factory=list)

    def look_vector(self) -> np.ndarray:
        return np.asarray([getattr(self, key) for key in LOOK_KEYS], dtype=np.float64)

    def to_dict(self) -> dict:
        return asdict(self)


def _downsample(rgb: np.ndarray, max_pixels: int = 160_000) -> np.ndarray:
    h, w = rgb.shape[:2]
    step = int(np.ceil(np.sqrt(h * w / max_pixels))) if h * w > max_pixels else 1
    return rgb[::step, ::step]


def _band_mean(values: np.ndarray, mask: np.ndarray, default: float = 0.0) -> float:
    return float(np.mean(values[mask])) if np.count_nonzero(mask) > 50 else default


def dominant_colors(lab: np.ndarray, k: int = 5, seed: int = 3) -> list[dict[str, float]]:
    """k-means palette in Lab space, weighted by cluster share."""
    import cv2

    pixels = lab.reshape(-1, 3).astype(np.float32)
    if len(pixels) > 20_000:
        rng = np.random.default_rng(seed)
        pixels = pixels[rng.choice(len(pixels), 20_000, replace=False)]
    if len(pixels) < k:
        return []
    cv2.setRNGSeed(seed)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 2, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k) / len(labels)
    order = np.argsort(-counts)
    from vanta.core.colormath import lab_to_rgb

    palette = []
    for idx in order:
        lab_c = centers[idx]
        rgb_c = lab_to_rgb(lab_c[None, :])[0]
        palette.append(
            {
                "L": float(lab_c[0]),
                "a": float(lab_c[1]),
                "b": float(lab_c[2]),
                "hex": "#" + "".join(f"{int(round(v * 255)):02x}" for v in rgb_c),
                "share": float(counts[idx]),
            }
        )
    return palette


def frame_stats(
    rgb: np.ndarray,
    *,
    face_boxes: list[tuple[float, float, float, float]] | None = None,
    palette: bool = False,
) -> ColorStats:
    """Measure one RGB frame (uint8 or float). ``face_boxes`` are normalised x,y,w,h."""
    x = to_float(_downsample(rgb))
    y = luma(x)
    lab = rgb_to_lab(x)
    chroma, hue = chroma_hue(lab)
    p = np.percentile(y, [1, 5, 25, 50, 75, 95, 99])
    shadows = y < 0.25
    mids = (y >= 0.25) & (y < 0.7)
    highs = y >= 0.7
    sw = skin_weight(lab)
    if face_boxes:
        mask = np.zeros(y.shape, dtype=bool)
        h, w = y.shape
        for bx, by, bw, bh in face_boxes:
            x0, y0 = int(bx * w), int(by * h)
            mask[max(0, y0) : min(h, y0 + int(bh * h)), max(0, x0) : min(w, x0 + int(bw * w))] = True
        sw = sw * mask
    skin_mask = sw > 0.5
    mid_mask = (y > 0.2) & (y < 0.85)
    mean_mid = x[mid_mask].mean(axis=0) if np.count_nonzero(mid_mask) > 50 else x.reshape(-1, 3).mean(0)
    skin_hue = float(np.median(hue[skin_mask])) if np.count_nonzero(skin_mask) > 80 else None
    skin_chroma = float(np.median(chroma[skin_mask])) if np.count_nonzero(skin_mask) > 80 else None
    return ColorStats(
        luma_mean=float(y.mean()),
        luma_p01=float(p[0]),
        luma_p05=float(p[1]),
        luma_p25=float(p[2]),
        luma_p50=float(p[3]),
        luma_p75=float(p[4]),
        luma_p95=float(p[5]),
        luma_p99=float(p[6]),
        contrast=float(p[5] - p[1]),
        black_point=float(p[0]),
        white_point=float(p[6]),
        clip_low=float(np.mean(y <= 2 / 255)),
        clip_high=float(np.mean(np.max(x, axis=-1) >= 253 / 255)),
        shadow_a=_band_mean(lab[..., 1], shadows),
        shadow_b=_band_mean(lab[..., 2], shadows),
        mid_a=_band_mean(lab[..., 1], mids),
        mid_b=_band_mean(lab[..., 2], mids),
        high_a=_band_mean(lab[..., 1], highs),
        high_b=_band_mean(lab[..., 2], highs),
        chroma_mean=float(chroma.mean()),
        chroma_p90=float(np.percentile(chroma, 90)),
        chroma_shadow=_band_mean(chroma, shadows),
        chroma_mid=_band_mean(chroma, mids),
        chroma_high=_band_mean(chroma, highs),
        cct_k=mccamy_cct(mean_mid),
        skin_fraction=float(np.mean(skin_mask)),
        skin_hue=skin_hue,
        skin_chroma=skin_chroma,
        dominant_colors=dominant_colors(lab) if palette else [],
    )


def aggregate(stats: list[ColorStats]) -> ColorStats:
    """Robust (median) aggregate of per-frame statistics; never trust one frame."""
    if not stats:
        raise ValueError("no frame statistics to aggregate")
    if len(stats) == 1:
        return stats[0]
    numeric = {}
    for key, value in stats[0].to_dict().items():
        if key == "dominant_colors":
            continue
        values = [getattr(s, key) for s in stats if getattr(s, key) is not None]
        if isinstance(value, float) or value is None:
            numeric[key] = float(np.median(values)) if values else None
    palette = max(stats, key=lambda s: len(s.dominant_colors)).dominant_colors
    return ColorStats(**numeric, dominant_colors=palette)


def look_distance(a: ColorStats, b: ColorStats, weights: np.ndarray | None = None) -> float:
    """Scale-normalised distance between two looks (used by the grade solver and L4 QC)."""
    scale = np.asarray([0.05, 0.05, 0.05, 0.05, 0.03, 3, 3, 3, 3, 3, 3, 4, 4, 4], dtype=np.float64)
    diff = (a.look_vector() - b.look_vector()) / scale
    if weights is not None:
        diff = diff * weights
    return float(np.sqrt(np.mean(diff**2)))


def describe_look(stats: ColorStats) -> dict[str, str]:
    """Human-readable labels derived from numbers (labels are views over measurements)."""

    def tint_word(a: float, b: float) -> str:
        if abs(a) < 1.5 and abs(b) < 1.5:
            return "neutral"
        if b < -1.5 and a < 1.5:
            return "cool/cyan" if a < -1 else "cool/blue"
        if b > 1.5 and a > 1.5:
            return "warm/orange"
        if b > 1.5:
            return "warm/yellow"
        if a > 1.5:
            return "magenta"
        return "green"

    return {
        "exposure": "low-key"
        if stats.luma_p50 < 0.28
        else ("high-key" if stats.luma_p50 > 0.62 else "mid-key"),
        "contrast": "high" if stats.contrast > 0.72 else ("low/flat" if stats.contrast < 0.45 else "medium"),
        "blacks": "crushed"
        if stats.black_point < 0.015
        else ("lifted/matte" if stats.black_point > 0.07 else "normal"),
        "saturation": "rich"
        if stats.chroma_mean > 28
        else ("muted" if stats.chroma_mean < 12 else "moderate"),
        "shadows": tint_word(stats.shadow_a, stats.shadow_b),
        "highlights": tint_word(stats.high_a, stats.high_b),
        "temperature": f"{int(round(stats.cct_k, -2))}K",
    }
