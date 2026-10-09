"""Colour conversions and difference metrics (sRGB/Rec.709 display referred, D65)."""

from __future__ import annotations

import numpy as np

REC709_LUMA = np.array([0.2126, 0.7152, 0.0722])
_M_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883])


# Y'CbCr luma coefficients (Kr, Kb) per matrix
_KRB = {"bt601": (0.299, 0.114), "bt709": (0.2126, 0.0722), "bt2020": (0.2627, 0.0593)}


def rgb_to_ycbcr_matrix(matrix: str) -> np.ndarray:
    """Normalised R'G'B' -> Y'CbCr (Y in 0..1, Cb/Cr in -0.5..0.5) for a named matrix."""
    kr, kb = _KRB[matrix]
    kg = 1 - kr - kb
    return np.array(
        [
            [kr, kg, kb],
            [-kr / (2 * (1 - kb)), -kg / (2 * (1 - kb)), 0.5],
            [0.5, -kg / (2 * (1 - kr)), -kb / (2 * (1 - kr))],
        ]
    )


def matrix_correction(actual: str, assumed: str = "bt601") -> np.ndarray:
    """RGB decoded with the ``assumed`` matrix -> RGB decoded with the ``actual`` one (exact, linear)."""
    return np.linalg.inv(rgb_to_ycbcr_matrix(actual)) @ rgb_to_ycbcr_matrix(assumed)


def correct_decoded(rgb: np.ndarray, actual: str, assumed: str = "bt601") -> np.ndarray:
    """Fix a frame a decoder converted with the wrong Y'CbCr matrix (OpenCV always uses BT.601)."""
    if actual == assumed:
        return rgb
    fixed = to_float(rgb) @ matrix_correction(actual, assumed).T.astype(np.float32)
    if rgb.dtype == np.uint8:
        return np.clip(fixed * 255 + 0.5, 0, 255).astype(np.uint8)
    return np.clip(fixed, 0, 1).astype(np.float32)


def to_float(rgb: np.ndarray) -> np.ndarray:
    if rgb.dtype == np.uint8:
        return rgb.astype(np.float32) / 255.0
    if rgb.dtype == np.uint16:
        return rgb.astype(np.float32) / 65535.0
    return rgb.astype(np.float32, copy=False)


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def luma(rgb: np.ndarray) -> np.ndarray:
    """Rec.709 luma on gamma-encoded values (what a waveform shows)."""
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Gamma-encoded sRGB float [0,1] -> CIE L*a*b* (D65)."""
    lin = srgb_to_linear(rgb)
    xyz = lin @ _M_RGB_TO_XYZ.T
    xyz = xyz / _WHITE_D65
    eps = 216 / 24389
    kappa = 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    lab = np.empty_like(f)
    lab[..., 0] = 116 * f[..., 1] - 16
    lab[..., 1] = 500 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200 * (f[..., 1] - f[..., 2])
    return lab


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    eps = 216 / 24389
    kappa = 24389 / 27
    xr = np.where(fx**3 > eps, fx**3, (116 * fx - 16) / kappa)
    yr = np.where(lab[..., 0] > kappa * eps, fy**3, lab[..., 0] / kappa)
    zr = np.where(fz**3 > eps, fz**3, (116 * fz - 16) / kappa)
    xyz = np.stack([xr, yr, zr], axis=-1) * _WHITE_D65
    lin = xyz @ np.linalg.inv(_M_RGB_TO_XYZ).T
    return np.clip(linear_to_srgb(lin), 0.0, 1.0)


def chroma_hue(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    hue = np.degrees(np.arctan2(lab[..., 2], lab[..., 1])) % 360
    return chroma, hue


def delta_e_2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Vectorised CIEDE2000 colour difference."""
    l1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    l2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    c1 = np.hypot(a1, b1)
    c2 = np.hypot(a2, b2)
    c_bar = (c1 + c2) / 2
    g = 0.5 * (1 - np.sqrt(c_bar**7 / (c_bar**7 + 25**7)))
    a1p = (1 + g) * a1
    a2p = (1 + g) * a2
    c1p = np.hypot(a1p, b1)
    c2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dlp = l2 - l1
    dcp = c2p - c1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, dh)
    dh = np.where(dh < -180, dh + 360, dh)
    dh = np.where(c1p * c2p == 0, 0, dh)
    dhp = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh) / 2)
    l_bar = (l1 + l2) / 2
    cp_bar = (c1p + c2p) / 2
    h_sum = h1p + h2p
    hp_bar = np.where(
        c1p * c2p == 0,
        h_sum,
        np.where(
            np.abs(h1p - h2p) <= 180,
            h_sum / 2,
            np.where(h_sum < 360, (h_sum + 360) / 2, (h_sum - 360) / 2),
        ),
    )
    t = (
        1
        - 0.17 * np.cos(np.radians(hp_bar - 30))
        + 0.24 * np.cos(np.radians(2 * hp_bar))
        + 0.32 * np.cos(np.radians(3 * hp_bar + 6))
        - 0.20 * np.cos(np.radians(4 * hp_bar - 63))
    )
    d_theta = 30 * np.exp(-(((hp_bar - 275) / 25) ** 2))
    rc = 2 * np.sqrt(cp_bar**7 / (cp_bar**7 + 25**7))
    sl = 1 + (0.015 * (l_bar - 50) ** 2) / np.sqrt(20 + (l_bar - 50) ** 2)
    sc = 1 + 0.045 * cp_bar
    sh = 1 + 0.015 * cp_bar * t
    rt = -np.sin(np.radians(2 * d_theta)) * rc
    return np.sqrt(
        (dlp / sl) ** 2 + (dcp / sc) ** 2 + (dhp / sh) ** 2 + rt * (dcp / sc) * (dhp / sh)
    )


def test_grid(size: int = 9) -> np.ndarray:
    """Uniform RGB test grid (N^3 x 3) used for LUT verification."""
    axis = np.linspace(0.0, 1.0, size, dtype=np.float32)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    return np.stack([r, g, b], axis=-1).reshape(-1, 3)


def mccamy_cct(rgb_mean: np.ndarray) -> float:
    """Approximate correlated colour temperature (K) of a mean sRGB colour."""
    lin = srgb_to_linear(np.asarray(rgb_mean, dtype=np.float64))
    x_, y_, z_ = lin @ _M_RGB_TO_XYZ.T
    total = x_ + y_ + z_
    if total <= 1e-9:
        return 6500.0
    x, y = x_ / total, y_ / total
    n = (x - 0.3320) / (0.1858 - y) if abs(0.1858 - y) > 1e-9 else 0.0
    cct = 449 * n**3 + 3525 * n**2 + 6823.3 * n + 5520.33
    return float(np.clip(cct, 1500, 20000))


SKIN_HUE_RANGE = (18.0, 72.0)  # Lab hue angle band of the skin-tone line (degrees)
SKIN_LINE_HUE = 45.0


def skin_weight(lab: np.ndarray) -> np.ndarray:
    """Soft membership (0..1) of pixels in the skin-tone region of Lab space."""
    chroma, hue = chroma_hue(lab)
    lo, hi = SKIN_HUE_RANGE
    centre = (lo + hi) / 2
    half = (hi - lo) / 2
    hue_w = np.clip(1 - np.abs(hue - centre) / half, 0, 1)
    chroma_w = np.clip((chroma - 6) / 10, 0, 1) * np.clip((60 - chroma) / 15, 0, 1)
    light_w = np.clip((lab[..., 0] - 20) / 15, 0, 1) * np.clip((95 - lab[..., 0]) / 10, 0, 1)
    return hue_w * chroma_w * light_w
