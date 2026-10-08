"""Compile a conservative, frame-addressed EDG assembly to FCPXML 1.10.

This creates an interchange artifact only. It does not imply Resolve accepted or
verified the import. The primary storyline is single-track hard cuts; a single
music bed is connected as an audio lane. Transitions, titles, retimes, and extra
video lanes are rejected until their gates and compilers are implemented.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from vanta.core.schemas import EditDecisionGraph, SourceMedia
from vanta.core.timing import fps_fraction, frame_time_string, source_frames_to_timeline_frames


@dataclass(frozen=True)
class CompileSummary:
    output_path: str
    video_clip_count: int
    audio_clip_count: int
    duration_frames: int
    fps: str


def _resource_id(index: int) -> str:
    return f"r{index}"


def _source_asset(resources: ET.Element, source: SourceMedia, ref: str, timeline_fps) -> None:
    path = Path(source.path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise FileNotFoundError(f"Source media is not a file: {path}")
    source_fps = source.fps or float(timeline_fps)
    source_duration = source.duration_frames
    if source_duration is None:
        raise ValueError(f"Source {source.id} requires duration_frames for FCPXML asset metadata")
    media_type = source.media_type.casefold()
    has_video = "1" if media_type in {"video", "audiovisual", "av"} else "0"
    has_audio = "1" if media_type in {"audio", "audiovisual", "av"} else "0"
    asset = ET.SubElement(
        resources,
        "asset",
        {
            "id": ref,
            "name": path.name,
            "start": "0s",
            "duration": frame_time_string(source_duration, source_fps),
            "hasVideo": has_video,
            "hasAudio": has_audio,
        },
    )
    ET.SubElement(asset, "media-rep", {"kind": "original-media", "src": path.as_uri()})


def compile_edg_to_fcpxml(
    graph: EditDecisionGraph,
    output_path: str | Path,
    *,
    width: int = 1080,
    height: int = 1920,
) -> CompileSummary:
    """Write validated single-video-track hard cuts and optional music to FCPXML."""
    if width < 1 or height < 1:
        raise ValueError("sequence width and height must be positive")
    if Path(output_path).suffix.casefold() != ".fcpxml":
        raise ValueError("output path must end in .fcpxml")
    if not graph.shots:
        raise ValueError("EDG must contain at least one shot")
    if graph.music and not graph.music.licence_confirmed:
        raise ValueError("music rights must be confirmed before compiling a timeline")
    if any(shot.track_index != 1 for shot in graph.shots):
        raise ValueError("FCPXML compiler currently supports one primary video track only")
    ordered = sorted(graph.shots, key=lambda item: (item.timeline_start_frame, item.id))
    timeline_fps = fps_fraction(graph.fps)
    end_frame = 0
    for shot in ordered:
        source = next(item for item in graph.sources if item.id == shot.source_media_id)
        source_fps = source.fps or graph.fps
        duration = source_frames_to_timeline_frames(shot.duration_frames, source_fps, graph.fps)
        if shot.timeline_start_frame < end_frame:
            raise ValueError(
                "overlapping primary-storyline clips require a verified transition compiler"
            )
        end_frame = shot.timeline_start_frame + duration
    source_map = {source.id: source for source in graph.sources}
    music_end = 0
    if graph.music:
        music_source = source_map[graph.music.source_media_id]
        music_out = graph.music.source_out_frame or music_source.duration_frames
        assert music_out is not None
        music_duration = source_frames_to_timeline_frames(
            music_out - graph.music.source_in_frame,
            music_source.fps or graph.fps,
            graph.fps,
        )
        music_end = graph.music.timeline_start_frame + music_duration
        end_frame = max(end_frame, music_end)

    root = ET.Element("fcpxml", {"version": "1.10"})
    resources = ET.SubElement(root, "resources")
    frame_duration = 1 / timeline_fps
    fmt_id = _resource_id(1)
    ET.SubElement(
        resources,
        "format",
        {
            "id": fmt_id,
            "name": f"FFVideoFormat{width}x{height}p{graph.fps:g}",
            "frameDuration": f"{frame_duration.numerator}/{frame_duration.denominator}s",
            "width": str(width),
            "height": str(height),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )
    refs: dict[str, str] = {}
    for index, source in enumerate(graph.sources, start=2):
        ref = _resource_id(index)
        refs[source.id] = ref
        _source_asset(resources, source, ref, timeline_fps)

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": graph.project_id})
    project = ET.SubElement(event, "project", {"name": graph.timeline_name})
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": fmt_id,
            "duration": frame_time_string(end_frame, timeline_fps),
            "tcStart": "0s",
            "tcFormat": "NDF",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    cursor = 0
    for shot in ordered:
        source = source_map[shot.source_media_id]
        source_fps = source.fps or graph.fps
        clip_duration = source_frames_to_timeline_frames(
            shot.duration_frames, source_fps, graph.fps
        )
        if shot.timeline_start_frame > cursor:
            gap_duration = shot.timeline_start_frame - cursor
            ET.SubElement(
                spine,
                "gap",
                {
                    "name": "Intentional gap",
                    "offset": frame_time_string(cursor, timeline_fps),
                    "start": "0s",
                    "duration": frame_time_string(gap_duration, timeline_fps),
                },
            )
        clip = ET.SubElement(
            spine,
            "asset-clip",
            {
                "name": shot.id,
                "ref": refs[source.id],
                "offset": frame_time_string(shot.timeline_start_frame, timeline_fps),
                "start": frame_time_string(shot.source_in_frame, source_fps),
                "duration": frame_time_string(shot.duration_frames, source_fps),
                "format": fmt_id,
            },
        )
        note = ET.SubElement(clip, "note")
        note.text = (
            "VANTA EDG shot "
            + shot.id
            + ("; " + "; ".join(shot.rationale) if shot.rationale else "")
        )
        cursor = shot.timeline_start_frame + clip_duration

    if cursor < end_frame and not graph.music:
        ET.SubElement(
            spine,
            "gap",
            {
                "name": "Tail gap",
                "offset": frame_time_string(cursor, timeline_fps),
                "start": "0s",
                "duration": frame_time_string(end_frame - cursor, timeline_fps),
            },
        )
    if graph.music:
        music = graph.music
        source = source_map[music.source_media_id]
        source_fps = source.fps or graph.fps
        music_out = music.source_out_frame or source.duration_frames
        assert music_out is not None
        audio = ET.SubElement(
            spine,
            "asset-clip",
            {
                "name": source.path and Path(source.path).name or music.source_media_id,
                "ref": refs[source.id],
                "lane": str(max(1, music.track_index - 1)),
                "offset": frame_time_string(music.timeline_start_frame, timeline_fps),
                "start": frame_time_string(music.source_in_frame, source_fps),
                "duration": frame_time_string(music_out - music.source_in_frame, source_fps),
                "audioRole": "music",
            },
        )
        note = ET.SubElement(audio, "note")
        note.text = "VANTA licensed/owned music bed; rights=" + (
            music.rights_source or "unconfirmed"
        )

    output = Path(output_path).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing artifact: {output}")
    if any(output == Path(source.path).expanduser().resolve() for source in graph.sources):
        raise ValueError("FCPXML output cannot overwrite a source media file")
    output.parent.mkdir(parents=True, exist_ok=True)
    xml = ET.tostring(root, encoding="unicode", xml_declaration=False)
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml
    temp = output.with_suffix(output.suffix + ".tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(xml)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(output)
    return CompileSummary(
        str(output),
        len(ordered),
        1 if graph.music else 0,
        end_frame,
        f"{timeline_fps.numerator}/{timeline_fps.denominator}",
    )
