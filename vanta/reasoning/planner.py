"""Planner (manual 10): three passes that turn brief + style + footage + music into an EDG.

Pass 1 Assembly: story, hook, shot selection (Viterbi), pacing, music, framing.
Pass 2 Polish: grade intents -> solved looks, typography + placement, masks,
       Fusion effects, transitions, speed ramps, captions, micro-timing.
Pass 3 Finish/QC: structural checks before anything touches Resolve.

The VLM supplies intent; solvers compute every number. The result also
carries the "steer board" (which references, effects and looks the edit will
draw on) so the user can steer before a single frame is written to Resolve.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from vanta.analysis.pipeline import load_analysis
from vanta.compile.fusion import load_registry
from vanta.config import Settings, get_settings
from vanta.core.schemas import (
    CaptionChunk,
    EditDecisionGraph,
    EditSpec,
    GradeSpec,
    MatteRef,
    MusicBed,
    Provenance,
    Reframe,
    Shot,
    SourceMedia,
    TextLayer,
    TimelineMarker,
)
from vanta.reasoning import director
from vanta.reasoning.effects_plan import plan_effects, spatial_reason_for_3d
from vanta.reasoning.footage_graph import FootageGraph, build_graph, semantic_scores
from vanta.reasoning.llm import LLMProvider, get_provider
from vanta.solvers import cut as cut_solver
from vanta.solvers import music as music_solver
from vanta.solvers.grade import GradeIntent, GradeSolution, solve_grade, write_shot_luts
from vanta.solvers.text import chunk_words, match_font, place_text, scan_fonts
from vanta.storage.library import Library
from vanta.style.brain import StyleBrain, StylePackage

PLANNER_VERSION = "planner-v3"
Progress = Callable[[str, float, str], None]


class PlanCancelled(RuntimeError):
    pass


class PlanningError(RuntimeError):
    pass


@dataclass
class PlanResult:
    edg: EditDecisionGraph
    package: StylePackage
    music_candidates: list[music_solver.MusicCandidate]
    chosen_music: music_solver.MusicCandidate | None
    grid: dict
    slots: list[cut_solver.Slot]
    placements: list[cut_solver.Placement]
    assembly: director.DirectorResult
    grade_intents: director.DirectorResult
    grades: dict[str, GradeSolution]
    steer: dict
    decisions: list[dict]
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    graph: FootageGraph | None = None


def _noop(_: str, __: float, ___: str) -> None:
    return None


def _frames_for(graph: FootageGraph, cand: cut_solver.FootageCandidate, start_s: float, end_s: float, n: int = 3) -> list[np.ndarray]:
    analysis = graph.analyses[cand.media_id]
    proxy = analysis.get("proxy")
    frames: list[np.ndarray] = []
    if not proxy or not Path(proxy).is_file():
        return frames
    cap = cv2.VideoCapture(proxy)
    fps = cap.get(cv2.CAP_PROP_FPS) or analysis.get("analysis_fps", 30.0)
    span = [start_s + 0.05, max(start_s + 0.06, end_s - 0.05)] if n <= 3 else [start_s, max(start_s + 0.01, end_s - 0.02)]
    for t in np.linspace(span[0], span[1], n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if ok:
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    cap.release()
    return frames


def _music_tracks(library: Library, settings: Settings) -> list[dict]:
    tracks = []
    for media in library.list_media("music", enabled_only=True):
        if media.get("analysis_status") != "done":
            continue
        analysis = load_analysis(media, settings)
        if analysis:
            tracks.append({**media, "analysis": analysis})
    return tracks


def _clap_scores(library: Library, request: str) -> dict[str, float]:
    try:
        from vanta.analysis.embeddings import audio_text_embeddings

        result = audio_text_embeddings([request])
    except Exception:
        return {}
    if not result:
        return {}
    model, vec = result
    ids, matrix = library.get_embeddings(model)
    if not ids:
        return {}
    m = matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9)
    scores = m @ vec[0]
    return {i: float(s) for i, s in zip(ids, scores, strict=True)}


def plan_edit(
    project: dict,
    library: Library,
    *,
    run_dir: Path,
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
    use_vlm: bool = True,
    music_choice: str | None = None,
    progress: Progress = _noop,
    cancelled: Callable[[], bool] = lambda: False,
    revision: int = 1,
    parent_id: str | None = None,
) -> PlanResult:
    settings = settings or get_settings()
    provider = provider or get_provider(settings)
    if not use_vlm:
        from vanta.reasoning.llm import DisabledProvider

        provider = DisabledProvider()
    timings: dict[str, float] = {}
    warnings: list[str] = []
    decisions: list[dict] = []
    t0 = time.monotonic()

    def check() -> None:
        if cancelled():
            raise PlanCancelled("planning cancelled")

    spec = EditSpec.model_validate(project["spec_json"])
    footage_ids = spec.footage_ids or list(project.get("footage_json") or [])
    if not footage_ids:
        raise PlanningError("the project has no footage; add clips in the Footage tab")
    fps = spec.fps
    width, height = spec.resolution
    registry = load_registry()
    registry_names = set(registry)
    run_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------ knowledge
    progress("retrieve", 0.02, "Loading the footage knowledge graph")
    graph = build_graph(library, footage_ids, settings)
    if not graph.candidates:
        raise PlanningError("no analysed footage shots are available; run analysis first")
    from vanta.analysis.pipeline import is_outdated

    stale = [m.get("title") or mid for mid, m in graph.media.items() if is_outdated(m)]
    if stale:
        warnings.append(f"re-analyse {len(stale)} clip(s) analysed by an older VANTA for exact colour: {', '.join(stale[:4])}")
    brain = StyleBrain(library, settings)
    profile_id = spec.profile_id or project.get("profile_id")
    progress("retrieve", 0.06, "Retrieving style rules, exemplars and reference statistics")
    pkg = brain.retrieve(profile_id, spec, list(graph.shots.values()))
    timings["retrieve"] = round(time.monotonic() - t0, 2)
    check()

    # ------------------------------------------------------ music
    progress("music", 0.1, "Ranking licensed music against the brief and style")
    tracks = _music_tracks(library, settings)
    recent = [p.get("music_id") for p in library.list_projects() if p["id"] != project["id"] and p.get("music_id")]
    clap = _clap_scores(library, spec.goal) if tracks else {}
    footage_energy = float(np.mean([c.motion_intensity for c in graph.candidates]))
    candidates = music_solver.rank_tracks(
        tracks, request=spec.goal, duration_s=spec.duration_s, style_audio=pkg.audio, footage_energy=footage_energy, recent_ids=recent, clap_scores=clap
    )
    chosen = None
    wanted = music_choice or spec.music_id
    if wanted:
        chosen = next((c for c in candidates if c.media_id == wanted), None)
        if chosen is None:
            track = next((t for t in tracks if t["id"] == wanted), None)
            if track:
                seg = music_solver.choose_segment(track["analysis"]["audio"], spec.duration_s)
                if seg:
                    rights = track.get("rights_json") or {}
                    chosen = music_solver.MusicCandidate(track["id"], track.get("title") or track["id"], 0.5, seg, track["analysis"]["audio"].get("bpm", 0), {}, ["chosen by you"], bool(rights.get("platform_cleared") or rights.get("licence_confirmed")))
            if chosen is None:
                warnings.append("chosen music is missing, unanalysed or shorter than the edit; using the top-ranked track")
    if chosen is None and candidates:
        chosen = candidates[0]
    if chosen:
        audio = next(t for t in tracks if t["id"] == chosen.media_id)["analysis"]["audio"]
        grid = music_solver.timeline_beat_grid(audio, chosen.segment)
        decisions.append({"dimension": "music", "predicted_confidence": float(np.clip(chosen.score, 0, 1)), "target": chosen.media_id, "detail": chosen.reasons})
        if not chosen.licence_confirmed:
            warnings.append(f"music '{chosen.title}' has no confirmed licence: final render is blocked until you confirm rights")
    else:
        grid = music_solver.synthetic_grid(spec.duration_s, bpm=float(pkg.audio.get("bpm") or 100.0))
        warnings.append("no analysed, licensed music in the library: a silent guide grid is used and a sync marker added")
    check()

    # ------------------------------------------------------ pass 1: assembly
    progress("assembly", 0.16, "Pass 1: solving cut points on the beat grid")
    slots = cut_solver.plan_slots(
        grid,
        spec.duration_s,
        fps,
        median_s=pkg.rhythm.get("median_s"),
        p10_s=pkg.rhythm.get("p10_s"),
        p90_s=pkg.rhythm.get("p90_s"),
        first_shot_s=pkg.rhythm.get("first_shot_s"),
        pacing=spec.sliders.pacing,
        beat_sync=spec.sliders.beat_sync if chosen else 0.0,
    )
    if not slots:
        raise PlanningError("could not plan any slots for the requested duration")
    progress("assembly", 0.22, f"Director choosing intent for {len(slots)} slots")
    from vanta.reasoning import features as feature_manual

    feature_context = {
        "speech": any(seg.get("words") for t in graph.transcripts.values() for seg in (t or {}).get("segments", [])),
        "landscape": width > height,
        "sfx_library": spec.sliders.sfx_priority >= 0.25 and bool(library.list_media("sfx", enabled_only=True)),
        "masking": spec.sliders.masking,
        "three_d": spec.sliders.three_d,
    }
    usable = feature_manual.available(feature_context)
    usable_keys = {f.key for f in usable}
    director_names = (registry_names & usable_keys) | (usable_keys & {"cross_dissolve", "dip_to_black"}) | {"text_basic"}
    assembly = director.assembly_pass(
        provider,
        spec,
        pkg,
        slots,
        graph,
        music_desc=(f"{chosen.title} {chosen.bpm:.0f} BPM, drop at {chosen.segment.drop_at_s}s" if chosen else "silent guide"),
        registry=director_names,
        features=feature_manual.prompt_catalogue(usable),
    )
    timings["assembly_director"] = assembly.seconds
    for item in assembly.data.get("slots", []):
        slot = slots[item["slot"]]
        slot.intent = item.get("intent", "")
        slot.preferred_tags = item.get("preferred_tags", [])
        slot.preferred_shots = item.get("preferred_shots", [])
        slot.avoid_shots = item.get("avoid_shots", [])
    for locked in spec.locks.get("shots", []):
        # "slot:shot" locks from the user (e.g. markers 'keep this cut')
        if ":" in locked:
            idx, shot_id = locked.split(":", 1)
            if idx.isdigit() and int(idx) < len(slots):
                slots[int(idx)].locked_shot = shot_id
    sem = semantic_scores(library, graph, [s.intent for s in slots])
    for cand in graph.candidates:
        if cand.shot_id in sem:
            cand.semantic = dict(enumerate(sem[cand.shot_id]))
    progress("assembly", 0.3, "Second-order Viterbi shot selection")
    placements = cut_solver.assign_shots(
        slots,
        graph.candidates,
        size_prior=pkg.composition.get("shot_sizes") or None,
        move_prior=pkg.motion.get("moves") or None,
        variety=spec.sliders.shot_variety,
    )
    timings["assembly"] = round(time.monotonic() - t0, 2)
    check()

    # ------------------------------------------------------ EDG skeleton
    sources: dict[str, SourceMedia] = {}
    for pl in placements:
        media = graph.media[pl.candidate.media_id]
        if media["id"] not in sources:
            info = graph.analyses[media["id"]].get("info", {})
            sources[media["id"]] = SourceMedia(
                id=media["id"],
                path=media["path"],
                media_type="audiovisual" if info.get("has_audio") else "video",
                duration_frames=pl.candidate.source_frames or None,
                fps=pl.candidate.source_fps,
                width=int(media.get("width") or 0) or None,
                height=int(media.get("height") or 0) or None,
                library_id=media["id"],
                color_space=info.get("color_transfer"),
            )
    shots: list[Shot] = []
    shot_ids: list[str] = []
    dialogue = spec.sliders.dialogue_priority >= 0.6 and bool(graph.transcripts)
    for pl in placements:
        sid = f"shot_{pl.slot.index:03d}"
        shot_ids.append(sid)
        cand = pl.candidate
        same_rate = abs(cand.source_fps - fps) < 1e-3
        has_speech = bool(cand.words)
        shots.append(
            Shot(
                id=sid,
                source_media_id=cand.media_id,
                source_in_frame=pl.source_in_frame,
                source_out_frame=pl.source_out_frame,
                timeline_start_frame=pl.slot.start_frame,
                track_index=1,
                confidence=pl.confidence,
                rationale=pl.rationale,
                timeline_duration_frames=None if same_rate else pl.slot.frames,
                conform="exact" if same_rate else "resolve_realtime",
                footage_shot_id=cand.shot_id,
                slot=pl.slot.index,
                role=pl.slot.role,
                audio_enabled=dialogue and has_speech,
                beat_aligned=pl.slot.beat_kind != "none",
                reframe=follow_reframe(graph, cand, pl.source_in_frame / cand.source_fps, pl.source_out_frame / cand.source_fps, width, height),
                provenance=Provenance(source="solver", solver=cut_solver.SOLVER_VERSION, model=assembly.source if assembly.source != "deterministic" else None, prompt_version=assembly.prompt_version, note=pl.slot.intent or None),
            )
        )
        decisions.append({"dimension": "cut", "predicted_confidence": pl.confidence, "target": sid, "detail": {"footage_shot": cand.shot_id, "terms": pl.terms}})

    # ------------------------------------------------------ pass 2: polish
    progress("polish", 0.38, "Pass 2: grading intent from the director")
    shot_infos = [
        {
            "id": sid,
            "look": (graph.shots[pl.candidate.shot_id].get("look")),
            "faces": pl.candidate.face_count,
            "description": pl.candidate.description,
            "thumb": graph.shots[pl.candidate.shot_id].get("thumb"),
            "profile": input_profile_for(graph.media[pl.candidate.media_id], graph.analyses[pl.candidate.media_id]),
        }
        for sid, pl in zip(shot_ids, placements, strict=True)
    ]
    intents = director.grade_pass(provider, pkg, spec, shot_infos)
    intent_by_id = {s["shot_id"]: s for s in intents.data.get("shots", [])}
    reference_look = pkg.color.get("target_look", {})
    grades: dict[str, GradeSolution] = {}
    grade_specs: list[GradeSpec] = []
    lut_dir = run_dir / "luts"
    frame_cache: dict[str, list[np.ndarray]] = {}
    mids = []
    from vanta.core.camera_log import decode_to_display
    from vanta.core.colormath import luma

    early_profiles = {mid: input_profile_for(graph.media[mid], graph.analyses[mid]) for mid in graph.media}
    for pl in placements:
        frames = _frames_for(graph, pl.candidate, pl.source_in_frame / pl.candidate.source_fps, pl.source_out_frame / pl.candidate.source_fps)
        frame_cache[pl.candidate.shot_id + f"@{pl.source_in_frame}"] = frames
        if frames:
            # exposure matching happens on the decoded (display-referred) image
            decoded = [decode_to_display(f[::4, ::4].astype(np.float32) / 255, early_profiles.get(pl.candidate.media_id, "rec709")) for f in frames]
            mids.append(float(np.median([np.median(luma(d)) for d in decoded])))
    match_luma = float(np.median(mids)) * 0.85 + 0.075 if mids else None
    solved_by_footage: dict[str, GradeSolution] = {}
    profiles = {mid: input_profile_for(graph.media[mid], graph.analyses[mid]) for mid in graph.media}
    for mid, profile in profiles.items():
        if profile != "rec709":
            warnings.append(f"{graph.media[mid].get('title')}: decoding as {profile} before grading")
    for i, (sid, pl) in enumerate(zip(shot_ids, placements, strict=True)):
        check()
        progress("grade", 0.42 + 0.22 * i / len(placements), f"Solving the look for {sid} ({i + 1}/{len(placements)})")
        frames = frame_cache.get(pl.candidate.shot_id + f"@{pl.source_in_frame}") or []
        intent_raw = intent_by_id.get(sid, {})
        intent = GradeIntent(
            text=intent_raw.get("intent", ""),
            warmth=intent_raw.get("warmth", "neutral"),
            contrast=intent_raw.get("contrast", "keep"),
            saturation=intent_raw.get("saturation", "keep"),
            separation=intent_raw.get("separation", "subtle"),
            protect_skin=bool(intent_raw.get("protect_skin", True)),
            deepen_blacks=bool(intent_raw.get("deepen_blacks", False)),
        )
        key = pl.candidate.shot_id
        if key in solved_by_footage:
            solution = solved_by_footage[key]
        elif frames:
            shot_ctx = pkg.per_shot.get(pl.candidate.shot_id, {})
            solution = solve_grade(
                frames,
                reference_look,
                intent=intent,
                intensity=spec.sliders.color_intensity,
                skin_priority=spec.sliders.skin_priority,
                contrast_bias=spec.sliders.contrast,
                saturation_bias=spec.sliders.saturation,
                match_luma=match_luma,
                rule_effects=shot_ctx.get("rule_effects"),
                seed=11 + i,
                input_profile=profiles.get(pl.candidate.media_id, "rec709"),
            )
            solved_by_footage[key] = solution
        else:
            warnings.append(f"{sid}: no frames available for grading; left ungraded")
            continue
        grades[sid] = solution
        paths = write_shot_luts(solution, lut_dir, key)
        rules = pkg.per_shot.get(pl.candidate.shot_id, {}).get("rules", [])
        grade_specs.append(
            GradeSpec(
                shot_id=sid,
                intent=intent.text or intents.data.get("global_intent", ""),
                params=solution.params.to_dict(),
                tech_cdl={**solution.cdl.to_resolve(), "path": paths["cdl"]},
                lut_path=paths["full"],
                look_lut_path=paths["look"],
                decode_lut_path=paths.get("decode"),
                route="lut_only",  # the executor upgrades to the 8-node DRX route when G4b has passed
                input_profile=solution.input_profile,
                node_enabled=solution.node_enabled,
                target_stats=solution.target,
                predicted_stats=solution.predicted,
                loss=solution.loss,
                confidence=solution.confidence,
                rationale=solution.rationale,
                provenance=Provenance(source="solver", solver="grade-v3", rules=rules, model=intents.source if intents.source != "deterministic" else None, prompt_version=intents.prompt_version, note=paths["look"]),
            )
        )
        decisions.append({"dimension": "color", "predicted_confidence": solution.confidence, "target": sid, "detail": {"loss": solution.loss}})
    check()

    # typography
    progress("text", 0.66, "Typography: placement, legibility and font mapping")
    fonts = scan_fonts()
    font = match_font((pkg.typography or {}).get("font_class", "bold sans"), fonts) or {"name": "Arial", "file": ""}
    texts: list[TextLayer] = []
    mattes: list[MatteRef] = []
    size_frac = float(np.clip((pkg.typography or {}).get("size", 0.05) * (0.8 + 0.5 * spec.sliders.typography_density), 0.025, 0.12))
    habitual = ((pkg.typography or {}).get("x", 0.5), (pkg.typography or {}).get("y", 0.42))
    fill = (pkg.typography or {}).get("fill", "#ffffff")
    if not isinstance(fill, str) or len(fill) != 7:
        fill = "#ffffff"
    placed_boxes = []
    track_cursor: dict[int, int] = {}
    for text_intent in assembly.data.get("texts", [])[: max(1, int(1 + 4 * spec.sliders.typography_density))]:
        slot_idx = int(text_intent.get("slot", 0))
        if slot_idx >= len(placements):
            continue
        pl = placements[slot_idx]
        sid = shot_ids[slot_idx]
        start = pl.slot.start_frame + (3 if slot_idx == 0 else 0)
        end = min(int(round(spec.duration_s * fps)), start + int(round((2.4 if text_intent.get("role") == "title" else 1.8) * fps)))
        template = text_intent.get("template", "text_basic")
        behind = template == "text_behind_subject"
        shot_record = graph.shots[pl.candidate.shot_id]
        notes = []
        if template.startswith("text3d"):
            ok, why = spatial_reason_for_3d(shot_record)
            if not ok:
                template = "text_2_5d"
                notes.append(f"3D downgraded to 2.5D: {why}")
        if behind and (spec.sliders.masking < 0.2 or not (pl.candidate.face_count or (shot_record.get("composition") or {}).get("subject_size", 0) > 0.08)):
            template, behind = "text_2_5d", False
            notes.append("behind-subject needs a clear subject and masking enabled; 2.5D fallback")
        frames = frame_cache.get(pl.candidate.shot_id + f"@{pl.source_in_frame}") or []
        text_value = text_intent["text"]
        if (pkg.typography or {}).get("case") == "upper":
            text_value = text_value.upper()
        placement = place_text(frames, text_value, size=size_frac, color=fill, platform=spec.platform, habitual=habitual, behind_subject=behind, avoid_boxes=placed_boxes) if frames else None
        matte_id = None
        if behind:
            matte = MatteRef(shot_id=sid, directory=str(run_dir / "masks" / sid), frame_count=max(1, pl.source_out_frame - pl.source_in_frame), method="pending", prompt=pl.candidate.description or "main subject", confidence=0.0, review_required=True)
            mattes.append(matte)
            matte_id = matte.id
        track = 2
        while track_cursor.get(track, -1) > start:
            track += 1
        track_cursor[track] = end
        layer = TextLayer(
            text=text_value,
            role=text_intent.get("role", "title"),
            template=template,
            start_frame=start,
            end_frame=max(start + 2, end),
            track_index=track,
            x=placement.x if placement else 0.5,
            y=placement.y if placement else 0.42,
            font=font["name"],
            size=size_frac,
            color=fill,
            outline=bool(placement and placement.outline) or bool((pkg.typography or {}).get("outline")),
            shadow=bool(placement.shadow) if placement else True,
            behind_subject=behind,
            matte_id=matte_id,
            shot_id=sid,
            placement_score=placement.score if placement else None,
            purpose=text_intent.get("reason", "communicate the hook"),
            confidence=round(float(min(0.95, 0.55 + 0.4 * (placement.score if placement else 0.3))), 3),
            params={"font_file": font.get("file", ""), "backing": bool(placement and placement.backing)},
            provenance=Provenance(source="planner", model=assembly.source if assembly.source != "deterministic" else None, note="; ".join(notes + (placement.reasons if placement else []))[:300]),
        )
        texts.append(layer)
        if placement:
            placed_boxes.append(placement.box)
        decisions.append({"dimension": "typography", "predicted_confidence": layer.confidence, "target": layer.id, "detail": {"template": template}})
    # captions from dialogue words mapped through the cut
    captions: list[CaptionChunk] = []
    if spec.captions:
        words = []
        for pl, shot in zip(placements, shots, strict=True):
            if not shot.audio_enabled:
                continue
            in_s = pl.source_in_frame / pl.candidate.source_fps
            out_s = pl.source_out_frame / pl.candidate.source_fps
            transcript = graph.transcripts.get(pl.candidate.media_id) or {}
            for seg in transcript.get("segments", []):
                for w in seg.get("words", []):
                    if in_s <= w["start_s"] and w["end_s"] <= out_s:
                        words.append({"text": w["text"], "start_s": pl.slot.start_s + (w["start_s"] - in_s), "end_s": pl.slot.start_s + (w["end_s"] - in_s)})
        for chunk in chunk_words(sorted(words, key=lambda w: w["start_s"])):
            captions.append(
                CaptionChunk(
                    text=chunk.text.upper() if (pkg.typography or {}).get("case") == "upper" else chunk.text,
                    start_frame=int(round(chunk.start_s * fps)),
                    end_frame=max(int(round(chunk.start_s * fps)) + 2, int(round(chunk.end_s * fps))),
                    words=[{"text": w["text"], "start_frame": int(round(w["start_s"] * fps)), "end_frame": int(round(w["end_s"] * fps))} for w in chunk.words],
                    y=0.68,
                )
            )
    check()

    # effects, transitions, ramps
    progress("effects", 0.74, "Effects and transitions (purpose-checked)")
    fx = plan_effects(
        spec,
        pkg,
        placements,
        shot_ids,
        fps=fps,
        registry=registry_names,
        director_effects=assembly.data.get("effects"),
        landscape=width > height,
        shot_records={pl.candidate.shot_id: graph.shots.get(pl.candidate.shot_id) or {} for pl in placements},
    )
    # subject effects share one matte per shot with behind-subject titles
    matte_by_shot = {m.shot_id: m for m in mattes}
    for effect in fx.effects:
        sid = fx.matte_shots.get(effect.id)
        if not sid:
            continue
        if sid not in matte_by_shot:
            pl = placements[shot_ids.index(sid)]
            matte_by_shot[sid] = MatteRef(shot_id=sid, directory=str(run_dir / "masks" / sid), frame_count=max(1, pl.source_out_frame - pl.source_in_frame), method="pending", prompt=pl.candidate.description or "main subject", confidence=0.0, review_required=True)
            mattes.append(matte_by_shot[sid])
        effect.matte_id = matte_by_shot[sid].id
    # a matte is solved on source frames: it cannot follow a speed-ramped pre-render
    for layer in texts:
        if layer.behind_subject and layer.shot_id in fx.ramps:
            layer.template, layer.behind_subject, layer.matte_id = "text_2_5d", False, None
            warnings.append(f"'{layer.text}': behind-subject title moved to 2.5D because {layer.shot_id} is speed-ramped")
    used_mattes = {t.matte_id for t in texts} | {e.matte_id for e in fx.effects}
    matte_shots = {m.shot_id for m in mattes if m.id in used_mattes}
    for shot in shots:
        if shot.reframe is not None and shot.reframe.dynamic and shot.id in matte_shots:
            shot.reframe = shot.reframe.model_copy(update={"keys": []})
            warnings.append(f"{shot.id}: subject-follow disabled so its subject matte stays aligned (static framing)")
    mattes[:] = [m for m in mattes if m.id in used_mattes]
    for sid, ramp in fx.ramps.items():
        shot = next(s for s in shots if s.id == sid)
        mean_speed = float(np.trapezoid([p[1] for p in ramp.curve], [p[0] for p in ramp.curve]))
        tl_frames = (shot.timeline_duration_frames or (shot.source_out_frame - shot.source_in_frame))
        src_frames = max(2, int(round(tl_frames * mean_speed / 100 * shot_source_fps(sources, shot) / fps)))
        shot.source_out_frame = shot.source_in_frame + src_frames
        shot.speed_percent = round(mean_speed, 4)
        shot.speed_ramp = ramp
        shot.timeline_duration_frames = tl_frames
        shot.conform = "resolve_realtime" if shot.conform == "resolve_realtime" else "exact"
    for effect in fx.effects:
        decisions.append({"dimension": "fusion", "predicted_confidence": effect.confidence, "target": effect.id, "detail": {"template": effect.template, "purpose": effect.purpose}})
    for transition in fx.transitions:
        decisions.append({"dimension": "fusion", "predicted_confidence": transition.confidence, "target": transition.id, "detail": {"kind": transition.kind}})
    warnings.extend(f"effect {r['effect']} at {r['at']} skipped: {r['reason']}" for r in fx.rejected)

    sfx_cues = plan_sfx(library, spec, fx.transitions, shots, sources, fps)
    # music bed + markers
    music_bed = None
    if chosen:
        track = next(t for t in tracks if t["id"] == chosen.media_id)
        info = track["analysis"]["audio"]
        rate = float(track.get("fps") or 0) or fps
        sources[track["id"]] = SourceMedia(
            id=track["id"],
            path=track["path"],
            media_type="audio",
            duration_frames=int(info["duration_s"] * rate),
            fps=rate,
            library_id=track["id"],
        )
        rights = track.get("rights_json") or {}
        music_bed = MusicBed(
            source_media_id=track["id"],
            source_in_frame=int(round(chosen.segment.start_s * rate)),
            source_out_frame=min(int(info["duration_s"] * rate), int(round(chosen.segment.end_s * rate))),
            timeline_start_frame=0,
            track_index=1,
            rights_source=rights.get("source"),
            licence_confirmed=chosen.licence_confirmed,
            fade_out_frames=int(0.5 * fps),
            duck_under_dialogue_db=-9.0 if dialogue else 0.0,
            beat_grid_s=[b["time_s"] for b in grid["beats"]],
            downbeats_s=[b["time_s"] for b in grid["beats"] if b["kind"] == "downbeat"],
            drop_s=grid["drops"][0] if grid["drops"] else None,
            bpm=grid.get("bpm"),
        )
    markers: list[TimelineMarker] = []
    for section in grid.get("sections", []):
        markers.append(TimelineMarker(frame=int(round(section["start_s"] * fps)), color="Cyan", name=f"AP:{section['label']}", note=f"music section {section['label']} (energy {section.get('energy', 0):.2f})", kind="section"))
    for drop in grid.get("drops", []):
        markers.append(TimelineMarker(frame=int(round(drop * fps)), color="Cyan", name="AP:drop", note="music drop: major visual change", kind="section"))
    if not chosen:
        markers.append(TimelineMarker(frame=0, color="Cyan", name="AP:sync", note="add your platform sound here: guide grid assumes this sync point", kind="sync_point"))

    edg = EditDecisionGraph(
        project_id=project["id"],
        revision=revision,
        parent_id=parent_id,
        fps=fps,
        width=width,
        height=height,
        timeline_name=f"VANTA_{_safe(project['name'])}_ASSEMBLY_V{revision}",
        sources=list(sources.values()),
        shots=shots,
        music=music_bed,
        grades=grade_specs,
        transitions=fx.transitions,
        texts=texts,
        effects=fx.effects,
        mattes=mattes,
        captions=captions,
        sfx=sfx_cues,
        markers=markers,
        locks=spec.locks,
        prohibitions=spec.never,
        provenance={
            "planner": PLANNER_VERSION,
            "cut_solver": cut_solver.SOLVER_VERSION,
            "music_solver": music_solver.SOLVER_VERSION,
            "assembly_source": assembly.source,
            "assembly_error": assembly.error,
            "grade_source": intents.source,
            "grade_error": intents.error,
            "style": pkg.provenance,
            "story": assembly.data.get("story"),
            "hook_strategy": assembly.data.get("hook_strategy"),
            "color_intent": intents.data.get("global_intent"),
        },
        metadata={"spec": spec.model_dump(mode="json"), "music": chosen.to_dict() if chosen else None, "grid": {"bpm": grid.get("bpm"), "drops": grid.get("drops"), "sections": grid.get("sections")}},
    )
    progress("finish", 0.84, "Pass 3: structural QC before execution")
    steer = build_steer_board(edg, pkg, candidates, chosen, placements, graph, grades, run_dir, fx, frame_cache)
    timings["total"] = round(time.monotonic() - t0, 2)
    progress("plan", 0.9, f"Planned {len(shots)} shots, {len(texts)} titles, {len(fx.transitions)} transitions")
    return PlanResult(edg, pkg, candidates, chosen, grid, slots, placements, assembly, intents, grades, steer, decisions, warnings, timings, graph)


SFX_WORDS = {
    "whoosh": ("whoosh", "swoosh", "swish", "woosh", "swipe", "transition"),
    "hit": ("hit", "impact", "boom", "punch", "slam", "thud"),
    "riser": ("riser", "rise", "build", "uplifter"),
}


def plan_sfx(library: Library, spec: EditSpec, transitions, shots: list[Shot], sources: dict[str, SourceMedia], fps: float) -> list:
    """Whooshes into stylised transitions and a hit on the drop, from the user's SFX library."""
    from vanta.core.schemas import SfxCue

    if spec.sliders.sfx_priority < 0.25:
        return []
    library_sfx = [m for m in library.list_media("sfx", enabled_only=True) if (m.get("duration_s") or 0) > 0.05]
    if not library_sfx:
        return []

    def pick(kind: str) -> dict | None:
        words = SFX_WORDS[kind]
        matches = [m for m in library_sfx if any(w in (str(m.get("title", "")) + " " + " ".join(m.get("tags_json") or [])).casefold() for w in words)]
        return min(matches, key=lambda m: m.get("duration_s") or 9) if matches else None

    cues = []
    gain = float(np.interp(spec.sliders.sfx_priority, [0.25, 1.0], [-14.0, -4.0]))
    by_id = {s.id: s for s in shots}

    def add(kind: str, frame: int, lead_s: float, purpose: str) -> None:
        media = pick(kind)
        if media is None:
            return
        length = int(min(media["duration_s"], 2.5) * fps)
        if media["id"] not in sources:
            sources[media["id"]] = SourceMedia(id=media["id"], path=media["path"], media_type="audio", fps=fps, duration_frames=max(length, int(media["duration_s"] * fps)), library_id=media["id"])
        rights = media.get("rights_json") or {}
        cues.append(
            SfxCue(
                source_media_id=media["id"],
                kind=kind,
                timeline_frame=max(0, frame - int(lead_s * fps)),
                source_out_frame=max(1, length),
                gain_db=round(gain, 1),
                purpose=purpose,
                licence_confirmed=bool(rights.get("platform_cleared") or rights.get("licence_confirmed")),
            )
        )

    for transition in transitions:
        if transition.kind in {"whip", "zoom_punch", "flash", "glitch"} and transition.to_shot in by_id:
            add("whoosh", by_id[transition.to_shot].timeline_start_frame, 0.35, "sell the " + transition.kind.replace("_", " "))
    for shot in shots:
        if shot.role == "drop":
            add("hit", shot.timeline_start_frame, 0.03, "impact on the drop")
            break
    return cues[:8]


