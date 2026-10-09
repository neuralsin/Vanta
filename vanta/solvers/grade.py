"""Grade solver (manual 11): intent -> relational target statistics -> parameters.

Why not LUT transfer: a night-cyan reference applied to a daylight beach would
wreck it. The solver keeps each shot's physical lighting (exposure, white
balance) and transfers *relationships*: tonal contrast, black depth, the
warm/cool split between highlights and shadows, and relative saturation.
Parameters come from a seeded Nelder-Mead search on downsampled frames,
minimising distance to the target ranges plus clipping, skin and noise penalties.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from vanta.analysis.color import ColorStats, frame_stats, look_distance
from vanta.core.colormath import (
    REC709_LUMA,
    SKIN_LINE_HUE,
    chroma_hue,
    rgb_to_lab,
    skin_weight,
    to_float,
)
from vanta.solvers.grade_math import CDL, LookParams, apply_look
from vanta.solvers.lut import Cube, read_cube, transform_error, write_cube

SOLVER_VERSION = "grade-v3"
NODE_LABELS = ("TECH", "BALANCE", "TONE", "LOOK", "SEP", "BG", "SKIN", "POLISH")

# how strongly each statistic follows the reference vs the shot's own physics
TRANSFER_WEIGHT = {
    "luma_p50": 0.25,  # exposure: mostly keep the shot's own lighting
    "contrast": 0.85,
    "black_point": 0.85,
    "separation": 0.9,  # high_b - shadow_b relationship
    "shadow_a": 0.6,
    "chroma_ratio": 0.8,
}


@dataclass
class GradeIntent:
    """Director's relational intent, or a deterministic default derived from the style."""

    text: str = ""
    warmth: str = "neutral"  # cooler | neutral | warmer
    contrast: str = "keep"  # softer | keep | punchier
    saturation: str = "keep"  # muted | keep | richer
    separation: str = "subtle"  # none | subtle | strong
    protect_skin: bool = True
    deepen_blacks: bool = False


@dataclass
class GradeSolution:
    params: LookParams
    cdl: CDL
    target: dict[str, float]
    predicted: dict[str, float]
    source: dict[str, float]
    loss: float
    confidence: float
    rationale: list[str] = field(default_factory=list)
    node_enabled: dict[str, bool] = field(default_factory=dict)
    input_profile: str = "rec709"

    def transform(self, rgb: np.ndarray) -> np.ndarray:
        """Full intended transform (camera decode -> TECH CDL -> LOOK) for LUT baking and verification."""
        from vanta.core.camera_log import decode_to_display

        return apply_look(self.cdl.apply(decode_to_display(rgb, self.input_profile)), self.params)


def _stats_dict(stats: ColorStats) -> dict[str, float]:
    d = {k: float(getattr(stats, k)) for k in ("luma_p05", "luma_p50", "luma_p95", "contrast", "black_point", "shadow_a", "shadow_b", "mid_a", "mid_b", "high_a", "high_b", "chroma_mean", "clip_low", "clip_high")}
    d["separation"] = d["high_b"] - d["shadow_b"]
    d["skin_hue"] = stats.skin_hue if stats.skin_hue is not None else float("nan")
    return d


def tech_cdl(source: dict[str, float], *, match_luma: float | None = None, neutralise: float = 0.5) -> tuple[CDL, list[str]]:
    """TECH node: per-shot exposure normalisation and partial white-balance correction."""
    notes = []
    slope = np.ones(3)
    if match_luma is not None and source["luma_p50"] > 0.02:
        gain = float(np.clip(match_luma / source["luma_p50"], 0.7, 1.4))
        if abs(gain - 1) > 0.03:
            slope *= gain
            notes.append(f"exposure normalised x{gain:.2f} toward shot-matched mid grey")
    cast_b = source["mid_b"]
    cast_a = source["mid_a"]
    if abs(cast_b) > 8 or abs(cast_a) > 6:
        # remove part of a strong cast; intentional warmth is restored by the LOOK node
        rb = 1 - neutralise * np.clip(cast_b / 80, -0.12, 0.12)
        ga = 1 - neutralise * np.clip(cast_a / 120, -0.06, 0.06)
        slope = slope * np.array([1 / rb ** 0.5, ga, rb])
        notes.append(f"neutralised {int(neutralise * 100)}% of a colour cast (a* {cast_a:.1f}, b* {cast_b:.1f})")
    return CDL(slope=tuple(float(v) for v in slope)), notes


