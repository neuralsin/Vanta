"""Range re-cut (patch, don't rebuild): re-solve slots and shots inside one time range only.

Used for instructions like "cut faster during the chorus". Shots outside the
range keep their frames, grades and IDs; titles anchored in the range are
re-attached to the new shot that covers their start.
"""

from __future__ import annotations

import copy
import re

from vanta.core.schemas import EditDecisionGraph, Provenance, Shot
from vanta.solvers import cut as cut_solver


def recut_range(edg: EditDecisionGraph, plan, start_frame: int, end_frame: int, *, pacing: float, beat_sync: float, variety: float) -> EditDecisionGraph:
    if plan is None or plan.graph is None:
        raise ValueError("range re-cut needs the run's planning context (footage graph + beat grid)")
    fps = edg.fps
    ordered = edg.ordered_shots(1)
    inside = [s for s in ordered if s.timeline_start_frame >= start_frame and edg.shot_end(s) <= end_frame]
    if not inside:
        # widen to the shots overlapping the range
        inside = [s for s in ordered if s.timeline_start_frame < end_frame and edg.shot_end(s) > start_frame]
    if not inside:
        raise ValueError("no shots in the requested range")
    a = inside[0].timeline_start_frame
    b = edg.shot_end(inside[-1])
    grid = plan.grid
    sub = {
        "beats": [{**bt, "time_s": round(bt["time_s"] - a / fps, 4)} for bt in grid.get("beats", []) if a / fps - 1e-3 <= bt["time_s"] < b / fps],
        "sections": [{**s, "start_s": max(0.0, s["start_s"] - a / fps), "end_s": min((b - a) / fps, s["end_s"] - a / fps)} for s in grid.get("sections", []) if s["end_s"] > a / fps and s["start_s"] < b / fps],
        "drops": [d - a / fps for d in grid.get("drops", []) if a / fps < d < b / fps],
    }
    pkg = plan.package
    slots = cut_solver.plan_slots(sub, (b - a) / fps, fps, median_s=pkg.rhythm.get("median_s"), p10_s=pkg.rhythm.get("p10_s"), p90_s=pkg.rhythm.get("p90_s"), pacing=pacing, beat_sync=beat_sync)
    before = ordered[ordered.index(inside[0]) - 1] if ordered.index(inside[0]) > 0 else None
    after_idx = ordered.index(inside[-1]) + 1
    after = ordered[after_idx] if after_idx < len(ordered) else None
    if slots:
        if before and before.footage_shot_id:
            slots[0].avoid_shots.append(before.footage_shot_id)
        if after and after.footage_shot_id:
            slots[-1].avoid_shots.append(after.footage_shot_id)
    for slot in slots:
        slot.start_frame += a
        slot.end_frame += a
        slot.start_s += a / fps
        slot.end_s += a / fps
    placements = cut_solver.assign_shots(slots, plan.graph.candidates, size_prior=pkg.composition.get("shot_sizes") or None, move_prior=pkg.motion.get("moves") or None, variety=variety)
    new = copy.deepcopy(edg)
    removed = {s.id for s in inside}
    grades_by_footage = {s.footage_shot_id: new.grade_for(s.id) for s in new.shots if new.grade_for(s.id)}
    new.shots = [s for s in new.shots if s.id not in removed]
    old_grades = [g for g in new.grades if g.shot_id not in removed]
    new_grades = []
    source_ids = {s.id for s in new.sources}
    created: list[Shot] = []
    for i, pl in enumerate(placements):
        cand = pl.candidate
        if cand.media_id not in source_ids:
            from vanta.core.schemas import SourceMedia

            new.sources.append(SourceMedia(id=cand.media_id, path=cand.source_path, fps=cand.source_fps, duration_frames=cand.source_frames, library_id=cand.media_id))
            source_ids.add(cand.media_id)
        same = abs(cand.source_fps - fps) < 1e-3
        shot = Shot(
            id=f"shot_r{edg.revision + 1}_{i:03d}_{a}",
            source_media_id=cand.media_id,
            source_in_frame=pl.source_in_frame,
            source_out_frame=pl.source_out_frame,
            timeline_start_frame=pl.slot.start_frame,
            confidence=pl.confidence,
            rationale=pl.rationale + ["range re-cut"],
            timeline_duration_frames=None if same else pl.slot.frames,
            conform="exact" if same else "resolve_realtime",
            footage_shot_id=cand.shot_id,
            slot=pl.slot.index,
            role=pl.slot.role,
            beat_aligned=pl.slot.beat_kind != "none",
            provenance=Provenance(source="patch", solver=cut_solver.SOLVER_VERSION, note="range re-cut"),
        )
        created.append(shot)
        template = grades_by_footage.get(cand.shot_id)
        if template is None and grades_by_footage:
            template = next(iter(grades_by_footage.values()))
        if template is not None:
            new_grades.append(template.model_copy(update={"shot_id": shot.id}))
    new.shots = sorted(new.shots + created, key=lambda s: s.timeline_start_frame)
    new.grades = old_grades + new_grades
    new.transitions = [t for t in new.transitions if t.from_shot not in removed and t.to_shot not in removed]
    new.effects = [e for e in new.effects if e.shot_id not in removed]
    for layer in new.texts:
        if layer.shot_id in removed:
            cover = next((s for s in created if s.timeline_start_frame <= layer.start_frame < s.timeline_start_frame + new.timeline_duration(s)), created[0] if created else None)
            layer.shot_id = cover.id if cover else None
            layer.matte_id = None
            if layer.behind_subject:
                layer.behind_subject = False
                layer.template = "text_2_5d"
    new.mattes = [m for m in new.mattes if m.shot_id not in removed]
    data = new.model_dump(mode="python")
    data.update(id=f"edg_r{edg.revision + 1}_{edg.id[-8:]}", revision=edg.revision + 1, parent_id=edg.id)
    data["timeline_name"] = re.sub(r"_V\d+$", f"_V{edg.revision + 1}", edg.timeline_name)
    result = EditDecisionGraph.model_validate(data)
    if abs(result.duration_frames - edg.duration_frames) > 1:
        raise ValueError("range re-cut changed the edit duration")
    return result
