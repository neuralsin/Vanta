"""Golden end-to-end run on synthetic media (no VLM, no Resolve, no GPU models).

Structural assertions only (manual 19): clip count, order, durations, beat
alignment, artefacts present, preview renders, QC runs. Pixels are not compared.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from vanta.analysis import synthetic
from vanta.analysis.pipeline import AnalysisOptions, analyse_media, analyse_music
from vanta.config import Settings, set_settings
from vanta.core.schemas import EditSpec, SliderSet
from vanta.ingest.library_import import import_file
from vanta.reasoning.nl import parse_instruction
from vanta.reasoning.patches import PatchContext, apply_patches
from vanta.reasoning.planner import plan_edit
from vanta.render.preview import render_preview
from vanta.storage.library import Library
from vanta.style.brain import StyleBrain

OPTIONS = AnalysisOptions(use_vlm=False, transcribe=False, embeddings=False, ocr=True)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("vanta_e2e")
    settings = Settings(data_dir=root / "data")
    settings.models.provider = "disabled"
    set_settings(settings)
    settings.ensure_dirs()
    media = root / "media"
    size = (360, 640)
    footage = []
    specs = [
        ("testsrc2", (1.0, 0.9, 0.8), 0),
        ("smptebars", (0.9, 0.95, 1.0), 90),
        ("mandelbrot", (1.0, 1.0, 1.0), 0),
        ("rgbtestsrc", (0.8, 0.9, 1.0), 140),
        ("testsrc", (1.0, 0.85, 0.7), 60),
    ]
    for i, (pattern, tint, pan) in enumerate(specs):
        footage.append(synthetic.make_test_clip(media / f"clip_{i}.mp4", duration_s=6.0, size=size, pattern=pattern, tint=tint, pan_px_per_s=pan))
    refs = []
    for r in range(3):
        parts = [synthetic.make_test_clip(media / f"ref{r}_{k}.mp4", duration_s=0.8 + 0.3 * k, size=size, pattern=p, tint=(1.0, 0.85 - 0.05 * r, 0.65 + 0.1 * r), text="NIGHT RIDE" if k == 0 else None) for k, p in enumerate(["testsrc2", "smptebars", "mandelbrot", "testsrc"])]
        refs.append(synthetic.concat_clips(parts, media / f"reference_{r}.mp4"))
    music = synthetic.make_click_track(media / "track.wav", bpm=120, duration_s=24, drop_at_s=8.0)
    library = Library(settings.db_path)
    brain = StyleBrain(library, settings)
    profile = library.ensure_profile("test-profile")
    for ref in refs:
        rec = import_file(library, ref, "reference", profile_id=profile["id"])
        analyse_media(rec, library, settings=settings, options=OPTIONS)
        brain.ingest_reference(library.get_media(rec["id"]))
    brain.rebuild_profile(profile["id"])
    footage_ids = []
    for clip in footage:
        rec = import_file(library, clip, "footage")
        analyse_media(rec, library, settings=settings, options=OPTIONS)
        footage_ids.append(rec["id"])
    mus = import_file(library, music, "music", rights={"source": "synthetic", "licence": "CC0", "platform_cleared": True})
    analyse_music(mus, library, settings=settings, embeddings=False)
    spec = EditSpec(
        goal="cinematic night ride reel",
        duration_s=12.0,
        aspect="9:16",
        fps=30,
        resolution=(360, 640),
        sliders=SliderSet(pacing=0.6, beat_sync=0.9, color_intensity=0.6, typography_density=0.4, motion_effects=0.6),
        footage_ids=footage_ids,
        profile_id=profile["id"],
        title_text="Night Ride",
        captions=False,
    )
    project = library.create_project("Night Ride", spec.model_dump(mode="json"), footage=footage_ids, profile_id=profile["id"])
    run_dir = root / "run"
    result = plan_edit(project, library, run_dir=run_dir, settings=settings, use_vlm=False)
    return {"settings": settings, "library": library, "result": result, "run_dir": run_dir, "profile": profile, "spec": spec}


def test_reference_analysis_produces_capsules_and_profile_stats(world):
    library = world["library"]
    refs = library.list_media("reference")
    assert len(refs) == 3 and all(r["analysis_status"] == "done" for r in refs)
    capsules = library.list_capsules()
    techniques = {c["technique"] for c in capsules}
    assert {"pacing", "look", "framing", "camera_motion"} <= techniques
    assert all(c["json"]["observed"] and c["json"]["inferred"] and c["json"]["evidence"] for c in capsules)
    profile = library.get_profile(world["profile"]["id"])
    assert profile["data_json"]["reference_count"] == 3
    assert any(r["kind"] == "principle" for r in library.list_rules())
    assert all(r["approval_state"] == "candidate" for r in library.list_rules()), "nothing is approved silently"


def test_plan_structure_duration_and_beat_alignment(world):
    result = world["result"]
    edg = result.edg
    assert edg.shots, "the planner placed shots"
    assert edg.duration_frames == 12 * 30
    ordered = edg.ordered_shots(1)
    cursor = 0
    for shot in ordered:
        assert shot.timeline_start_frame == cursor, "no gaps or overlaps on V1"
        cursor += edg.timeline_duration(shot)
    beats = np.asarray(edg.music.beat_grid_s)
    aligned = [s.timeline_start_frame / edg.fps for s in ordered[1:] if s.beat_aligned]
    assert aligned and max(np.min(np.abs(beats - t)) for t in aligned) <= 0.04
    assert edg.music.licence_confirmed
    assert len(edg.grades) == len(edg.shots) and all(Path(g.lut_path).is_file() for g in edg.grades)
    assert edg.texts and edg.texts[0].text.upper() == "NIGHT RIDE"
    assert result.steer["strip"] and result.steer["music"]
    first = edg.shot(ordered[0].id)
    assert edg.timeline_duration(first) / edg.fps <= 2.0, "hook shot stays short"


def test_scoped_grade_patch_leaves_cut_untouched(world):
    edg = world["result"].edg
    nl = parse_instruction("make the grade 15% less aggressive", edg)
    assert nl and nl.patches and nl.patches[0].changes == {"intensity": 0.85}
    new, report = apply_patches(edg, nl.patches, PatchContext(lut_dir=world["run_dir"] / "luts"))
    assert new.revision == edg.revision + 1 and new.parent_id == edg.id
    assert [(s.id, s.source_in_frame, s.timeline_start_frame) for s in new.shots] == [(s.id, s.source_in_frame, s.timeline_start_frame) for s in edg.shots]
    assert all(a.lut_path != b.lut_path for a, b in zip(new.grades, edg.grades, strict=True))
    assert new.texts == edg.texts


def test_preview_renders_with_audio_and_qc_runs(world):
    from vanta.analysis.media import probe
    from vanta.qc.levels import run_qc

    edg = world["result"].edg
    out = world["run_dir"] / "proxy" / "preview.mp4"
    render_preview(edg, out, scale=1.0, loudness_lufs=-14.0)
    info = probe(out)
    assert info.has_video and info.has_audio
    assert abs(info.duration_s - 12.0) < 0.15
    qc = run_qc(edg, out, run_id="run_test", iteration=0, expected_duration_s=12.0, platform="instagram_reel", target_lufs=-14.0, render_kind="preview")
    kinds = {f.kind for f in qc.findings}
    assert "gap" not in kinds and "missing_media" not in kinds and "unknown_template" not in kinds
    assert qc.metrics.get("integrated_lufs") is not None
    json.dumps(qc.model_dump(mode="json"))