def relational_target(
    source: dict[str, float],
    reference: dict[str, float],
    *,
    intent: GradeIntent,
    intensity: float,
    rule_effects: list[dict] | None = None,
    contrast_bias: float = 0.5,
    saturation_bias: float = 0.5,
) -> tuple[dict[str, float], list[str]]:
    """Target statistics that keep the shot's physics and borrow the reference's relationships."""
    notes = []
    t: dict[str, float] = {}
    k = float(np.clip(intensity, 0, 1))

    def blend(key: str, ref_value: float | None) -> float:
        if ref_value is None or np.isnan(ref_value):
            return source[key]
        return source[key] + k * TRANSFER_WEIGHT.get(key, 0.7) * (ref_value - source[key])

    t["luma_p50"] = blend("luma_p50", reference.get("luma_p50"))
    t["contrast"] = blend("contrast", reference.get("contrast"))
    t["black_point"] = blend("black_point", reference.get("black_point"))
    ref_sep = (reference.get("high_b", 0.0) - reference.get("shadow_b", 0.0)) if reference else 0.0
    t["separation"] = source["separation"] + k * TRANSFER_WEIGHT["separation"] * (ref_sep - source["separation"])
    t["shadow_a"] = blend("shadow_a", reference.get("shadow_a"))
    # relative saturation: reference chroma relative to a typical 22 C*
    ratio = (reference.get("chroma_mean", 22.0) / 22.0) if reference else 1.0
    t["chroma_mean"] = source["chroma_mean"] * (1 + k * TRANSFER_WEIGHT["chroma_ratio"] * (ratio - 1))
    # intent adjustments (relational words from the director)
    if intent.contrast == "punchier":
        t["contrast"] *= 1 + 0.12 * k
    elif intent.contrast == "softer":
        t["contrast"] *= 1 - 0.12 * k
    if intent.saturation == "richer":
        t["chroma_mean"] *= 1 + 0.15 * k
    elif intent.saturation == "muted":
        t["chroma_mean"] *= 1 - 0.2 * k
    if intent.separation == "strong":
        t["separation"] += 3.0 * k * np.sign(t["separation"] or 1)
    elif intent.separation == "none":
        t["separation"] = source["separation"]
    if intent.deepen_blacks:
        t["black_point"] = min(t["black_point"], source["black_point"] * 0.6)
    mean_b_shift = {"cooler": -3.0, "warmer": 3.0}.get(intent.warmth, 0.0) * k
    t["mean_b"] = (source["high_b"] + source["shadow_b"]) / 2 + mean_b_shift
    # slider biases (0.5 = neutral)
    t["contrast"] *= 1 + (contrast_bias - 0.5) * 0.3
    t["chroma_mean"] *= 1 + (saturation_bias - 0.5) * 0.4
    # rule effects from the Style Brain (conditional rules matched by code)
    for effect in rule_effects or []:
        for key, value in effect.items():
            if key in {"shadow_b", "high_b"} and isinstance(value, (int, float)):
                base = effect.get("baseline")
                if isinstance(base, (int, float)):
                    delta = (value - base) * k * 0.6
                    t["separation"] += delta if key == "high_b" else -delta
                    notes.append(f"rule: {key} {'+' if delta >= 0 else ''}{delta:.1f}")
            elif key in {"contrast", "black_point", "chroma_mean", "luma_p50"} and isinstance(value, (int, float)):
                t[key] = t[key] + (value - t[key]) * 0.5 * k
                notes.append(f"rule: {key} toward {value:.3g}")
    t["contrast"] = float(np.clip(t["contrast"], 0.25, 0.95))
    t["black_point"] = float(np.clip(t["black_point"], 0.0, 0.12))
    t["chroma_mean"] = float(np.clip(t["chroma_mean"], 2.0, 60.0))
    t["luma_p50"] = float(np.clip(t["luma_p50"], 0.08, 0.8))
    return t, notes


