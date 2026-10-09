"""Text-based edit (manual 12.3): kept transcript ranges -> EDG with linked A/V and fresh captions."""

from __future__ import annotations

from pathlib import Path

from vanta.analysis.pipeline import load_analysis
from vanta.analysis.transcribe import Transcript
from vanta.config import Settings, get_settings
from vanta.core.schemas import (
    CaptionChunk,
    EditDecisionGraph,
    EditSpec,
    GradeSpec,
    MusicBed,
    Provenance,
    Shot,
    SourceMedia,
)
from vanta.solvers import textedit
from vanta.solvers.text import chunk_words
from vanta.storage.library import Library


class TextEditError(RuntimeError):
    pass


def plan_text_edit(
    project: dict,
    library: Library,
    media_id: str,
    dropped_word_indices: set[int],
    *,
    run_dir: Path,
    settings: Settings | None = None,
    target_duration_s: float | None = None,
    revision: int = 1,
) -> EditDecisionGraph:
    settings = settings or get_settings()
    spec = EditSpec.model_validate(project["spec_json"])
    media = library.get_media(media_id)
    analysis = load_analysis(media, settings) if media else None
    if not analysis or not analysis.get("transcript") or analysis["transcript"].get("error"):
        raise TextEditError("this clip has no transcript; analyse it with speech transcription enabled")
    transcript = Transcript.from_dict(analysis["transcript"])
    if transcript.is_empty:
        raise TextEditError("the transcript is empty (no speech detected)")
    words = textedit.flatten(transcript)
    ranges = textedit.kept_ranges(words, dropped_word_indices)
    if target_duration_s:
        ranges = textedit.trim_to_duration(ranges, target_duration_s)
    audio = None
    wav = Path(analysis["proxy"]).with_name("audio.wav")
    if wav.is_file():
        from vanta.analysis.audio import load_audio

        audio, sr = load_audio(wav, sr=22050)
    else:
        sr = 22050
    info = analysis.get("info", {})
    src_fps = float(info.get("fps") or 30.0)
    duration_s = float(info.get("duration_s") or analysis["duration_s"])
    cuts = textedit.to_source_ranges(ranges, media_duration_s=duration_s, audio=audio, sr=sr)
    if not cuts:
        raise TextEditError("nothing is left after the edit")
    fps = spec.fps
    sources = [SourceMedia(id=media_id, path=media["path"], media_type="audiovisual", fps=src_fps, duration_frames=int(info.get("frame_count") or duration_s * src_fps), library_id=media_id)]
    shots: list[Shot] = []
    cursor = 0
    captions_words: list[dict] = []
    same = abs(src_fps - fps) < 1e-3
    for i, (a, b, text) in enumerate(cuts):
        src_in = int(round(a * src_fps))
        src_out = max(src_in + 2, int(round(b * src_fps)))
        tl_frames = src_out - src_in if same else max(2, int(round((src_out - src_in) / src_fps * fps)))
        shots.append(
            Shot(
                id=f"tx_{i:03d}",
                source_media_id=media_id,
                source_in_frame=src_in,
                source_out_frame=src_out,
                timeline_start_frame=cursor,
                timeline_duration_frames=None if same else tl_frames,
                conform="exact" if same else "resolve_realtime",
                audio_enabled=True,
                confidence=0.92,
                rationale=[f"kept: “{text[:80]}”"],
                role="dialogue",
                provenance=Provenance(source="user", solver="textedit-v1", note="text-based edit"),
            )
        )
        for w in words:
            if a <= w.start_s and w.end_s <= b + 1e-3 and w.index not in dropped_word_indices:
                offset = cursor / fps - a
                captions_words.append({"text": w.text, "start_s": w.start_s + offset, "end_s": w.end_s + offset})
        cursor += tl_frames
    captions = []
    if spec.captions:
        for chunk in chunk_words(captions_words):
            start = int(round(chunk.start_s * fps))
            captions.append(CaptionChunk(text=chunk.text, start_frame=start, end_frame=max(start + 2, int(round(chunk.end_s * fps))), words=[{"text": w["text"], "start_frame": int(round(w["start_s"] * fps)), "end_frame": int(round(w["end_s"] * fps))} for w in chunk.words]))
    music = None
    if spec.music_id:
        track = library.get_media(spec.music_id)
        rights = (track or {}).get("rights_json") or {}
        track_analysis = load_analysis(track, settings) if track else None
        if track and track_analysis:
            rate = fps
            total = int(track_analysis["duration_s"] * rate)
            sources.append(SourceMedia(id=track["id"], path=track["path"], media_type="audio", fps=rate, duration_frames=total, library_id=track["id"]))
            music = MusicBed(source_media_id=track["id"], source_in_frame=0, source_out_frame=min(total, cursor), timeline_start_frame=0, track_index=1, rights_source=rights.get("source"), licence_confirmed=bool(rights.get("platform_cleared") or rights.get("licence_confirmed")), gain_db=-12.0, duck_under_dialogue_db=-9.0, fade_out_frames=int(fps * 0.5))
    grades: list[GradeSpec] = []
    edg = EditDecisionGraph(
        project_id=project["id"],
        revision=revision,
        fps=fps,
        width=spec.resolution[0],
        height=spec.resolution[1],
        timeline_name=f"VANTA_{''.join(ch for ch in project['name'].title() if ch.isalnum())[:24] or 'Edit'}_ASSEMBLY_V{revision}",
        sources=sources,
        shots=shots,
        music=music,
        grades=grades,
        captions=captions,
        provenance={"planner": "text-edit-v1", "kept_ranges": len(cuts), "removed_words": len(dropped_word_indices)},
        metadata={"spec": spec.model_dump(mode="json"), "text_edit": {"media_id": media_id, "dropped": sorted(dropped_word_indices)}},
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    return edg