def input_profile_for(media: dict, analysis: dict) -> str:
    """User override (Library) wins; otherwise the profile detected during analysis."""
    from vanta.core.camera_log import PROFILES

    chosen = (media.get("info_json") or {}).get("color_profile")
    if chosen in PROFILES:
        return chosen
    detected = (analysis.get("color_profile") or {}).get("key")
    return detected if detected in PROFILES else "rec709"


def follow_reframe(graph: FootageGraph, cand: cut_solver.FootageCandidate, in_s: float, out_s: float, width: int, height: int) -> Reframe | None:
    """Subject-follow reframe when the subject moves across the crop; else the static subject reframe."""
    from vanta.solvers.reframe import follow_keys, locate_subjects

    static = subject_reframe(cand, width, height)
    timeline_aspect = width / max(1, height)
    if abs(cand.aspect - timeline_aspect) / timeline_aspect < 0.05:
        return static
    duration = max(out_s - in_s, 1e-3)
    n = int(np.clip(duration * 5, 4, 40))  # 5 samples per second
    times = list(np.linspace(0.0, duration, n))
    frames = _frames_for(graph, cand, in_s, out_s, n=n)
    if len(frames) < 4:
        return static
    track = locate_subjects(frames, times[: len(frames)], start=(cand.subject_x, cand.subject_y))
    keys, pan, tilt = follow_keys(track, cand.aspect, width, height, duration)
    if keys:
        return Reframe(pan=pan, tilt=tilt, keys=keys)
    if abs(pan) > 0.005 or abs(tilt) > 0.005:
        return Reframe(pan=pan, tilt=tilt)
    return static


