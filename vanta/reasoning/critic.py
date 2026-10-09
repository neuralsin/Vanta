"""Critic (L5 editorial rubric) and repair agent (manual 17).

The critic must return structured issues, never "looks good". The repair
agent turns each issue into a scoped patch; at most three repair iterations
run, then remaining issues are shown to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from vanta.config import get_settings
from vanta.core.schemas import EditDecisionGraph, Patch, QCFinding, QCResult
from vanta.reasoning import prompts
from vanta.reasoning.llm import (
    LLMProvider,
    ModelOutputInvalid,
    ModelUnavailable,
    contact_sheet,
    dumps_compact,
)
from vanta.render.preview import contact_frames

MAX_ITERATIONS = 3


@dataclass
class CriticOutcome:
    report: dict
    findings: list[QCFinding]
    source: str
    error: str | None = None


def critique(edg: EditDecisionGraph, render: Path, qc: QCResult, provider: LLMProvider) -> CriticOutcome:
    duration = edg.duration_frames / edg.fps
    times = sorted({0.3, 0.9, *[(s.timeline_start_frame + edg.timeline_duration(s) / 2) / edg.fps for s in edg.ordered_shots(1)]})
    times = [t for t in times if t < duration][:24]
    frames = contact_frames(render, times)
    if not frames:
        return CriticOutcome({}, [], "none", "no frames to critique")
    sheet = contact_sheet(frames, [f"{t:.1f}s" for t in times[: len(frames)]], cols=6)
    summary = {
        "duration_s": round(duration, 2),
        "shots": [{"id": s.id, "start": round(s.timeline_start_frame / edg.fps, 2), "role": s.role} for s in edg.ordered_shots(1)],
        "texts": [{"text": t.text, "start": round(t.start_frame / edg.fps, 2)} for t in edg.texts],
        "effects": [e.template for e in edg.effects] + [t.kind for t in edg.transitions],
        "drop_s": edg.music.drop_s if edg.music else None,
    }
    try:
        result = provider.generate_json(
            system=prompts.SYSTEM_EDITOR,
            prompt=prompts.CRITIC_PROMPT.format(qc=dumps_compact([f.description for f in qc.findings][:20]), summary=dumps_compact(summary)),
            schema=prompts.CriticReport,
            images=[sheet],
            prompt_version=prompts.CRITIC_PROMPT_VERSION,
            think=get_settings().models.critic_think,
        )
    except (ModelUnavailable, ModelOutputInvalid) as exc:
        return CriticOutcome({}, [], "none", str(exc))
    findings = []
    for issue in result.data.get("issues", []):
        shot = next((s for s in edg.ordered_shots(1) if s.timeline_start_frame / edg.fps <= issue["at_s"] < edg.shot_end(s) / edg.fps), None)
        findings.append(
            QCFinding(
                level=5,
                kind=f"editorial_{issue['type']}",
                severity=issue["severity"],
                confidence=issue["confidence"],
                description=issue["description"],
                affected_range_s=(max(0.0, issue["at_s"] - 0.5), issue["at_s"] + 0.5),
                target_id=shot.id if shot else None,
                repair=issue["repair"],
                blocking=False,
            )
        )
    return CriticOutcome(result.data, findings, result.model)


@dataclass
class RepairPlan:
    patches: list[Patch] = field(default_factory=list)
    unresolved: list[QCFinding] = field(default_factory=list)
    affected_ranges_s: list[tuple[float, float]] = field(default_factory=list)


def plan_repairs(edg: EditDecisionGraph, findings: list[QCFinding], *, graph=None, frames_for_text=None, platform: str = "instagram_reel") -> RepairPlan:
    """Issues -> scoped patches. Only confident, actionable findings are repaired."""
    plan = RepairPlan()
    seen: set[tuple[str, str]] = set()

    def patch(dimension: str, op: str, target: str, changes: dict, reason: str, conf: float) -> None:
        key = (dimension, target)
        if key in seen:
            return
        seen.add(key)
        plan.patches.append(Patch(target_edg_id=edg.id, base_revision=edg.revision, dimension=dimension, operation=op, target_id=target, changes=changes, reason=reason[:200], confidence=conf, requires_approval=conf < 0.7, source="critic"))

    for f in findings:
        if f.confidence < 0.55 or f.severity < 0.3:
            plan.unresolved.append(f)
            continue
        if f.affected_range_s:
            plan.affected_ranges_s.append(f.affected_range_s)
        if f.repair == "MOVE_TEXT" and f.target_id:
            layer = next((t for t in edg.texts if t.id == f.target_id), None)
            if layer is None:
                plan.unresolved.append(f)
                continue
            if f.kind in {"text_over_face", "unsafe_text"}:
                new_y = 0.3 if layer.y > 0.5 else 0.62
                if frames_for_text is not None:
                    from vanta.solvers.text import place_text

                    frames = frames_for_text(layer)
                    if frames:
                        placed = place_text(frames, layer.text, size=layer.size, color=layer.color, platform=platform)
                        new_y = placed.y
                        patch("text", "adjust", layer.id, {"x": placed.x, "y": placed.y, "outline": placed.outline or layer.outline, "backing": placed.backing}, f"repair: {f.description}", 0.85)
                        continue
                patch("text", "adjust", layer.id, {"y": new_y}, f"repair: {f.description}", 0.75)
            elif f.kind in {"ocr_mismatch", "low_contrast_text"}:
                patch("text", "adjust", layer.id, {"outline": True, "backing": f.kind == "ocr_mismatch", "size_scale": 1.12}, f"repair: {f.description}", 0.8)
            else:
                plan.unresolved.append(f)
        elif f.repair == "ADJUST_GRADE" and f.target_id:
            if f.suggested:
                patch("grade", "adjust", f.target_id, dict(f.suggested), f"repair: {f.description}", 0.8)
            elif f.kind == "clipping":
                patch("grade", "adjust", f.target_id, {"intensity": 0.8, "exposure": -0.1}, f"repair: {f.description}", 0.8)
            else:
                patch("grade", "adjust", f.target_id, {"intensity": 0.9}, f"repair: {f.description}", 0.7)
        elif f.repair == "ADJUST_MUSIC" and edg.music:
            if f.kind in {"true_peak", "audio_clipping"}:
                patch("music", "adjust", "music", {"gain_db": -2.0}, f"repair: {f.description}", 0.9)
            else:
                plan.unresolved.append(f)  # loudness is normalised at render; recheck after
        elif f.repair == "RETIME_CUT" and f.target_id and edg.music and edg.music.beat_grid_s:
            shot = next((s for s in edg.ordered_shots(1) if s.id == f.target_id), None)
            ordered = edg.ordered_shots(1)
            if shot and ordered.index(shot) > 0:
                prev = ordered[ordered.index(shot) - 1]
                beats = np.asarray(edg.music.beat_grid_s)
                cut_s = shot.timeline_start_frame / edg.fps
                nearest = float(beats[np.argmin(np.abs(beats - cut_s))])
                if abs(nearest - cut_s) > 0.02:
                    patch("cut", "retime", prev.id, {"cut_frame": int(round(nearest * edg.fps))}, f"repair: snap cut to beat at {nearest:.2f}s", 0.8)
            else:
                plan.unresolved.append(f)
        elif f.repair == "REPLACE_SHOT" and f.target_id and graph is not None:
            patch("cut", "replace", f.target_id, {"footage_shot_id": "__next_best__"}, f"repair: {f.description}", min(0.8, f.confidence))
        elif f.repair == "REMOVE_EFFECT" and f.target_id:
            effect = next((e for e in edg.effects if e.shot_id == f.target_id), None)
            if effect:
                patch("effect", "remove", effect.id, {}, f"repair: {f.description}", 0.75)
            else:
                plan.unresolved.append(f)
        elif f.repair == "REMOVE_TEXT" and f.target_id:
            patch("text", "remove", f.target_id, {}, f"repair: {f.description}", 0.7)
        else:
            plan.unresolved.append(f)
    return plan


def next_best_footage(graph, edg: EditDecisionGraph, shot_id: str) -> str | None:
    """Replacement candidate for a slot: best unused footage shot that fits the duration."""
    shot = edg.shot(shot_id)
    used = {s.footage_shot_id for s in edg.shots}
    need = edg.timeline_duration(shot) / edg.fps
    options = [c for c in graph.candidates if c.shot_id not in used and c.longest_range() >= need and not c.rejected]
    if not options:
        options = [c for c in graph.candidates if c.shot_id != shot.footage_shot_id and c.longest_range() >= need]
    if not options:
        return None
    return max(options, key=lambda c: c.quality + 0.3 * c.hook_potential * (shot.role == "hook")).shot_id
