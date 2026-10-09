"""Scoped EDG patches (manual 10, 17): patch, don't rebuild.

``apply_patches`` returns a new EDG revision where only the targeted elements
changed. Grade patches re-bake only the affected shots' LUTs; cut patches keep
every other shot's frames; text patches never touch the cut. Each applied
patch is recorded on the revision for provenance and learning.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from vanta.core.schemas import EditDecisionGraph, EffectLayer, Patch, Provenance
from vanta.solvers.grade_math import CDL, LookParams, apply_look
from vanta.solvers.lut import write_cube


class PatchError(ValueError):
    pass


@dataclass
class PatchContext:
    lut_dir: Path
    graph: Any | None = None  # FootageGraph for shot replacement
    grid: dict | None = None  # beat grid for retiming
    notes: list[str] = field(default_factory=list)


def _cdl_from(spec: dict | None) -> CDL:
    if not spec:
        return CDL()

    def vec(key: str, default: float) -> tuple[float, float, float]:
        raw = spec.get(key)
        if isinstance(raw, str):
            parts = [float(v) for v in raw.split()]
            if len(parts) == 3:
                return tuple(parts)  # type: ignore[return-value]
        return (default, default, default)

    return CDL(slope=vec("Slope", 1.0), offset=vec("Offset", 0.0), power=vec("Power", 1.0), saturation=float(spec.get("Saturation", 1.0)))


def _rebake(edg: EditDecisionGraph, shot_id: str, params: LookParams, ctx: PatchContext, tag: str) -> None:
    grade = edg.grade_for(shot_id)
    if grade is None:
        raise PatchError(f"{shot_id} has no grade to modify")
    if grade.locked:
        raise PatchError(f"grade on {shot_id} is locked by the user")
    from vanta.core.camera_log import decode_to_display
    from vanta.solvers.grade import lut_size_for

    cdl = _cdl_from(grade.tech_cdl)
    profile = grade.input_profile
    ctx.lut_dir.mkdir(parents=True, exist_ok=True)
    path = write_cube(ctx.lut_dir / f"{shot_id}_r{edg.revision}_{tag}.cube", lambda x: apply_look(cdl.apply(decode_to_display(x, profile)), params), size=lut_size_for(profile), title=f"VANTA {shot_id} {tag}")
    look_path = write_cube(ctx.lut_dir / f"{shot_id}_r{edg.revision}_{tag}_look.cube", lambda x: apply_look(x, params), size=33, title=f"VANTA {shot_id} {tag} look")
    grade.look_lut_path = str(look_path)
    grade.params = params.to_dict()
    grade.lut_path = str(path)
    grade.provenance = Provenance(source="patch", solver="grade-v3", note=tag, rules=grade.provenance.rules)


def _targets(edg: EditDecisionGraph, target: str, kind: str) -> list[str]:
    if kind == "shot":
        ids = [s.id for s in edg.shots]
    elif kind == "text":
        ids = [t.id for t in edg.texts]
    elif kind == "effect":
        ids = [e.id for e in edg.effects] + [t.id for t in edg.transitions]
    else:
        ids = []
    if target in {"all", "*", ""}:
        return ids
    if target.startswith("range:"):
        a, b = (int(v) for v in target.split(":", 1)[1].split("-"))
        if kind == "shot":
            return [s.id for s in edg.shots if a <= s.timeline_start_frame < b]
        if kind == "text":
            return [t.id for t in edg.texts if t.start_frame < b and t.end_frame > a]
        return [e.id for e in edg.effects if e.start_frame < b and e.end_frame > a]
    return [target] if target in ids else []


def _apply_one(edg: EditDecisionGraph, patch: Patch, ctx: PatchContext) -> list[str]:
    changed: list[str] = []
    dim, op, ch = patch.dimension, patch.operation, patch.changes
    if dim == "grade":
        for sid in _targets(edg, patch.target_id, "shot"):
            grade = edg.grade_for(sid)
            if not grade or grade.locked:
                continue
            params = LookParams.from_dict(grade.params)
            if "intensity" in ch:
                params = params.scaled(float(ch["intensity"]))
            if "contrast" in ch:
                params.contrast = float(np.clip(params.contrast + float(ch["contrast"]), 0.6, 1.6))
            if "saturation" in ch:
                params.saturation = float(np.clip(params.saturation * (1 + float(ch["saturation"])), 0.0, 1.8))
            if "exposure" in ch:
                params.exposure = float(np.clip(params.exposure + float(ch["exposure"]), -2, 2))
            if "warmth" in ch:
                w = float(ch["warmth"]) * 0.02
                params.highlight_tint = tuple(np.clip(np.asarray(params.highlight_tint) + np.array([w, w * 0.4, -w]), -0.08, 0.08).tolist())
                params.shadow_tint = tuple(np.clip(np.asarray(params.shadow_tint) + np.array([w * 0.5, 0, -w * 0.5]), -0.08, 0.08).tolist())
            if "black_point" in ch:
                params.lift = float(np.clip(params.lift + float(ch["black_point"]), 0, 0.15))
            if "skin_protect" in ch:
                params.skin_protect = float(np.clip(ch["skin_protect"], 0, 1))
            _rebake(edg, sid, params, ctx, f"{patch.id[-6:]}")
            changed.append(sid)
    elif dim == "text":
        for tid in _targets(edg, patch.target_id, "text"):
            layer = next(t for t in edg.texts if t.id == tid)
            if layer.locked and op != "remove":
                continue
            if op == "remove":
                edg.texts = [t for t in edg.texts if t.id != tid]
            else:
                for key in ("x", "y", "size", "text", "color", "outline", "shadow", "template", "start_frame", "end_frame"):
                    if key in ch:
                        setattr(layer, key, ch[key])
                if "backing" in ch:
                    layer.params = {**layer.params, "backing": bool(ch["backing"])}
                if "dy" in ch:
                    layer.y = float(np.clip(layer.y + float(ch["dy"]), 0.05, 0.95))
                if "size_scale" in ch:
                    layer.size = float(np.clip(layer.size * float(ch["size_scale"]), 0.015, 0.4))
                layer.provenance = Provenance(source="patch", note=patch.reason[:200])
            changed.append(tid)
        if op == "add" and "layer" in ch:
            from vanta.core.schemas import TextLayer

            edg.texts.append(TextLayer.model_validate(ch["layer"]))
            changed.append(edg.texts[-1].id)
        if op == "remove" and patch.target_id in {"all", "*"}:
            edg.texts = [t for t in edg.texts if t.locked]
            edg.captions = []
            changed.append("captions")
    elif dim in {"effect", "fusion"}:
        if op == "remove":
            ids = set(_targets(edg, patch.target_id, "effect"))
            if "template" in ch:
                ids |= {e.id for e in edg.effects if e.template == ch["template"]} | {t.id for t in edg.transitions if t.kind == ch["template"] or t.template == ch["template"]}
            keep_fx = [e for e in edg.effects if e.id not in ids or e.locked]
            keep_tr = [t for t in edg.transitions if t.id not in ids]
            changed = [e.id for e in edg.effects if e not in keep_fx] + [t.id for t in edg.transitions if t not in keep_tr]
            edg.effects, edg.transitions = keep_fx, keep_tr
        elif op == "add":
            edg.effects.append(EffectLayer.model_validate(ch["layer"]))
            changed.append(edg.effects[-1].id)
    elif dim == "cut":
        if op == "replace":
            shot = edg.shot(patch.target_id)
            if shot.locked:
                raise PatchError(f"{shot.id} is locked")
            if ctx.graph is None:
                raise PatchError("shot replacement needs the footage graph")
            new_id = ch.get("footage_shot_id")
            cand = ctx.graph.candidate(new_id)
            tl_frames = edg.timeline_duration(shot)
            src_len = int(round(tl_frames * cand.source_fps / edg.fps))
            start = ch.get("source_in_frame")
            if start is None:
                rng = max(cand.usable_ranges, key=lambda r: r[1] - r[0]) if cand.usable_ranges else (cand.start_s, cand.end_s)
                start = int(round(((rng[0] + rng[1]) / 2 - tl_frames / edg.fps / 2) * cand.source_fps))
            start = int(max(0, min(start, cand.source_frames - src_len)))
            if cand.media_id not in {s.id for s in edg.sources}:
                from vanta.core.schemas import SourceMedia

                edg.sources.append(SourceMedia(id=cand.media_id, path=cand.source_path, fps=cand.source_fps, duration_frames=cand.source_frames, library_id=cand.media_id))
            shot.source_media_id = cand.media_id
            shot.footage_shot_id = cand.shot_id
            shot.source_in_frame = start
            shot.source_out_frame = start + src_len
            same = abs(cand.source_fps - edg.fps) < 1e-3
            shot.timeline_duration_frames = None if same and shot.speed_percent == 100 else tl_frames
            shot.conform = "exact" if same else "resolve_realtime"
            shot.provenance = Provenance(source="patch", note=patch.reason[:200])
            # the new footage needs its own grade: keep the look params, drop the stale LUT
            grade = edg.grade_for(shot.id)
            if grade:
                grade.provenance = Provenance(source="patch", note="regrade pending for replaced shot")
            changed.append(shot.id)
        elif op in {"retime", "move"}:
            a = edg.shot(patch.target_id)
            ordered = edg.ordered_shots(1)
            idx = ordered.index(a)
            if idx + 1 >= len(ordered):
                raise PatchError("cannot retime the last cut")
            b = ordered[idx + 1]
            new_cut = int(ch["cut_frame"])
            delta = new_cut - b.timeline_start_frame
            if delta == 0:
                return []
            a_len = edg.timeline_duration(a) + delta
            b_len = edg.timeline_duration(b) - delta
            if a_len < 4 or b_len < 4:
                raise PatchError("retime would make a shot shorter than 4 frames")
            for shot, length in ((a, a_len), (b, b_len)):
                src_fps = edg.source(shot.source_media_id).fps or edg.fps
                src_len = int(round(length * src_fps / edg.fps * shot.speed_percent / 100))
                if shot is a:
                    shot.source_out_frame = shot.source_in_frame + src_len
                else:
                    shot.source_in_frame = max(0, shot.source_out_frame - src_len)
                    shot.timeline_start_frame = new_cut
                if shot.timeline_duration_frames is not None or abs(src_fps - edg.fps) > 1e-3:
                    shot.timeline_duration_frames = length
            changed += [a.id, b.id]
    elif dim == "music":
        if edg.music is None:
            raise PatchError("the edit has no music bed")
        if "gain_db" in ch:
            edg.music.gain_db = float(np.clip(edg.music.gain_db + float(ch["gain_db"]), -60, 12))
        if "duck_under_dialogue_db" in ch:
            edg.music.duck_under_dialogue_db = float(ch["duck_under_dialogue_db"])
        changed.append("music")
    elif dim == "metadata":
        edg.metadata = {**edg.metadata, **ch}
        changed.append("metadata")
    else:
        raise PatchError(f"unsupported patch dimension {dim}")
    return changed


def apply_patches(edg: EditDecisionGraph, patches: list[Patch], ctx: PatchContext) -> tuple[EditDecisionGraph, dict[str, list[str]]]:
    """Return a new revision with the patches applied and a map patch id -> changed element ids."""
    new = copy.deepcopy(edg)
    report: dict[str, list[str]] = {}
    for patch in patches:
        if patch.target_edg_id != edg.id:
            raise PatchError(f"patch {patch.id} targets a different EDG")
        report[patch.id] = _apply_one(new, patch, ctx)
    data = new.model_dump(mode="python")
    data.update(id=f"edg_r{edg.revision + 1}_{edg.id[-8:]}", revision=edg.revision + 1, parent_id=edg.id, patch_ids=[*edg.patch_ids, *[p.id for p in patches]])
    data["timeline_name"] = _bump_version(edg.timeline_name, edg.revision + 1)
    return EditDecisionGraph.model_validate(data), report


def _bump_version(name: str, revision: int) -> str:
    import re

    if re.search(r"_V\d+$", name):
        return re.sub(r"_V\d+$", f"_V{revision}", name)
    return f"{name}_V{revision}"


def diff_summary(old: EditDecisionGraph, new: EditDecisionGraph) -> dict[str, Any]:
    """What changed between revisions (shown in Review and used by learning)."""
    old_shots = {s.id: s for s in old.shots}
    changed_shots = [s.id for s in new.shots if s.id in old_shots and (s.footage_shot_id != old_shots[s.id].footage_shot_id or s.source_in_frame != old_shots[s.id].source_in_frame or s.timeline_start_frame != old_shots[s.id].timeline_start_frame)]
    old_grades = {g.shot_id: g.lut_path for g in old.grades}
    changed_grades = [g.shot_id for g in new.grades if old_grades.get(g.shot_id) != g.lut_path]
    old_texts = {t.id: (t.x, t.y, t.text, t.size) for t in old.texts}
    changed_texts = [t.id for t in new.texts if old_texts.get(t.id) != (t.x, t.y, t.text, t.size)]
    removed_texts = [tid for tid in old_texts if tid not in {t.id for t in new.texts}]
    return {
        "shots": changed_shots,
        "grades": changed_grades,
        "texts": changed_texts,
        "texts_removed": removed_texts,
        "effects_removed": [e.id for e in old.effects if e.id not in {x.id for x in new.effects}],
        "transitions_removed": [t.id for t in old.transitions if t.id not in {x.id for x in new.transitions}],
    }
