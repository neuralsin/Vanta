"""Parametric look model (manual 11.4) and CDL representation.

The look operates on display-referred Rec.709/sRGB-encoded floats, which is the
domain both reels and a Resolve Rec.709 timeline deliver. It is smooth and
monotone in luminance, so it bakes cleanly into a 33/65-point LUT.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

import numpy as np

from vanta.core.colormath import (
    REC709_LUMA,
    linear_to_srgb,
    rgb_to_lab,
    skin_weight,
    srgb_to_linear,
)


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _neutralise(tint: tuple[float, float, float] | np.ndarray) -> np.ndarray:
    vec = np.asarray(tint, dtype=np.float32)
    return vec - float(vec @ REC709_LUMA)


@dataclass
class LookParams:
    """Creative look parameters. Neutral values leave the image unchanged."""

    exposure: float = 0.0  # stops, applied in linear light
    contrast: float = 1.0  # slope around pivot on luma
    pivot: float = 0.435
    lift: float = 0.0  # black point raise (matte), 0..0.15
    gain: float = 1.0  # white point scale
    shadow_tint: tuple[float, float, float] = (0.0, 0.0, 0.0)
    highlight_tint: tuple[float, float, float] = (0.0, 0.0, 0.0)
    saturation: float = 1.0
    shadow_saturation: float = 1.0
    highlight_saturation: float = 1.0
    highlight_rolloff: float = 0.0  # 0..1 soft shoulder strength
    skin_protect: float = 0.0  # 0..1 attenuates tint/saturation on skin

    BOUNDS = {
        "exposure": (-2.0, 2.0),
        "contrast": (0.6, 1.6),
        "lift": (0.0, 0.15),
        "gain": (0.8, 1.15),
        "saturation": (0.0, 1.8),
        "shadow_saturation": (0.5, 1.5),
        "highlight_saturation": (0.5, 1.5),
        "highlight_rolloff": (0.0, 1.0),
        "skin_protect": (0.0, 1.0),
    }
    TINT_BOUND = 0.08

    VECTOR_FIELDS = (
        "exposure",
        "contrast",
        "lift",
        "gain",
        "saturation",
        "shadow_saturation",
        "highlight_saturation",
        "highlight_rolloff",
    )

    def to_vector(self) -> np.ndarray:
        values = [getattr(self, name) for name in self.VECTOR_FIELDS]
        values += list(self.shadow_tint) + list(self.highlight_tint)
        return np.asarray(values, dtype=np.float64)

    @classmethod
    def from_vector(cls, vector: np.ndarray, *, base: LookParams | None = None) -> LookParams:
        params = LookParams(**asdict(base)) if base else LookParams()
        count = len(cls.VECTOR_FIELDS)
        for name, value in zip(cls.VECTOR_FIELDS, vector[:count], strict=True):
            lo, hi = cls.BOUNDS[name]
            setattr(params, name, float(np.clip(value, lo, hi)))
        bound = cls.TINT_BOUND
        params.shadow_tint = tuple(float(np.clip(v, -bound, bound)) for v in vector[count : count + 3])
        params.highlight_tint = tuple(
            float(np.clip(v, -bound, bound)) for v in vector[count + 3 : count + 6]
        )
        return params

    def clamped(self) -> LookParams:
        return LookParams.from_vector(self.to_vector(), base=self)

    def to_dict(self) -> dict[str, object]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> LookParams:
        known = {item.name for item in fields(cls)}
        clean = {k: (tuple(v) if isinstance(v, list) else v) for k, v in payload.items() if k in known}
        return cls(**clean)

    def scaled(self, amount: float) -> LookParams:
        """Interpolate between neutral (0) and this look (1); used for 'less aggressive'."""
        neutral = LookParams(pivot=self.pivot, skin_protect=self.skin_protect)
        vector = neutral.to_vector() + (self.to_vector() - neutral.to_vector()) * amount
        return LookParams.from_vector(vector, base=self)

    def is_neutral(self, tol: float = 1e-4) -> bool:
        return bool(np.allclose(self.to_vector(), LookParams().to_vector(), atol=tol))


def _core(rgb: np.ndarray, p: LookParams, *, with_colour: bool) -> np.ndarray:
    x = rgb.astype(np.float32, copy=True)
    if p.exposure:
        x = linear_to_srgb(srgb_to_linear(x) * (2.0**p.exposure)).astype(np.float32)
    y = x @ REC709_LUMA.astype(np.float32)
    # tonal shape on luma, colour preserved additively
    y2 = p.pivot + (y - p.pivot) * p.contrast
    if p.highlight_rolloff > 0:
        knee = 1.0 - 0.35 * p.highlight_rolloff
        over = np.maximum(y2 - knee, 0.0)
        y2 = np.where(y2 > knee, knee + (1 - knee) * (1 - np.exp(-over / max(1e-3, 1 - knee))), y2)
    y2 = p.lift + y2 * (p.gain - p.lift)
    x = x + (y2 - y)[..., None]
    if with_colour:
        w_s = (1.0 - _smoothstep(0.0, 0.55, y2))[..., None]
        w_h = _smoothstep(0.45, 1.0, y2)[..., None]
        x = x + w_s * _neutralise(p.shadow_tint) + w_h * _neutralise(p.highlight_tint)
        y3 = x @ REC709_LUMA.astype(np.float32)
        sat = (
            p.saturation
            * (1 + (p.shadow_saturation - 1) * (1 - _smoothstep(0.0, 0.5, y3)))
            * (1 + (p.highlight_saturation - 1) * _smoothstep(0.5, 1.0, y3))
        )
        x = y3[..., None] + (x - y3[..., None]) * sat[..., None]
    return np.clip(x, 0.0, 1.0)


def apply_look(rgb: np.ndarray, params: LookParams) -> np.ndarray:
    """Apply ``params`` to gamma-encoded float RGB (any leading shape, last axis RGB)."""
    graded = _core(rgb, params, with_colour=True)
    if params.skin_protect > 0:
        protected = _core(rgb, params, with_colour=False)
        weight = skin_weight(rgb_to_lab(np.clip(rgb, 0, 1))) * params.skin_protect
        graded = graded + (protected - graded) * weight[..., None]
    return graded


@dataclass
class CDL:
    """ASC CDL for the TECH node. Write-only in Resolve: verify by exported LUT."""

    slope: tuple[float, float, float] = (1.0, 1.0, 1.0)
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    power: tuple[float, float, float] = (1.0, 1.0, 1.0)
    saturation: float = 1.0
    node_index: int = 1
    meta: dict[str, float] = field(default_factory=dict)

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        x = np.clip(rgb * np.asarray(self.slope) + np.asarray(self.offset), 0.0, 1.0)
        x = np.power(x, np.asarray(self.power))
        y = x @ REC709_LUMA
        x = y[..., None] + (x - y[..., None]) * self.saturation
        return np.clip(x, 0.0, 1.0).astype(np.float32)

    def to_resolve(self) -> dict[str, object]:
        def fmt(values: tuple[float, float, float]) -> str:
            return " ".join(f"{v:.5f}" for v in values)

        return {
            "NodeIndex": str(self.node_index),
            "Slope": fmt(self.slope),
            "Offset": fmt(self.offset),
            "Power": fmt(self.power),
            "Saturation": f"{self.saturation:.5f}",
        }

    def to_xml(self, cdl_id: str) -> str:
        def fmt(values: tuple[float, float, float]) -> str:
            return " ".join(f"{v:.6f}" for v in values)

        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ColorDecisionList xmlns="urn:ASC:CDL:v1.01">\n'
            f'  <ColorDecision><ColorCorrection id="{cdl_id}">\n'
            f"    <SOPNode><Slope>{fmt(self.slope)}</Slope><Offset>{fmt(self.offset)}</Offset>"
            f"<Power>{fmt(self.power)}</Power></SOPNode>\n"
            f"    <SatNode><Saturation>{self.saturation:.6f}</Saturation></SatNode>\n"
            "  </ColorCorrection></ColorDecision>\n</ColorDecisionList>\n"
        )

    def is_identity(self) -> bool:
        return (
            np.allclose(self.slope, 1)
            and np.allclose(self.offset, 0)
            and np.allclose(self.power, 1)
            and abs(self.saturation - 1) < 1e-6
        )
