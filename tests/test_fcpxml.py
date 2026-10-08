from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from vanta.compile.fcpxml import compile_edg_to_fcpxml
from vanta.core.schemas import EditDecisionGraph, MusicBed, Shot, SourceMedia


def make_graph(tmp_path, *, overlap: bool = False, fps: float = 30):
    sources = []
    shots = []
    for i in range(5):
        path = tmp_path / f"clip-{i}.mov"
        path.write_bytes(b"test fixture; compiler does not decode media")
        media_id = f"video-{i}"
        sources.append(
            SourceMedia(
                id=media_id, path=str(path), media_type="video", duration_frames=90, fps=fps
            )
        )
        start = i * 9 if overlap else i * 10
        shots.append(
            Shot(
                id=f"shot-{i}",
                source_media_id=media_id,
                source_in_frame=0,
                source_out_frame=10,
                timeline_start_frame=start,
                rationale=["fixture decision"],
            )
        )
    music_path = tmp_path / "song.wav"
    music_path.write_bytes(b"test fixture; compiler does not decode media")
    sources.append(
        SourceMedia(
            id="song", path=str(music_path), media_type="audio", duration_frames=150, fps=fps
        )
    )
    return EditDecisionGraph(
        project_id="p",
        fps=fps,
        timeline_name="ASSEMBLY_V1",
        sources=sources,
        shots=shots,
        music=MusicBed(
            source_media_id="song",
            source_out_frame=150,
            rights_source="test license",
            licence_confirmed=True,
        ),
    )


def test_compiler_writes_five_hard_cuts_and_one_music_lane(tmp_path) -> None:
    graph = make_graph(tmp_path)
    output = tmp_path / "runs" / "run_test" / "edit.fcpxml"
    summary = compile_edg_to_fcpxml(graph, output)
    assert summary.video_clip_count == 5
    assert summary.audio_clip_count == 1
    assert summary.duration_frames == 150
    root = ET.parse(output).getroot()
    assert root.attrib["version"] == "1.10"
    clips = root.findall(".//asset-clip")
    assert len(clips) == 6
    assert [clip.attrib["name"] for clip in clips[:5]] == [f"shot-{i}" for i in range(5)]
    assert clips[-1].attrib["audioRole"] == "music"
    assert clips[-1].attrib["lane"] == "1"
    assert clips[2].attrib["offset"] == "2/3s"
    assert output.with_suffix(".fcpxml.tmp").exists() is False
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        compile_edg_to_fcpxml(graph, output)


def test_compiler_rejects_overlaps_extra_video_tracks_and_missing_media(tmp_path) -> None:
    with pytest.raises(ValueError, match="overlaps"):
        make_graph(tmp_path, overlap=True)
    graph = make_graph(tmp_path / "other") if (tmp_path / "other").exists() else None
    if graph is None:
        (tmp_path / "other").mkdir()
        graph = make_graph(tmp_path / "other")
    graph.shots[0].track_index = 2
    with pytest.raises(ValueError, match="one primary video track"):
        compile_edg_to_fcpxml(graph, tmp_path / "tracks.fcpxml")
    graph = make_graph(tmp_path / "missing") if (tmp_path / "missing").exists() else None
    if graph is None:
        (tmp_path / "missing").mkdir()
        graph = make_graph(tmp_path / "missing")
    (tmp_path / "missing" / "clip-0.mov").unlink()
    with pytest.raises(FileNotFoundError):
        compile_edg_to_fcpxml(graph, tmp_path / "missing.fcpxml")


def test_fractional_fps_uses_broadcast_rate_and_exact_frame_times(tmp_path) -> None:
    graph = make_graph(tmp_path, fps=29.97)
    summary = compile_edg_to_fcpxml(graph, tmp_path / "fractional.fcpxml")
    assert summary.fps == "30000/1001"
    root = ET.parse(tmp_path / "fractional.fcpxml").getroot()
    fmt = root.find("./resources/format")
    assert fmt is not None and fmt.attrib["frameDuration"] == "1001/30000s"


def test_compiler_rejects_music_without_confirmed_rights(tmp_path) -> None:
    graph = make_graph(tmp_path)
    graph.music.licence_confirmed = False
    with pytest.raises(ValueError, match="rights must be confirmed"):
        compile_edg_to_fcpxml(graph, tmp_path / "unlicensed.fcpxml")
