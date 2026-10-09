"""Effects are tools, not fetishes (manual 9.4): every transition, effect, ramp and 3D
choice needs a purpose, matching conditions and a registered template; the
user's sliders, allow-list and ``never`` list gate each one.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np

from vanta.core.schemas import EditSpec, EffectLayer, Provenance, SpeedRamp, Transition
from vanta.reasoning.director import seeded_keys
from vanta.solvers.cut import Placement
from vanta.style.brain import StylePackage


@dataclass
class EffectsPlan:
    transitions: list[Transition] = field(default_factory=list)
    effects: list[EffectLayer] = field(default_factory=list)
    ramps: dict[str, SpeedRamp] = field(default_factory=dict)  # edg shot id -> ramp
    notes: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    matte_shots: dict[str, str] = field(default_factory=dict)  # effect id -> edg shot id needing a subject matte


def _share(pkg: StylePackage, *names: str) -> float:
    shares = {item["effect"]: item["share"] for item in (pkg.fusion or {}).get("top", [])}
    return max((shares.get(n, 0.0) for n in names), default=0.0)


def _allowed(name: str, spec: EditSpec, registry: set[str]) -> bool:
    if spec.allowed_effects is not None and name not in spec.allowed_effects:
        return False
    return name in registry or name.startswith("resolve:")


def _violates_never(name: str, spec: EditSpec, a: Placement | None, b: Placement | None) -> str | None:
    for rule in spec.never:
        text = rule.casefold()
        if "zoom" in text and "static" in text and name in {"zoom_punch"}:
            if (a and a.candidate.camera_move == "static") or (b and b.candidate.camera_move == "static"):
                return rule
        if "glow" in text and name in {"subject_glow", "cinematic_bloom"}:
            return rule
        if ("blur" in text and name == "speed_blur") or ("freeze" in text and name == "freeze_frame"):
            return rule
        if ("letterbox" in text or "bars" in text) and name == "letterbox":
            return rule
        if ("outline" in text and name == "subject_outline") or ("parallax" in text and name == "parallax"):
            return rule
        if "3d" in text and name.startswith("text3d"):
            return rule
        if "shake" in text and name == "camera_shake":
            return rule
        if "glitch" in text and name == "glitch":
            return rule
        if "flash" in text and name == "flash_transition":
            return rule
        if "transition" in text and "no" in text.split():
            return rule
    return None


def plan_effects(
    spec: EditSpec,
    pkg: StylePackage,
    placements: list[Placement],
    shot_ids: list[str],
    *,
    fps: float,
    registry: set[str],
    director_effects: list[dict] | None = None,
    landscape: bool = False,
    shot_records: dict[str, dict] | None = None,
) -> EffectsPlan:
    plan = EffectsPlan()
    shot_records = shot_records or {}
    goal = spec.goal.casefold()
    duration_min = max(spec.duration_s / 60, 1e-3)
    rhythm = pkg.rhythm or {}
    tpm = rhythm.get("transitions_per_min") or {}
    motion_fx = spec.sliders.motion_effects
    budget = int(round(sum(tpm.get(k, 0) for k in ("flash", "whip", "zoom", "glitch", "dissolve", "fade_black", "fade_white")) * duration_min * (0.5 + motion_fx)))
    budget = max(budget, 1 if motion_fx >= 0.6 else 0)
    used = 0
    # director transition wishes: (slot of the incoming shot) -> (template, purpose, confidence)
    wishes = {
        int(p["slot"]): (p["template"], str(p.get("purpose") or ""), float(p.get("confidence", 0.0)))
        for p in director_effects or []
        if isinstance(p.get("slot"), int) and p.get("template") in TRANSITION_TEMPLATES - {"hard_cut"} and p.get("purpose") and float(p.get("confidence", 0)) >= 0.5
    }
    for i in range(len(placements) - 1):
        a, b = placements[i], placements[i + 1]
        cut_slot = b.slot
        options: list[tuple[float, str, str]] = []
        if (cut_slot.is_drop or cut_slot.beat_kind == "downbeat") and motion_fx >= 0.4 and _share(pkg, "glitch") >= 0.25:
            options.append((_share(pkg, "glitch"), "glitch", "digital energy accent the references use"))
        if i + 1 in wishes:
            name, why, conf = wishes[i + 1]
            options.append((0.35 + 0.3 * conf, name, why[:160]))
        if cut_slot.is_drop and motion_fx >= 0.3:
            options.append((_share(pkg, "zoom", "zoom_transition", "punch_in") + 0.1, "zoom_punch", "impact on the drop"))
            options.append((_share(pkg, "flash"), "flash_transition", "punctuate the drop"))
        if a.candidate.camera_move.startswith("pan") and b.candidate.camera_move.startswith("pan") and motion_fx >= 0.4:
            options.append((_share(pkg, "whip") + 0.1, "whip_pan", "carry the pan direction through the cut"))
        if cut_slot.section in {"breakdown", "outro"} and tpm.get("dissolve", 0) > 0.5:
            options.append((0.6, "cross_dissolve", "soften the energy shift"))
        if cut_slot.role == "outro" and tpm.get("fade_black", 0) > 0:
            options.append((0.5, "dip_to_black", "close the story"))
        options = [o for o in options if o[0] >= 0.25]
        if not options or used >= budget:
            continue
        score, name, purpose = max(options)
        template = None if name in {"cross_dissolve", "dip_to_black"} else name
        if template and not _allowed(template, spec, registry):
            plan.rejected.append({"effect": name, "at": shot_ids[i + 1], "reason": "not allowed by spec/registry"})
            continue
        bad = _violates_never(name, spec, a, b)
        if bad:
            plan.rejected.append({"effect": name, "at": shot_ids[i + 1], "reason": f"never: {bad}"})
            continue
        frames = 10 if name in {"cross_dissolve", "dip_to_black"} else 6
        plan.transitions.append(
            Transition(
                from_shot=shot_ids[i],
                to_shot=shot_ids[i + 1],
                kind={"zoom_punch": "zoom_punch", "flash_transition": "flash", "whip_pan": "whip", "glitch": "glitch"}.get(name, name),
                duration_frames=frames,
                route="resolve_api" if template is None else "fusion_template",
                template=template,
                purpose=purpose,
                confidence=round(min(0.92, 0.55 + score * 0.5), 3),
                provenance=Provenance(source="solver", solver="effects-v2", note=f"style share {score:.2f}"),
            )
        )
        used += 1
    # speed ramps on drops (purpose: beat impact), only when the style uses them
    if spec.sliders.speed_ramps >= 0.3 and (pkg.motion or {}).get("speed_events_per_min", 0) > 0.4:
        for pl, sid in zip(placements, shot_ids, strict=True):
            if pl.slot.is_drop and pl.candidate.motion_intensity > 0.45 and pl.slot.duration_s >= 0.8:
                plan.ramps[sid] = SpeedRamp(curve=[(0.0, 100.0), (0.3, 45.0), (0.7, 45.0), (1.0, 100.0)], purpose="beat_impact", route="prerender")
                plan.notes.append(f"{sid}: 100->45->100 speed ramp to land the drop")
    # bloom on night shots with practical lights
    if spec.sliders.glow >= 0.3 and "cinematic_bloom" in registry and _allowed("cinematic_bloom", spec, registry):
        for pl, sid in zip(placements, shot_ids, strict=True):
            if pl.candidate.lighting in {"night", "neon"} and not _violates_never("cinematic_bloom", spec, pl, None):
                plan.effects.append(
                    EffectLayer(
                        template="cinematic_bloom",
                        shot_id=sid,
                        start_frame=pl.slot.start_frame,
                        end_frame=pl.slot.end_frame,
                        params={"strength": round(0.2 + 0.3 * spec.sliders.glow, 3)},
                        purpose="emphasise practical lights at night",
                        confidence=0.72,
                        provenance=Provenance(source="solver", solver="effects-v2"),
                    )
                )
    # impact shake on the drop
    if motion_fx >= 0.6 and _share(pkg, "shake") >= 0.25 and "camera_shake" in registry:
        for pl, sid in zip(placements, shot_ids, strict=True):
            if pl.slot.is_drop and not _violates_never("camera_shake", spec, pl, None):
                length = min(pl.slot.frames, int(0.5 * fps))
                plan.effects.append(
                    EffectLayer(
                        template="camera_shake",
                        shot_id=sid,
                        start_frame=pl.slot.start_frame,
                        end_frame=pl.slot.start_frame + length,
                        params={"keys": seeded_keys(zlib.crc32(sid.encode()) % 10_000, 0, length, amplitude=0.01, every=2)},
                        purpose="impact on the drop",
                        confidence=0.66,
                        provenance=Provenance(source="solver", solver="effects-v2"),
                    )
                )
    def add(template: str, index: int, purpose: str, confidence: float, provenance: Provenance) -> bool:
        pl, sid = placements[index], shot_ids[index]
        if template not in registry or not _allowed(template, spec, registry):
            plan.rejected.append({"effect": template, "at": sid, "reason": "not allowed by spec/registry"})
            return False
        bad = _violates_never(template, spec, pl, None)
        if bad:
            plan.rejected.append({"effect": template, "at": sid, "reason": f"never: {bad}"})
            return False
        on_shot = [e for e in plan.effects if e.shot_id == sid and e.template != "letterbox"]
        if any(e.template == template and e.shot_id == sid for e in plan.effects):
            return False
        if template != "letterbox" and len(on_shot) >= 2:
            plan.rejected.append({"effect": template, "at": sid, "reason": "shot already carries two picture effects"})
            return False
        if template in MATTE_EFFECTS and sid in plan.ramps:
            plan.rejected.append({"effect": template, "at": sid, "reason": "a subject matte cannot follow a speed-ramped pre-render"})
            return False
        params, why_not = _effect_params(template, pl, spec, landscape=landscape, shot=shot_records.get(pl.candidate.shot_id) or {}, fps=fps)
        if why_not:
            plan.rejected.append({"effect": template, "at": sid, "reason": why_not})
            return False
        effect = EffectLayer(
            template=template,
            shot_id=sid,
            start_frame=pl.slot.start_frame,
            end_frame=pl.slot.end_frame,
            params=params,
            purpose=purpose[:160],
            confidence=round(float(confidence), 3),
            provenance=provenance,
        )
        plan.effects.append(effect)
        if template in MATTE_EFFECTS:
            plan.matte_shots[effect.id] = sid
        return True

    solver = Provenance(source="solver", solver="effects-v3")
    # directional blur on the fastest drop shot
    if motion_fx >= 0.6 and _share(pkg, "blur", "speed_blur", "motion_blur") >= 0.2:
        drops = [i for i, pl in enumerate(placements) if pl.slot.is_drop and pl.candidate.motion_intensity >= 0.65]
        if drops:
            add("speed_blur", max(drops, key=lambda i: placements[i].candidate.motion_intensity), "smooth the fastest motion on the drop", 0.64, solver)
    # one freeze on the peak moment, only when the references do it
    if _share(pkg, "freeze", "freeze_frame") >= 0.2:
        peaks = [i for i, pl in enumerate(placements) if pl.slot.is_drop and pl.slot.frames >= 14]
        if peaks:
            add("freeze_frame", peaks[0], "hold the peak moment on the drop", 0.62, solver)
    # hero subject glow at night (needs a subject matte)
    if spec.sliders.glow >= 0.5 and spec.sliders.masking >= 0.3:
        heroes = [i for i, pl in enumerate(placements) if pl.candidate.lighting in {"night", "neon"} and _has_subject(pl, shot_records)]
        if heroes:
            best = max(heroes, key=lambda i: placements[i].candidate.quality)
            add("subject_glow", best, "make the hero subject radiate against the night", 0.62, solver)
    # slow push on locked-off shots: life without fake camera shake
    if motion_fx >= 0.35 and _share(pkg, "zoom", "punch_in", "parallax", "ken_burns") >= 0.1:
        pushes = 0
        for i, pl in enumerate(placements):
            if pushes < 2 and pl.candidate.camera_move == "static" and pl.candidate.motion_intensity < 0.12 and pl.slot.duration_s >= 1.4:
                pushes += add("parallax", i, "slow push to give a locked-off shot life", 0.68, solver)
    # scope bars on landscape timelines when the brief or the references ask for them
    if landscape and (any(w in goal for w in ("letterbox", "anamorphic", "2.39", "scope")) or _share(pkg, "letterbox") >= 0.3):
        for i in range(len(placements)):
            add("letterbox", i, "2.39:1 scope framing for a cinematic look", 0.8, solver)
    # director-proposed effects: same policy, parameter and subject checks
    for proposal in director_effects or []:
        slot = proposal.get("slot")
        template = proposal.get("template")
        if not isinstance(slot, int) or not 0 <= slot < len(placements) or not isinstance(template, str):
            continue
        if template.startswith("text") or template in TRANSITION_TEMPLATES or template in {"kinetic_words", "scale_pop", "slide", "typewriter", "tracking_reveal", "masked_reveal", "word_highlight"}:
            continue  # titles are planned by the text planner; transitions by the cut logic above
        if proposal.get("confidence", 0) < 0.5:
            plan.rejected.append({"effect": template, "at": shot_ids[slot], "reason": "director confidence below 0.5"})
            continue
        if not proposal.get("purpose"):
            plan.rejected.append({"effect": template, "at": shot_ids[slot], "reason": "no stated purpose"})
            continue
        add(template, slot, proposal["purpose"], float(proposal.get("confidence", 0.6)), Provenance(source="planner", note="director proposal"))
    return plan


MATTE_EFFECTS = {"subject_glow", "subject_outline"}
TRANSITION_TEMPLATES = {"zoom_punch", "whip_pan", "flash_transition", "glitch", "cross_dissolve", "dip_to_black", "hard_cut"}


def _has_subject(pl: Placement, shot_records: dict[str, dict]) -> bool:
    comp = (shot_records.get(pl.candidate.shot_id) or {}).get("composition") or {}
    return bool(pl.candidate.face_count) or comp.get("subject_size", 0) > 0.08


def _effect_params(template: str, pl: Placement, spec: EditSpec, *, landscape: bool, shot: dict, fps: float) -> tuple[dict, str | None]:
    """Parameters for a picture effect on this shot, or the reason it does not fit."""
    cand, frames = pl.candidate, pl.slot.frames
    if template == "cinematic_bloom":
        if cand.lighting not in {"night", "neon", "golden", "backlit"}:
            return {}, "bloom needs practical lights or a strong backlight"
        return {"strength": round(0.2 + 0.3 * spec.sliders.glow, 3)}, None
    if template == "camera_shake":
        if not pl.slot.is_drop and pl.slot.beat_kind != "downbeat":
            return {}, "shake belongs on a hit (drop or downbeat)"
        length = min(frames, int(0.5 * fps))
        return {"keys": seeded_keys(zlib.crc32(cand.shot_id.encode()) % 10_000, 0, length, amplitude=0.01, every=2)}, None
    if template == "speed_blur":
        if cand.motion_intensity < 0.5:
            return {}, "speed blur needs fast motion"
        vertical = cand.camera_move.startswith("tilt")
        return {"amount": round(0.03 + 0.05 * spec.sliders.motion_effects, 3), "angle": 90.0 if vertical else 0.0}, None
    if template == "parallax":
        if cand.camera_move != "static" or cand.motion_intensity > 0.25:
            return {}, "parallax is for locked-off shots"
        drift = float(np.clip((cand.subject_x - 0.5) * 0.08, -0.04, 0.04))
        return {"zoom_0": 1.0, "zoom_1": round(1.04 + 0.04 * spec.sliders.motion_effects, 3), "pan_x0": 0.5, "pan_x1": round(0.5 + drift, 4)}, None
    if template == "freeze_frame":
        if frames < 14:
            return {}, "slot too short to hold a freeze"
        at = int(frames * 0.55)
        return {"freeze_at": at, "freeze_at_p1": at + 1, "freeze_at_p4": min(frames - 1, at + 4)}, None
    if template == "letterbox":
        if not landscape:
            return {}, "letterbox would crop a vertical frame"
        w, h = spec.resolution
        return {"bar": round(float(np.clip(1 - (w / h) / 2.39, 0.02, 0.6)), 4)}, None  # exact 2.39:1 scope
    if template in MATTE_EFFECTS:
        if spec.sliders.masking < 0.2:
            return {}, "masking is disabled in the brief"
        comp = shot.get("composition") or {}
        if not (cand.face_count or comp.get("subject_size", 0) > 0.08):
            return {}, "no clear subject to isolate"
        if (shot.get("motion") or {}).get("blur", 0) > 0.6:
            return {}, "too much motion blur for a clean matte"
        if template == "subject_glow":
            return {"radius": 18, "strength": round(0.4 + 0.6 * spec.sliders.glow, 3)}, None
        return {"color": "#FFFFFF", "width_px": 0.006}, None
    if template in {"picture_in_picture", "screen_replacement", "tracked_callout"}:
        return {}, "needs inputs VANTA does not have (inset media / tracking)"
    return {}, None


def spatial_reason_for_3d(shot: dict) -> tuple[bool, str]:
    """3D needs strong perspective/negative space and a stable camera (manual 13.7)."""
    comp = shot.get("composition") or {}
    motion = shot.get("motion") or {}
    reasons = []
    ok = True
    if comp.get("negative_space", 0) < 0.35:
        ok = False
        reasons.append("not enough negative space")
    if motion.get("stability", 0) < 0.6:
        ok = False
        reasons.append("camera not stable enough for a solved 3D layer")
    return ok, "; ".join(reasons) or "negative space + stable camera"