def _measure(frames: list[np.ndarray]) -> dict[str, float]:
    stats = [frame_stats(f) for f in frames]
    keys = _stats_dict(stats[0]).keys()
    out = {}
    for k in keys:
        values = [v for v in (_stats_dict(s)[k] for s in stats) if v is not None and not np.isnan(v)]
        out[k] = float(np.median(values)) if values else float("nan")
    return out


def _pool(frames: list[np.ndarray], n: int = 6000, seed: int = 5) -> np.ndarray:
    """Stratified pixel sample pooled across frames (objective evaluation in ~1 ms)."""
    rng = np.random.default_rng(seed)
    pixels = np.concatenate([f.reshape(-1, 3) for f in frames])
    if len(pixels) > n:
        pixels = pixels[rng.choice(len(pixels), n, replace=False)]
    return pixels.astype(np.float32)


def _fast_stats(pixels: np.ndarray) -> dict[str, float]:
    """The subset of colour statistics the objective needs, vectorised on a pixel pool."""
    y = pixels @ REC709_LUMA.astype(np.float32)
    lab = rgb_to_lab(np.clip(pixels, 0, 1))
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    p01, p05, p50, p95 = np.percentile(y, [1, 5, 50, 95])
    shadows, highs = y < 0.25, y >= 0.7

    def band(values: np.ndarray, mask: np.ndarray) -> float:
        return float(values[mask].mean()) if mask.sum() > 30 else 0.0

    shadow_b, high_b = band(lab[:, 2], shadows), band(lab[:, 2], highs)
    return {
        "luma_p50": float(p50),
        "contrast": float(p95 - p05),
        "black_point": float(p01),
        "shadow_a": band(lab[:, 1], shadows),
        "shadow_b": shadow_b,
        "high_b": high_b,
        "separation": high_b - shadow_b,
        "mean_b": (high_b + shadow_b) / 2,
        "chroma_mean": float(chroma.mean()),
        "clip": float(np.mean(y <= 2 / 255) + np.mean(np.max(pixels, axis=1) >= 253 / 255)),
    }


def _skin_deviation(frames: list[np.ndarray]) -> float:
    devs = []
    for f in frames:
        lab = rgb_to_lab(f)
        w = skin_weight(lab)
        if w.sum() < 50:
            continue
        _, hue = chroma_hue(lab)
        devs.append(float(np.average(np.abs(((hue - SKIN_LINE_HUE + 180) % 360) - 180), weights=w)))
    return float(np.mean(devs)) if devs else 0.0


