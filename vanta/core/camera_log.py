"""Camera log / HDR input transforms -> display Rec.709 (the TECH stage for camera footage).

Each profile = published transfer function (code value -> scene linear) + a
3x3 gamut matrix to Rec.709 primaries. Scene linear is then tone-mapped with a
hue-preserving filmic curve (smooth highlight roll-off, no hard clip) and
encoded for display. Grading statistics are measured *after* this decode, and
the decode is baked into each shot LUT ahead of the creative look, so a log
clip in a non-managed Resolve timeline renders correctly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from vanta.core.colormath import REC709_LUMA, linear_to_srgb


def _slog3(x: np.ndarray) -> np.ndarray:
    cv = x * 1023.0
    return np.where(cv >= 171.2102946929, (10.0 ** ((cv - 420.0) / 261.5)) * (0.18 + 0.01) - 0.01, (cv - 95.0) * 0.01125 / (171.2102946929 - 95.0))


def _vlog(x: np.ndarray) -> np.ndarray:
    b, c, d = 0.00873, 0.241514, 0.598206
    return np.where(x < 0.181, (x - 0.125) / 5.6, 10.0 ** ((x - d) / c) - b)


def _clog3(x: np.ndarray) -> np.ndarray:
    return np.where(
        x < 0.097465473,
        -(10.0 ** ((0.12783901 - x) / 0.36726845) - 1) / 14.98325,
        np.where(x <= 0.15277891, (x - 0.12512219) / 1.9754798, (10.0 ** ((x - 0.12240537) / 0.36726845) - 1) / 14.98325),
    )


def _logc3(x: np.ndarray) -> np.ndarray:  # ARRI LogC3, EI 800
    cut, a, b, c, d, e, f = 0.010591, 5.555556, 0.052272, 0.247190, 0.385537, 5.367655, 0.092809
    return np.where(x > e * cut + f, (10.0 ** ((x - d) / c) - b) / a, (x - f) / e)


def _flog(x: np.ndarray) -> np.ndarray:
    a, b, c, d, e, f = 0.555556, 0.009468, 0.344676, 0.790453, 8.735631, 0.092864
    return np.where(x >= 0.100537775223865, (10.0 ** ((x - d) / c) - b) / a, (x - f) / e)


def _dlog(x: np.ndarray) -> np.ndarray:  # DJI D-Log
    return np.where(x <= 0.14, (x - 0.0929) / 6.025, (10.0 ** (3.89616 * x - 2.27752) - 0.0108) / 0.9892)


def _apple_log(x: np.ndarray) -> np.ndarray:
    r0, rt, c, b, gamma, beta = -0.05641088, 0.01, 47.28711236, 0.00964052, 0.08550479, 0.69336945
    pt = c * (rt - r0) ** 2
    return np.where(x >= pt, 2.0 ** ((x - beta) / gamma) - b, np.where(x < 0, r0, np.sqrt(np.maximum(x, 0) / c) + r0))


def _hlg(x: np.ndarray) -> np.ndarray:
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    scene = np.where(x <= 0.5, (x**2) / 3.0, (np.exp((x - c) / a) + b) / 12.0)
    return scene * 4.0  # HLG reference white (~0.26 scene) -> diffuse white near 1.0


def _pq(x: np.ndarray) -> np.ndarray:
    m1, m2, c1, c2, c3 = 0.1593017578125, 78.84375, 0.8359375, 18.8515625, 18.6875
    p = np.power(np.clip(x, 0, 1), 1 / m2)
    nits = 10000.0 * np.power(np.maximum(p - c1, 0) / (c2 - c3 * p), 1 / m1)
    return nits / 100.0  # 100 nits diffuse white = 1.0


M_SGAMUT3CINE = np.array([[1.6269474, -0.5401385, -0.0868089], [-0.1785155, 1.4179409, -0.2394254], [-0.0444361, -0.1959199, 1.2403560]])
M_VGAMUT = np.array([[1.806576, -0.695697, -0.110879], [-0.170090, 1.305955, -0.135865], [-0.025206, -0.154468, 1.179674]])
M_AWG3 = np.array([[1.617523, -0.537287, -0.080237], [-0.070573, 1.334613, -0.264040], [-0.021102, -0.226954, 1.248056]])
M_REC2020 = np.array([[1.660491, -0.587641, -0.072850], [-0.124550, 1.132900, -0.008349], [-0.018151, -0.100579, 1.118730]])


@dataclass(frozen=True)
class CameraProfile:
    key: str
    label: str
    decode: Callable[[np.ndarray], np.ndarray] | None
    matrix: np.ndarray | None
    exposure: float = 0.0  # stops applied in scene linear before tone mapping
    note: str = ""


PROFILES: dict[str, CameraProfile] = {
    "rec709": CameraProfile("rec709", "Rec.709 / standard (no decode)", None, None),
    "slog3": CameraProfile("slog3", "Sony S-Log3 / S-Gamut3.Cine", _slog3, M_SGAMUT3CINE),
    "vlog": CameraProfile("vlog", "Panasonic V-Log / V-Gamut", _vlog, M_VGAMUT),
    "clog3": CameraProfile("clog3", "Canon C-Log3 / Cinema Gamut", _clog3, M_REC2020, note="Cinema Gamut approximated by Rec.2020 primaries"),
    "logc3": CameraProfile("logc3", "ARRI LogC3 / AWG3 (EI 800)", _logc3, M_AWG3),
    "flog": CameraProfile("flog", "Fujifilm F-Log / F-Gamut", _flog, M_REC2020),
    "dlog": CameraProfile("dlog", "DJI D-Log / D-Gamut", _dlog, M_REC2020, note="D-Gamut approximated by Rec.2020 primaries"),
    "apple_log": CameraProfile("apple_log", "Apple Log (iPhone)", _apple_log, M_REC2020),
    "hlg": CameraProfile("hlg", "HLG / Rec.2100 (phones, HDR)", _hlg, M_REC2020),
    "pq": CameraProfile("pq", "PQ / HDR10", _pq, M_REC2020),
    "generic_log": CameraProfile("generic_log", "Log (unknown camera, S-Log3-like)", _slog3, None, note="pick the exact camera profile for best colour"),
}


def tonemap(lin: np.ndarray, *, white: float = 9.0, highlight_desat: float = 0.7) -> np.ndarray:
    """Hue-preserving filmic tone map: scene linear -> display linear in [0, 1]."""
    lin = np.maximum(lin, 0.0)
    y = lin @ REC709_LUMA.astype(lin.dtype)
    y_safe = np.maximum(y, 1e-6)
    y_t = y_safe * (1 + y_safe / (white * white)) / (1 + y_safe)  # extended Reinhard
    out = lin * (y_t / y_safe)[..., None]
    # desaturate toward white only where channels exceed display range (no hue shift)
    over = np.clip((np.max(out, axis=-1) - 0.8) / 0.4, 0, 1)[..., None] * highlight_desat
    out = out * (1 - over) + y_t[..., None] * over
    return np.clip(out, 0.0, 1.0)


def decode_to_display(rgb: np.ndarray, profile: str | CameraProfile, *, exposure: float = 0.0) -> np.ndarray:
    """Log/HDR code values in [0,1] -> display-referred Rec.709 gamma-encoded [0,1]."""
    prof = PROFILES[profile] if isinstance(profile, str) else profile
    if prof.decode is None:
        return np.clip(rgb, 0, 1)
    lin = prof.decode(np.asarray(rgb, dtype=np.float64))
    if prof.matrix is not None:
        lin = lin @ prof.matrix.T
    lin = lin * (2.0 ** (prof.exposure + exposure))
    return linear_to_srgb(tonemap(lin)).astype(np.float32)


def looks_like_log(stats: dict) -> tuple[bool, float]:
    """Flat, lifted, desaturated statistics are the signature of an undecoded log signal."""
    p05, p95 = stats.get("luma_p05", 0.0), stats.get("luma_p95", 1.0)
    chroma = stats.get("chroma_mean", 30.0)
    score = 0.0
    score += 0.4 * float(np.clip((p05 - 0.05) / 0.06, 0, 1))
    score += 0.3 * float(np.clip((0.82 - p95) / 0.15, 0, 1))
    score += 0.3 * float(np.clip((16 - chroma) / 8, 0, 1))
    return score >= 0.6, round(score, 3)


def detect_profile(info: dict, shot_stats: list[dict]) -> tuple[str, float, str]:
    """Best guess of a clip's input profile: metadata first, then statistics."""
    transfer = (info.get("color_transfer") or "").casefold()
    if "arib-std-b67" in transfer:
        return "hlg", 0.95, "HLG transfer flagged in the file"
    if "smpte2084" in transfer:
        return "pq", 0.95, "PQ transfer flagged in the file"
    if "log" in transfer:
        return "generic_log", 0.7, f"log transfer flagged ({transfer})"
    if not shot_stats:
        return "rec709", 0.5, "no statistics"
    scores = [looks_like_log(s)[1] for s in shot_stats if s]
    median = float(np.median(scores)) if scores else 0.0
    if median >= 0.6:
        return "generic_log", round(min(0.85, median), 3), "flat, lifted, desaturated image: looks like log — choose the exact camera profile"
    return "rec709", round(1 - median, 3), "standard display-referred footage"
