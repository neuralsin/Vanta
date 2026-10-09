"""Correction capture (manual 18): explicit feedback + implicit diffs of the final timeline.

Implicit corrections compare what VANTA wrote (EDG + execution report item
map) with the timeline the user finished in Resolve. Grade changes cannot be
read as parameters, so they are measured: the clip's exported LUT is applied to
VANTA's own reference frames and the colour statistics are compared.
Every correction is classified and stored with a confidence in the inference.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from vanta.analysis.color import frame_stats
from vanta.core.schemas import EditDecisionGraph

KINDS = ("explicit_positive", "explicit_negative", "implicit_correction", "parameter_adjustment", "structural_correction")
FEEDBACK = {
    "thumbs_up": ("explicit_positive", None),
    "thumbs_down": ("explicit_negative", None),
    "too_much": ("explicit_negative", "intensity_down"),
    "too_little": ("explicit_negative", "intensity_up"),
    "wrong_shot": ("explicit_negative", "cut"),
    "wrong_colour": ("explicit_negative", "color"),
    "wrong_text": ("explicit_negative", "typography"),
    "wrong_effect": ("explicit_negative", "fusion"),
    "wrong_pacing": ("explicit_negative", "rhythm"),
}


@dataclass
class Correction:
    dimension: str  # cut | rhythm | color | typography | fusion | audio | masking
    kind: str
    description: str
    ai_value: Any = None
    user_value: Any = None
    target: str | None = None
    context: dict = field(default_factory=dict)
    confidence: float = 0.7
    signal: str | None = None  # normalised signal used for rule mining, e.g. "grade_too_aggressive"

    def to_dict(self) -> dict:
        return asdict(self)


def from_feedback(kind: str, *, target: str | None, dimension_hint: str | None, context: dict | None = None, note: str = "") -> Correction:
    category, detail = FEEDBACK.get(kind, ("explicit_negative", None))
    dimension = dimension_hint or {"intensity_down": "color", "intensity_up": "color"}.get(detail or "", detail or "general")
    signal = {
        "too_much": f"{dimension}_too_strong",
        "too_little": f"{dimension}_too_weak",
        "wrong_shot": "shot_rejected",
        "wrong_colour": "colour_rejected",
        "wrong_text": "text_rejected",
        "wrong_effect": "effect_rejected",
        "wrong_pacing": "pacing_rejected",
        "thumbs_up": f"{dimension}_accepted",
        "thumbs_down": f"{dimension}_rejected",
    }.get(kind)
    return Correction(dimension=dimension, kind=category, description=note or kind.replace("_", " "), target=target, context=context or {}, confidence=0.95, signal=signal)


def _grade_delta(our_lut: str | None, exported_lut: str | None, frames: list[np.ndarray]) -> dict[str, float] | None:
    from vanta.solvers.lut import read_cube

    if not our_lut or not exported_lut or not Path(our_lut).is_file() or not Path(exported_lut).is_file() or not frames:
        return None
    ours = read_cube(our_lut)
    theirs = read_cube(exported_lut)
    a = [frame_stats((np.clip(ours.apply(f.astype(np.float32) / 255), 0, 1) * 255).astype(np.uint8)) for f in frames]
    b = [frame_stats((np.clip(theirs.apply(f.astype(np.float32) / 255), 0, 1) * 255).astype(np.uint8)) for f in frames]
    return {
        "contrast": float(np.mean([y.contrast - x.contrast for x, y in zip(a, b, strict=True)])),
        "saturation": float(np.mean([y.chroma_mean - x.chroma_mean for x, y in zip(a, b, strict=True)])),
        "warmth": float(np.mean([(y.high_b + y.shadow_b) - (x.high_b + x.shadow_b) for x, y in zip(a, b, strict=True)]) / 2),
        "exposure": float(np.mean([y.luma_p50 - x.luma_p50 for x, y in zip(a, b, strict=True)])),
        "ai_contrast": float(np.mean([x.contrast for x in a])),
        "user_contrast": float(np.mean([y.contrast for y in b])),
    }


def diff_timeline(
    edg: EditDecisionGraph,
    item_ids: dict[str, str],
    final: dict,
    *,
    exported_luts: dict[str, str] | None = None,
    reference_frames: dict[str, list[np.ndarray]] | None = None,
) -> list[Correction]:
    """Compare VANTA's edit with the user's final Resolve timeline snapshot."""
    corrections: list[Correction] = []
    by_id = {it["item_id"]: it for it in final.get("items", [])}
    video_final = sorted((it for it in final.get("items", []) if it["track_type"] == "video" and it["track_index"] == 1), key=lambda it: it["start_frame"])
    ours = edg.ordered_shots(1)
    removed = []
    for shot in ours:
        item = by_id.get(item_ids.get(shot.id, ""))
        context = {"role": shot.role, "slot": shot.slot, "footage_shot": shot.footage_shot_id}
        if item is None:
            removed.append(shot)
            continue
        length = edg.timeline_duration(shot)
        if item["start_frame"] != shot.timeline_start_frame or item["duration"] != length:
            delta = item["duration"] - length
            corrections.append(
                Correction(
                    "rhythm",
                    "parameter_adjustment",
                    f"{shot.id} retimed by {delta:+d} frames",
                    ai_value=length,
                    user_value=item["duration"],
                    target=shot.id,
                    context=context,
                    confidence=0.85,
                    signal="shots_longer" if delta > 0 else "shots_shorter",
                )
            )
        if "source_start_frame" in item and item["source_start_frame"] != shot.source_in_frame:
            corrections.append(Correction("cut", "parameter_adjustment", f"{shot.id} slipped to source frame {item['source_start_frame']}", ai_value=shot.source_in_frame, user_value=item["source_start_frame"], target=shot.id, context=context, confidence=0.7, signal="slip"))
        # grade
        grade = edg.grade_for(shot.id)
        luts = item.get("luts") or {}
        if grade and grade.lut_path:
            current = luts.get("1")
            if not current:
                corrections.append(Correction("color", "structural_correction", f"grade removed from {shot.id}", target=shot.id, context=context, confidence=0.8, signal="grade_rejected"))
            elif Path(current).name != Path(grade.lut_path).name or (item.get("nodes", 1) > 1):
                delta = _grade_delta(grade.lut_path, (exported_luts or {}).get(shot.id), (reference_frames or {}).get(shot.id, []))
                signal = "grade_modified"
                desc = f"grade on {shot.id} modified"
                confidence = 0.6
                if delta:
                    if delta["contrast"] < -0.05 or delta["saturation"] < -3:
                        signal, confidence = "grade_too_aggressive", float(np.clip(0.6 + abs(delta["contrast"]) * 2, 0.6, 0.9))
                        desc = f"contrast {delta['ai_contrast']:.2f} -> {delta['user_contrast']:.2f}: grade too aggressive"
                    elif delta["contrast"] > 0.05 or delta["saturation"] > 3:
                        signal, confidence = "grade_too_weak", 0.7
                    if abs(delta["warmth"]) > 2:
                        corrections.append(Correction("color", "parameter_adjustment", f"warmth shifted {delta['warmth']:+.1f} b*", target=shot.id, context=context, confidence=0.7, signal="warmer" if delta["warmth"] > 0 else "cooler"))
                corrections.append(Correction("color", "parameter_adjustment", desc, ai_value=grade.params, user_value=delta, target=shot.id, context=context, confidence=confidence, signal=signal))
        # fusion comps removed
        comps = set(item.get("fusion_comps") or [])
        for layer in edg.texts:
            if layer.shot_id == shot.id and comps == set() and layer.template not in {"word_highlight", "caption_clean"}:
                corrections.append(Correction("typography", "structural_correction", f"title '{layer.text}' removed", target=layer.id, context={"template": layer.template, "y": layer.y}, confidence=0.6, signal="text_removed"))
    # replaced shots: a removed shot whose slot is now filled by different media
    for shot in removed:
        replacement = next((it for it in video_final if abs(it["start_frame"] - shot.timeline_start_frame) <= 2 and it["item_id"] not in item_ids.values()), None)
        if replacement:
            corrections.append(Correction("cut", "structural_correction", f"{shot.id} replaced with {Path(replacement.get('source_path') or '?').name}", ai_value=shot.footage_shot_id, user_value=replacement.get("source_path"), target=shot.id, context={"role": shot.role}, confidence=0.9, signal="shot_replaced"))
        else:
            corrections.append(Correction("cut", "structural_correction", f"{shot.id} removed", ai_value=shot.footage_shot_id, target=shot.id, context={"role": shot.role}, confidence=0.85, signal="shot_removed"))
    # pacing overall
    if video_final and ours:
        ai_med = float(np.median([edg.timeline_duration(s) for s in ours])) / edg.fps
        user_med = float(np.median([it["duration"] for it in video_final])) / edg.fps
        if abs(user_med - ai_med) / max(ai_med, 1e-3) > 0.15:
            corrections.append(Correction("rhythm", "parameter_adjustment", f"median shot {ai_med:.2f}s -> {user_med:.2f}s", ai_value=ai_med, user_value=user_med, confidence=0.8, signal="pacing_slower" if user_med > ai_med else "pacing_faster"))
    # user steering markers (no vanta customData)
    for frame, marker in (final.get("markers") or {}).items():
        if isinstance(marker, dict) and not str(marker.get("customData", "")).startswith("vanta:"):
            corrections.append(Correction("general", "implicit_correction", f"user marker '{marker.get('name')}' ({marker.get('color')}) at {frame}: {marker.get('note', '')}", target=str(frame), context={"color": marker.get("color"), "note": marker.get("note")}, confidence=0.9, signal=f"marker_{str(marker.get('color', '')).casefold()}"))
    return corrections


def correction_rate(edg: EditDecisionGraph, corrections: list[Correction]) -> dict[str, float]:
    """Manual 18 metric: how much did you have to fix, per dimension."""
    made = {"cut": len(edg.shots), "rhythm": len(edg.shots), "color": len(edg.grades), "typography": len(edg.texts), "fusion": len(edg.effects) + len(edg.transitions)}
    changed: dict[str, int] = {}
    for c in corrections:
        if c.kind != "explicit_positive":
            changed[c.dimension] = changed.get(c.dimension, 0) + 1
    return {dim: round(changed.get(dim, 0) / count, 3) for dim, count in made.items() if count}
