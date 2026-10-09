"""Unit tests: cut/music/text solvers, text-based editing, NL grammar, learning and preferences."""

from __future__ import annotations

import numpy as np

from vanta.analysis.transcribe import Segment, Transcript, Word
from vanta.core.schemas import AutonomyMode, EditDecisionGraph, EditSpec, Shot, SourceMedia
from vanta.learning.corrections import diff_timeline, from_feedback
from vanta.learning.preferences import RECURRENCE, apply_preferences, calibration, mine_preferences
from vanta.reasoning.nl import parse_instruction
from vanta.solvers import cut as cut_solver
from vanta.solvers import music as music_solver
from vanta.solvers import textedit
from vanta.solvers.text import chunk_words
from vanta.storage.library import Library


def _grid(duration=12.0, bpm=120.0, drop=6.0):
    beats = []
    for i, t in enumerate(np.arange(0, duration, 60 / bpm)):
        beats.append({"time_s": float(t), "strength": 1.0 if i % 4 == 0 else 0.5, "kind": "downbeat" if i % 4 == 0 else ("strong" if i % 2 == 0 else "soft"), "bar": i // 4, "beat_in_bar": i % 4})
    return {"beats": beats, "sections": [{"start_s": 0, "end_s": drop, "label": "build", "energy": 0.5}, {"start_s": drop, "end_s": duration, "label": "drop", "energy": 0.9}], "drops": [drop], "bpm": bpm}


def test_slots_cut_on_beats_force_the_drop_and_fill_exactly():
    slots = cut_solver.plan_slots(_grid(), 12.0, 30, median_s=1.2, p10_s=0.6, p90_s=2.0, first_shot_s=0.8, pacing=0.6, beat_sync=0.9)
    assert slots[0].start_frame == 0 and slots[-1].end_frame == 360
    assert all(a.end_frame == b.start_frame for a, b in zip(slots, slots[1:], strict=False))
    assert any(abs(s.start_s - 6.0) < 1 / 30 and s.is_drop for s in slots), "a cut lands on the drop"
    beat_times = np.arange(0, 12, 0.5)
    on_beat = [s for s in slots[1:] if s.beat_kind != "none"]
    assert on_beat and all(np.min(np.abs(beat_times - s.start_s)) <= 0.04 for s in on_beat)
    assert slots[0].duration_s <= 0.85, "hook stays short"


def _cand(i, size="medium", motion=0.4, quality=0.8):
    return cut_solver.FootageCandidate(shot_id=f"f{i}", media_id=f"m{i % 2}", source_path=f"C:/m{i}.mp4", source_fps=30, source_frames=600, start_s=0, end_s=20, usable_ranges=[(0.0, 20.0)], quality=quality, motion_intensity=motion, shot_size=size)


def test_viterbi_avoids_repeating_shots_back_to_back():
    slots = cut_solver.plan_slots(_grid(), 12.0, 30, median_s=1.2, pacing=0.6, beat_sync=0.9)
    cands = [_cand(i, size=["close", "wide", "medium"][i % 3], quality=0.95 if i == 0 else 0.7) for i in range(4)]
    placements = cut_solver.assign_shots(slots, cands)
    ids = [p.candidate.shot_id for p in placements]
    assert all(a != b for a, b in zip(ids, ids[1:], strict=False))
    assert all(a != c for a, c in zip(ids, ids[2:], strict=False)) or len(set(ids)) < 3
    reused = [p for p in placements if p.candidate.shot_id == "f0"]
    spans = [(p.source_in_frame, p.source_out_frame) for p in reused]
    assert len(spans) == len(set(spans)), "reused footage takes different source ranges"


def test_music_segment_places_drop_near_payoff_and_ends_on_a_bar():
    audio = {"duration_s": 60.0, "bpm": 120.0, "beats": _grid(60, 120, 30)["beats"], "drops_s": [30.0], "sections": []}
    seg = music_solver.choose_segment(audio, 20.0, drop_target_frac=0.4)
    assert seg is not None and seg.drop_at_s is not None
    assert abs(seg.drop_at_s - 8.0) <= 2.0
    assert abs((seg.end_s - seg.start_s) / 2.0 - round((seg.end_s - seg.start_s) / 2.0)) < 1e-6, "whole bars"


def test_caption_chunks_have_no_orphans():
    words = [{"text": w, "start_s": i * 0.3, "end_s": i * 0.3 + 0.25} for i, w in enumerate("we rode all night until the sun came up".split())]
    chunks = chunk_words(words)
    assert all(2 <= len(c.words) <= 5 for c in chunks)
    assert " ".join(c.text for c in chunks) == " ".join(w["text"] for w in words)


def test_text_based_edit_removes_fillers_and_repeats_with_handles():
    words = [Word(t, i * 0.4, i * 0.4 + 0.3, 0.9) for i, t in enumerate("so um I went I went to the bike shop".split())]
    transcript = Transcript("en", [Segment("x", 0, 4, words)], "test", 4.0)
    flat = textedit.flatten(transcript)
    drop = textedit.drop_fillers(flat) | textedit.drop_repeats(flat)
    kept = " ".join(w.text for w in flat if w.index not in drop)
    assert kept == "so I went to the bike shop"
    ranges = textedit.to_source_ranges(textedit.kept_ranges(flat, drop), media_duration_s=4.0)
    assert ranges[0][0] <= 0.0 + 1e-6 and all(b > a for a, b, _ in ranges)


def _edg():
    shots = [Shot(id=f"shot_{i:03d}", source_media_id="s", source_in_frame=30 * i, source_out_frame=30 * i + 30, timeline_start_frame=30 * i, role="build") for i in range(4)]
    return EditDecisionGraph(project_id="p", fps=30, timeline_name="VANTA_P_ASSEMBLY_V1", sources=[SourceMedia(id="s", path="C:/s.mp4", fps=30, duration_frames=600)], shots=shots, metadata={"grid": {"drops": [2.0], "sections": [{"start_s": 2.0, "end_s": 4.0, "label": "drop", "energy": 0.9}]}})


def test_nl_grammar_maps_to_scoped_operations():
    edg = _edg()
    grade = parse_instruction("Make the grade 15% less aggressive", edg)
    assert grade.patches[0].dimension == "grade" and grade.patches[0].changes == {"intensity": 0.85}
    faster = parse_instruction("cut faster during the chorus", edg)
    assert faster.replan == {"range": [60, 120], "pacing_delta": 0.25}
    refs = parse_instruction("use the visual language of reference 2 but keep the typography from reference 1", edg, references=["ref_a", "ref_b"])
    assert refs.spec_changes["reference_weights"] == {"ref_b": {"color": 1.0}, "ref_a": {"typography": 1.0}}
    assert parse_instruction("undo the last mask", edg).undo == "mask"
    assert parse_instruction("lower the music", edg).patches[0].changes == {"gain_db": -3.0}


def test_timeline_diff_classifies_corrections():
    edg = _edg()
    item_ids = {s.id: f"it{i}" for i, s in enumerate(edg.shots)}
    final = {
        "items": [
            {"item_id": "it0", "track_type": "video", "track_index": 1, "start_frame": 0, "duration": 45, "source_start_frame": 0},
            {"item_id": "it1", "track_type": "video", "track_index": 1, "start_frame": 45, "duration": 30, "source_start_frame": 30},
            {"item_id": "new", "track_type": "video", "track_index": 1, "start_frame": 60, "duration": 30, "source_path": "C:/other.mp4"},
            {"item_id": "it3", "track_type": "video", "track_index": 1, "start_frame": 90, "duration": 30, "source_start_frame": 90},
        ],
        "markers": {"10": {"color": "Red", "name": "no text here", "note": "", "customData": ""}, "20": {"color": "Green", "customData": "vanta:run:step"}},
    }
    corrections = diff_timeline(edg, item_ids, final)
    signals = {c.signal for c in corrections}
    assert {"shots_longer", "shot_replaced", "marker_red"} <= signals
    assert not any(c.signal == "marker_green" for c in corrections), "AI breadcrumbs are not user markers"


def test_preferences_need_recurrence_and_approval(tmp_path):
    library = Library(tmp_path / "lib.sqlite3")
    for _ in range(RECURRENCE - 1):
        library.add_correction({**from_feedback("too_much", target="shot_001", dimension_hint="color").to_dict()})
    assert mine_preferences(library) == []
    library.add_correction({**from_feedback("too_much", target="shot_002", dimension_hint="color").to_dict()})
    proposed = mine_preferences(library)
    assert proposed and all(r.kind in {"preference", "negative"} for r in proposed)
    rules = [r["json"] | {"approval_state": r["approval_state"], "kind": r["kind"]} for r in library.list_rules()]
    spec = EditSpec(goal="g", duration_s=10, aspect="9:16", fps=30, autonomy=AutonomyMode.ASSISTED)
    unchanged, notes = apply_preferences(spec, rules)
    assert unchanged.sliders == spec.sliders and not notes, "candidates never change behaviour"
    library.set_rule_state(proposed[0].id, "approved")
    rules = [r["json"] | {"approval_state": r["approval_state"], "kind": r["kind"]} for r in library.list_rules()]
    changed, notes = apply_preferences(spec, rules)
    assert changed.sliders.color_intensity < spec.sliders.color_intensity and notes


def test_calibration_bins_predicted_vs_accepted(tmp_path):
    library = Library(tmp_path / "cal.sqlite3")
    rng = np.random.default_rng(1)
    for _ in range(80):
        c = float(rng.uniform(0.4, 1.0))
        decision = library.add_decision({"dimension": "cut", "predicted_confidence": c})
        library.resolve_decision(decision, bool(rng.uniform() < c))
    result = calibration(library)
    assert result["decisions"] == 80 and result["bins"] and result["suggested"] is not None