def subject_reframe(cand: cut_solver.FootageCandidate, width: int, height: int) -> Reframe | None:
    """Subject-aware static reframe when the source aspect differs from the timeline (manual 12.5)."""
    timeline_aspect = width / max(1, height)
    if abs(cand.aspect - timeline_aspect) / timeline_aspect < 0.05:
        return None
    if cand.aspect > timeline_aspect:
        scaled_w = cand.aspect * height  # fill: source scaled to timeline height
        limit = (scaled_w - width) / (2 * width)
        pan = float(np.clip((0.5 - cand.subject_x) * scaled_w / width, -limit, limit))
        return Reframe(pan=round(pan, 4)) if abs(pan) > 0.005 else None
    scaled_h = width / cand.aspect
    limit = (scaled_h - height) / (2 * height)
    tilt = float(np.clip((cand.subject_y - 0.5) * scaled_h / height, -limit, limit))
    return Reframe(tilt=round(tilt, 4)) if abs(tilt) > 0.005 else None


def shot_source_fps(sources: dict[str, SourceMedia], shot: Shot) -> float:
    return sources[shot.source_media_id].fps or 30.0


def _safe(name: str) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9]+", "", name.title())[:24]
    return cleaned or "Edit"


def build_steer_board(
    edg: EditDecisionGraph,
    pkg: StylePackage,
    candidates: list[music_solver.MusicCandidate],
    chosen: music_solver.MusicCandidate | None,
    placements: list[cut_solver.Placement],
    graph: FootageGraph,
    grades: dict[str, GradeSolution],
    run_dir: Path,
    fx,
    frame_cache: dict[str, list[np.ndarray]],
) -> dict[str, Any]:
    """'Your video will be inspired by these reels and these effects' — shown before execution."""
    from vanta.analysis.media import save_jpeg

    steer_dir = run_dir / "steer"
    steer_dir.mkdir(parents=True, exist_ok=True)
    references = {}
    for dim, weights in pkg.dimension_weights.items():
        for ref, weight in weights.items():
            if weight <= 0:
                continue
            references.setdefault(ref, {})[dim] = round(weight, 2)
    ref_cards = []
    library_titles = {}
    for ref, dims in references.items():
        techniques = [t for t in pkg.techniques if t["reference"] == ref and t["confidence"] >= 0.5]
        ref_cards.append({"reference_id": ref, "dimensions": dims, "techniques": [{"technique": t["technique"], "purpose": t["purpose"], "evidence": t["evidence"]} for t in techniques[:6]]})
        library_titles[ref] = None
    looks = []
    for sid, pl in list(zip([s.id for s in edg.shots], placements, strict=True))[:6]:
        frames = frame_cache.get(pl.candidate.shot_id + f"@{pl.source_in_frame}") or []
        solution = grades.get(sid)
        if not frames or not solution:
            continue
        before = frames[len(frames) // 2]
        after = (np.clip(solution.transform(before.astype(np.float32) / 255), 0, 1) * 255).astype(np.uint8)
        b_path = save_jpeg(before, steer_dir / f"{sid}_before.jpg", long_edge=420)
        a_path = save_jpeg(after, steer_dir / f"{sid}_after.jpg", long_edge=420)
        looks.append({"shot_id": sid, "before": str(b_path), "after": str(a_path), "rationale": solution.rationale[-1] if solution.rationale else ""})
    strip = [
        {
            "shot_id": shot.id,
            "slot": shot.slot,
            "role": shot.role,
            "start_s": round(shot.timeline_start_frame / edg.fps, 2),
            "duration_s": round(edg.timeline_duration(shot) / edg.fps, 2),
            "thumb": graph.shots[shot.footage_shot_id].get("thumb") if shot.footage_shot_id in graph.shots else None,
            "footage_shot": shot.footage_shot_id,
            "confidence": shot.confidence,
            "intent": shot.provenance.note,
        }
        for shot in edg.shots
    ]
    return {
        "references": ref_cards,
        "rules": [{"id": r.get("id"), "statement": r.get("statement"), "state": r.get("approval_state")} for r in pkg.rules[:12]],
        "planned_effects": [{"kind": "transition", "name": t.kind, "at": t.to_shot, "purpose": t.purpose, "confidence": t.confidence} for t in edg.transitions]
        + [{"kind": "effect", "name": e.template, "at": e.shot_id, "purpose": e.purpose, "confidence": e.confidence} for e in edg.effects]
        + [{"kind": "text", "name": t.template, "at": t.shot_id, "purpose": t.purpose, "text": t.text, "confidence": t.confidence} for t in edg.texts]
        + [{"kind": "speed_ramp", "name": "speed_ramp", "at": s.id, "purpose": s.speed_ramp.purpose, "confidence": 0.7} for s in edg.shots if s.speed_ramp],
        "skipped_effects": fx.rejected,
        "music": [c.to_dict() for c in candidates],
        "chosen_music": chosen.to_dict() if chosen else None,
        "strip": strip,
        "looks": looks,
        "color_intent": (pkg.color or {}).get("intent"),
        "typography": pkg.typography,
        "rhythm": pkg.rhythm,
    }
