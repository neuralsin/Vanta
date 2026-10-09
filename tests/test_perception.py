"""Golden tests for perception on synthetic media with known ground truth."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from vanta.analysis import synthetic
from vanta.analysis.audio import analyse_audio, beat_alignment_error
from vanta.analysis.color import frame_stats
from vanta.analysis.media import FrameReader, make_proxy, probe
from vanta.analysis.motion import summarise_motion
from vanta.analysis.shots import segment_shots
from vanta.analysis.signals import extract_signals
from vanta.solvers.grade_math import LookParams, apply_look
from vanta.solvers.lut import lut_monotone_luma, read_cube, transform_error, write_cube


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("media")
    size = (360, 640)
    a = synthetic.make_test_clip(root / "a.mp4", duration_s=2.0, size=size, tint=(1.0, 0.85, 0.7))
    b = synthetic.make_test_clip(
        root / "b.mp4", duration_s=1.5, size=size, pattern="mandelbrot", tint=(0.7, 0.85, 1.0)
    )
    c = synthetic.make_test_clip(root / "c.mp4", duration_s=2.5, size=size, pattern="smptebars", pan_px_per_s=120)
    reel = synthetic.concat_clips([a, b, c], root / "reel.mp4")
    text = synthetic.make_test_clip(root / "text.mp4", duration_s=1.0, size=size, pattern="color=c=0x203040", text="NIGHT RIDE")
    track = synthetic.make_click_track(root / "click.wav", bpm=120, duration_s=20, drop_at_s=8.0)
    return {"a": a, "b": b, "c": c, "reel": reel, "text": text, "track": track}


def test_probe_reports_geometry_and_rate(media):
    info = probe(media["reel"])
    assert info.has_video and info.display_size == (360, 640)
    assert info.fps == pytest.approx(30.0)
    assert info.duration_s == pytest.approx(6.0, abs=0.1)


def test_shot_segmentation_recovers_known_cuts(media, tmp_path):
    proxy = make_proxy(media["reel"], tmp_path / "proxy.mp4", long_edge=640)
    sig = extract_signals(proxy)
    shots, boundaries = segment_shots(sig)
    starts = [s.start_frame for s in shots]
    assert len(shots) == 3, starts
    assert abs(starts[1] - 60) <= 1 and abs(starts[2] - 105) <= 1
    assert all(b.kind == "hard_cut" for b in boundaries)
    pan = summarise_motion(sig, shots[2].start_frame, shots[2].end_frame)
    assert pan.camera_move in {"pan_right", "pan_left", "tracking"}
    still = summarise_motion(sig, shots[0].start_frame, shots[0].end_frame)
    assert abs(still.pan_per_s) < abs(pan.pan_per_s)


def test_colour_stats_separate_warm_and_cool(media):
    with FrameReader(media["a"]) as reader:
        warm = frame_stats(reader.read_at(1.0))
    with FrameReader(media["b"]) as reader:
        cool = frame_stats(reader.read_at(0.7))
    assert warm.mid_b > cool.mid_b
    assert warm.cct_k < cool.cct_k


def test_click_track_tempo_beats_downbeats_and_drop(media):
    result = analyse_audio(media["track"])
    assert result.bpm == pytest.approx(120, abs=1.0)
    truth = np.arange(0, 20, 0.5)
    stats = beat_alignment_error([b.time_s for b in result.beats[1:-1]], truth)
    assert stats["within_40ms"] >= 0.95
    downbeats = result.downbeat_times
    assert np.all(np.min(np.abs(downbeats[:, None] - np.arange(0, 20, 2.0)[None, :]), axis=1) < 0.05)
    assert any(abs(d - 8.0) < 0.6 for d in result.drops_s)


def test_lut_round_trip_matches_look_under_one_delta_e(tmp_path):
    look = LookParams(contrast=1.2, saturation=0.8, shadow_tint=(-0.03, 0.0, 0.04), lift=0.02)
    path = write_cube(tmp_path / "look.cube", lambda rgb: apply_look(rgb, look), size=33)
    cube = read_cube(path)
    err = transform_error(cube.apply, lambda rgb: apply_look(rgb, look))
    assert err["mean_de"] < 1.0
    assert lut_monotone_luma(cube)
    identity = transform_error(lambda x: apply_look(x, LookParams()), lambda x: x)
    assert identity["max_de"] < 1e-3


def test_ocr_reads_rendered_title(media):
    from vanta.analysis.ocr import detect_text, normalise_text

    with FrameReader(media["text"]) as reader:
        frame = reader.read_at(0.5)
    found = detect_text(frame)
    assert any(normalise_text(d.text) == "nightride" for d in found), [d.text for d in found]