def _downsample(rgb: np.ndarray, long_edge: int = 192) -> np.ndarray:
    import cv2

    x = to_float(rgb)
    h, w = x.shape[:2]
    s = long_edge / max(h, w)
    if s < 1:
        x = cv2.resize(x, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return x


def solve_grade(
    frames: list[np.ndarray],
    reference_look: dict[str, float],
    *,
    intent: GradeIntent | None = None,
    intensity: float = 0.6,
    skin_priority: float = 0.8,
    contrast_bias: float = 0.5,
    saturation_bias: float = 0.5,
    match_luma: float | None = None,
    rule_effects: list[dict] | None = None,
    seed: int = 11,
    max_iter: int = 260,
    input_profile: str = "rec709",
) -> GradeSolution:
    from vanta.core.camera_log import decode_to_display

    if not frames:
        raise ValueError("grade solver needs at least one frame")
    intent = intent or GradeIntent()
    # camera log / HDR is decoded first: every statistic below is display-referred
    small = [decode_to_display(_downsample(f), input_profile) for f in frames[:4]]
    source = _measure(small)
    cdl, notes = tech_cdl(source, match_luma=match_luma)
    teched = [cdl.apply(f) for f in small]
    tech_stats = _measure(teched)
    target, rule_notes = relational_target(
        tech_stats,
        reference_look,
        intent=intent,
        intensity=intensity,
        rule_effects=rule_effects,
        contrast_bias=contrast_bias,
        saturation_bias=saturation_bias,
    )
    base_skin = _skin_deviation(teched)
    base_clip = _fast_stats(_pool(teched))["clip"]
    skin_protect = float(np.clip(skin_priority, 0, 1)) if intent.protect_skin else 0.0
    rng = np.random.default_rng(seed)
    scales = {"luma_p50": 0.03, "contrast": 0.03, "black_point": 0.01, "separation": 1.5, "shadow_a": 1.5, "chroma_mean": 2.0, "mean_b": 1.5}

    pool = _pool(teched)
    skin_pool = pool[skin_weight(rgb_to_lab(pool)) > 0.5]

    def objective(vector: np.ndarray) -> float:
        params = LookParams.from_vector(vector)
        params.skin_protect = skin_protect
        graded = apply_look(pool, params)
        m = _fast_stats(graded)
        err = sum(((m[key] - target[key]) / scales[key]) ** 2 for key in scales if key in target)
        clip = max(0.0, m["clip"] - base_clip - 0.002) * 400
        skin = 0.0
        if len(skin_pool) > 40:
            _, hue = chroma_hue(rgb_to_lab(apply_look(skin_pool, params)))
            deviation = float(np.mean(np.abs(((hue - SKIN_LINE_HUE + 180) % 360) - 180)))
            skin = max(0.0, deviation - base_skin - 2.0) * skin_priority * 0.6
        noise = max(0.0, params.lift - 0.06) * 30 * max(0.0, 0.2 - tech_stats["luma_p05"])
        reg = 0.02 * float(np.sum((vector - LookParams().to_vector()) ** 2))
        return float(err + clip + skin + noise + reg)

    start = LookParams().to_vector()
    initial_loss = objective(start)
    best_x, best_f = start, initial_loss
    for attempt in range(2):
        x0 = start if attempt == 0 else start + rng.normal(0, 0.03, size=start.shape)
        result = minimize(objective, x0, method="Nelder-Mead", options={"maxiter": max_iter, "xatol": 1e-3, "fatol": 1e-3, "adaptive": True})
        if result.fun < best_f:
            best_x, best_f = result.x, float(result.fun)
    params = LookParams.from_vector(best_x)
    params.skin_protect = skin_protect
    graded = [apply_look(f, params) for f in teched]
    predicted = _measure(graded)
    predicted["mean_b"] = (predicted["high_b"] + predicted["shadow_b"]) / 2
    final = _fast_stats(apply_look(pool, params))
    tolerance = {"luma_p50": 3, "contrast": 3, "black_point": 3, "separation": 3, "shadow_a": 3, "chroma_mean": 3, "mean_b": 3}
    within = float(np.mean([abs(final[k] - target[k]) / scales[k] <= tolerance[k] for k in scales if k in target]))
    improvement = float(np.clip(1 - best_f / max(initial_loss, 1e-6), 0, 1)) if initial_loss > 1 else 1.0
    confidence = float(np.clip(0.55 + 0.27 * within + 0.13 * improvement, 0.45, 0.95))
    rationale = notes + rule_notes
    rationale.append(f"{within * 100:.0f}% of target statistics within tolerance; loss reduced {improvement * 100:.0f}%")
    rationale.append(
        "contrast {:.2f}->{:.2f}, separation Δb* {:.1f}->{:.1f}, chroma {:.1f}->{:.1f}".format(
            tech_stats["contrast"], predicted["contrast"], tech_stats["separation"], predicted["separation"], tech_stats["chroma_mean"], predicted["chroma_mean"]
        )
    )
    node_enabled = {
        "TECH": not cdl.is_identity(),
        "BALANCE": bool(np.any(np.abs(params.shadow_tint) > 1e-3) or np.any(np.abs(params.highlight_tint) > 1e-3)),
        "TONE": abs(params.contrast - 1) > 0.02 or params.lift > 0.005,
        "LOOK": not params.is_neutral(),
        "SEP": False,
        "BG": False,
        "SKIN": skin_protect > 0.3 and base_skin > 0,
        "POLISH": False,
    }
    return GradeSolution(
        params=params,
        cdl=cdl,
        target={k: round(v, 4) for k, v in target.items()},
        predicted={k: round(v, 4) for k, v in predicted.items() if not np.isnan(v)},
        source={k: round(v, 4) for k, v in source.items() if not np.isnan(v)},
        loss=round(best_f, 4),
        confidence=round(confidence, 3),
        rationale=rationale,
        node_enabled=node_enabled,
        input_profile=input_profile,
    )


def lut_size_for(profile: str) -> int:
    """65-point for camera log/HDR (steep decode curves), 33-point for display-referred clips."""
    return 33 if profile == "rec709" else 65


def write_shot_luts(solution: GradeSolution, folder: Path, shot_id: str, *, size: int | None = None) -> dict[str, str]:
    """Write the baked full transform (lut_only route) and the LOOK-only LUT (DRX route)."""
    size = size or lut_size_for(solution.input_profile)
    folder.mkdir(parents=True, exist_ok=True)
    full = write_cube(folder / f"{shot_id}_full.cube", solution.transform, size=size, title=f"VANTA {shot_id} full")
    look = write_cube(folder / f"{shot_id}_look.cube", lambda x: apply_look(x, solution.params), size=size, title=f"VANTA {shot_id} look")
    cdl_path = folder / f"{shot_id}.cdl"
    cdl_path.write_text(solution.cdl.to_xml(shot_id), encoding="utf-8")
    paths = {"full": str(full), "look": str(look), "cdl": str(cdl_path)}
    if solution.input_profile != "rec709":
        from vanta.core.camera_log import decode_to_display

        paths["decode"] = str(write_cube(folder / f"{shot_id}_decode.cube", lambda x: decode_to_display(x, solution.input_profile), size=65, title=f"VANTA {shot_id} decode {solution.input_profile}"))
    return paths


def verify_exported_lut(exported_cube: str | Path, intended: GradeSolution | Cube, *, threshold_de: float = 1.0) -> dict[str, float | bool]:
    """Manual 11.5: compare Resolve's exported clip LUT to the intended transform (< 1 dE)."""
    cube = read_cube(exported_cube)
    fn = intended.apply if isinstance(intended, Cube) else intended.transform
    err = transform_error(cube.apply, fn)
    return {**err, "passed": err["mean_de"] < threshold_de}


def grade_qc(solutions: dict[str, GradeSolution]) -> list[dict]:
    """Manual 11.7: target distance, clipping, skin deviation, shot-to-shot consistency."""
    findings = []
    items = list(solutions.items())
    for shot_id, sol in items:
        clip_delta = sol.predicted.get("clip_low", 0) + sol.predicted.get("clip_high", 0) - sol.source.get("clip_low", 0) - sol.source.get("clip_high", 0)
        if clip_delta > 0.01:
            findings.append({"shot_id": shot_id, "kind": "clipping", "severity": min(1.0, clip_delta * 20), "description": f"grade clips {clip_delta * 100:.1f}% more pixels"})
        if sol.loss > 6:
            findings.append({"shot_id": shot_id, "kind": "target_distance", "severity": min(1.0, sol.loss / 20), "description": f"look could not reach the target (loss {sol.loss:.1f})"})
    for (a_id, a), (b_id, b) in zip(items, items[1:], strict=False):
        jump = abs(a.predicted.get("luma_p50", 0) - b.predicted.get("luma_p50", 0)) + abs(a.predicted.get("separation", 0) - b.predicted.get("separation", 0)) / 20
        if jump > 0.18:
            findings.append({"shot_id": b_id, "kind": "consistency", "severity": min(1.0, jump), "description": f"look jumps between {a_id} and {b_id}"})
    return findings


def scaled_solution(solution: GradeSolution, amount: float) -> GradeSolution:
    """Scoped patch support: 'make the grade 15% less aggressive' scales only the LOOK."""
    return GradeSolution(
        params=solution.params.scaled(amount),
        cdl=solution.cdl,
        target=solution.target,
        predicted=solution.predicted,
        source=solution.source,
        loss=solution.loss,
        confidence=solution.confidence,
        rationale=solution.rationale + [f"look intensity scaled to {amount * 100:.0f}% by patch"],
        node_enabled=solution.node_enabled,
        input_profile=solution.input_profile,
    )


def luma_mid(frames: list[np.ndarray]) -> float:
    return float(np.median([np.median(to_float(_downsample(f)) @ REC709_LUMA) for f in frames]))


__all__ = ["GradeIntent", "GradeSolution", "NODE_LABELS", "lut_size_for", "solve_grade", "write_shot_luts", "verify_exported_lut", "grade_qc", "scaled_solution", "look_distance"]
