"""Perception pipeline (manual 8.1): probe -> normalise -> shots -> adaptive sampling ->
OCR -> audio -> motion -> embeddings -> VLM -> layer reconstruction -> analysis JSON.

One implementation serves references and footage; the footage path adds usable
ranges, transcripts and search tags (footage knowledge graph, section 15).
Results are cached under ``analysis/<content_key>/<ANALYSIS_VERSION>``.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from vanta.analysis import effects as fx
from vanta.analysis.audio import analyse_audio, beat_alignment_error
from vanta.analysis.color import aggregate, describe_look, frame_stats
from vanta.analysis.frame_vision import composition, detect_faces, quality
from vanta.analysis.media import (
    FrameReader,
    effective_matrix,
    extract_audio,
    is_full_range,
    make_proxy,
    probe,
    save_jpeg,
)
from vanta.analysis.motion import summarise_motion
from vanta.analysis.shots import (
    pacing_curve,
    pacing_stats,
    scenedetect_cuts,
    segment_shots,
    split_long_takes,
)
from vanta.analysis.signals import FrameSignals, extract_signals
from vanta.config import Settings, get_settings
from vanta.storage.library import Library

ANALYSIS_VERSION = "analysis-v4"  # v4: matrix-correct proxies (BT.709 sources were decoded as BT.601)

Progress = Callable[[str, float, str], None]


class AnalysisCancelled(RuntimeError):
    pass


@dataclass
class AnalysisOptions:
    use_vlm: bool = True
    transcribe: bool = True
    embeddings: bool = True
    ocr: bool = True
    analysis_fps: float | None = None  # None keeps the source rate (VFR is normalised to 30)
    max_ocr_samples: int = 240


def _noop(_: str, __: float, ___: str) -> None:
    return None


def analysis_dir(settings: Settings, content_key: str) -> Path:
    return settings.analysis_dir / content_key / ANALYSIS_VERSION


VISUAL_KINDS = {"reference", "footage"}


def is_outdated(media: dict) -> bool:
    """Picture analysis made by an older pipeline (e.g. before matrix-correct proxies): re-analyse."""
    return media.get("kind") in VISUAL_KINDS and media.get("analysis_status") == "done" and media.get("analysis_version") != ANALYSIS_VERSION


def load_analysis(media: dict, settings: Settings | None = None) -> dict | None:
    settings = settings or get_settings()
    path = media.get("analysis_path")
    if path and Path(path).is_file():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    candidate = analysis_dir(settings, media["content_key"]) / "analysis.json"
    if candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def _atomic_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    os.replace(tmp, path)


def _sample_plan(shots, fps: float, duration: float, max_ocr: int) -> tuple[dict[int, list[str]], list[int]]:
    """Adaptive sampling: ~1.5 fps baseline, 6 fps in the 0.5 s after every cut.

    Returns ``frame -> purposes`` (stats/thumb/effects) and the OCR frame list.
    """
    plan: dict[int, list[str]] = {}
    ocr: set[int] = set()

    def add(frame: int, purpose: str) -> None:
        plan.setdefault(frame, []).append(purpose)

    for shot in shots:
        length = shot.end_frame - shot.start_frame
        mid = shot.start_frame + length // 2
        add(mid, "thumb")
        n_stats = int(np.clip(round(shot.duration_s * 1.5), 3, 12))
        for k in range(n_stats):
            frame = shot.start_frame + int((k + 0.5) * length / n_stats)
            add(frame, "stats")
        for frac in (0.2, 0.5, 0.8):
            add(shot.start_frame + int(frac * length), "effects")
        for k in range(int(min(0.5, shot.duration_s) * 6)):
            ocr.add(min(shot.end_frame - 1, shot.start_frame + int(k * fps / 6) + 1))
    step = max(1, int(round(fps / 2)))
    total = int(duration * fps)
    ocr.update(range(0, total, step))
    ocr_list = sorted(ocr)
    if len(ocr_list) > max_ocr:
        idx = np.linspace(0, len(ocr_list) - 1, max_ocr).astype(int)
        ocr_list = [ocr_list[i] for i in idx]
    return plan, ocr_list


def _usable_ranges(samples: list[tuple[float, float]], shake: float, start: float, end: float) -> list[list[float]]:
    """Contiguous ranges where sampled quality stays usable (blur/black/exposure)."""
    if shake > 0.25:
        return []
    ranges: list[list[float]] = []
    current: list[float] | None = None
    for t, score in sorted(samples):
        if score >= 0.35:
            if current is None:
                current = [max(start, t - 0.3), t]
            current[1] = t
        elif current is not None:
            ranges.append(current)
            current = None
    if current is not None:
        ranges.append(current)
    merged = []
    for a, b in ranges:
        b = min(end, b + 0.3)
        if b - a >= 0.5:
            merged.append([round(a, 3), round(b, 3)])
    return merged


def _layer_stack(shot: dict, text_events: list[dict]) -> dict:
    """Manual 8.2: how the image is built, not what it looks like."""
    look = shot["look"]
    effects = [e["type"] for e in shot.get("effects", [])]
    texts = [t for t in text_events if t["start_s"] < shot["end_s"] and t["end_s"] > shot["start_s"]]
    return {
        "base_video": {"camera": shot["motion"]["camera_move"], "shot_size": shot["composition"]["shot_size"]},
        "grade": {
            "shadows": look["shadows"],
            "highlights": look["highlights"],
            "blacks": look["blacks"],
            "contrast": look["contrast"],
            "saturation": look["saturation"],
        },
        "subject_isolation": {
            "matte_likely": any(t.get("occlusion_observed") for t in texts),
        },
        "typography": [
            {
                "text": t["text"],
                "class": (t.get("typography") or {}).get("font_class"),
                "position": t["position"],
                "animation": t["animation"],
            }
            for t in texts
        ],
        "effects": effects + [e["type"] for e in shot["motion"].get("speed_events", [])],
        "transition_in": shot["transition_in"],
    }


def analyse_media(
    media: dict,
    library: Library,
    *,
    settings: Settings | None = None,
    options: AnalysisOptions | None = None,
    progress: Progress = _noop,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    """Analyse one reference or footage item; returns and persists the analysis JSON."""
    settings = settings or get_settings()
    options = options or AnalysisOptions()
    kind = media["kind"]
    if kind not in {"reference", "footage"}:
        raise ValueError("analyse_media handles video references and footage; use analyse_music for audio")
    started = time.monotonic()
    out_dir = analysis_dir(settings, media["content_key"])
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    def check() -> None:
        if cancelled():
            raise AnalysisCancelled("analysis cancelled by user")

    def stage(name: str):
        class _Timer:
            def __enter__(self_inner):
                self_inner.t = time.monotonic()
                check()
                return self_inner

            def __exit__(self_inner, *exc):
                timings[name] = round(time.monotonic() - self_inner.t, 3)
                return False

        return _Timer()

    library.update_media(media["id"], analysis_status="running", error=None)
    source = Path(media["path"])
    with stage("probe"):
        progress("probe", 0.02, "Reading media metadata")
        info = probe(source)
    fps = options.analysis_fps or (30.0 if info.variable_frame_rate or not info.fps else round(info.fps, 3))
    with stage("proxy"):
        progress("normalise", 0.05, "Building analysis proxy (720p, constant frame rate)")
        proxy = make_proxy(source, out_dir / "proxy_m601.mp4", long_edge=720, fps=fps, matrix=effective_matrix(info), full_range=is_full_range(info))
    with stage("signals"):
        progress("signals", 0.1, "Measuring every frame: change, sharpness, optical flow")
        sig_path = out_dir / "signals.npz"
        if sig_path.is_file():
            sig = FrameSignals.load(sig_path)
        else:
            sig = extract_signals(proxy, progress=lambda p: progress("signals", 0.1 + 0.25 * p, "Optical flow and frame statistics"))
            sig.save(sig_path)
    with stage("shots"):
        progress("shots", 0.36, "Detecting shot boundaries and transitions")
        cross = scenedetect_cuts(proxy)
        spans, boundaries = segment_shots(sig, cross_check=cross)
        camera_spans = spans
        if kind == "footage":
            # long camera takes become 3-6 s moments the cut solver can choose between
            spans = split_long_takes(spans, sig)
            if len(spans) != len(camera_spans):
                progress("shots", 0.38, f"Split {len(camera_spans)} take(s) into {len(spans)} moments")
    duration = sig.frame_count / sig.fps
    plan, ocr_frames = _sample_plan(spans, sig.fps, duration, options.max_ocr_samples)
    wanted = set(plan) | (set(ocr_frames) if options.ocr else set())
    shot_of = np.zeros(sig.frame_count, dtype=np.int32)
    for span in spans:
        shot_of[span.start_frame : span.end_frame] = span.index
    per_shot: dict[int, dict[str, list]] = {s.index: {"stats": [], "effects": [], "quality": [], "thumb": None, "faces": None} for s in spans}
    detections = []
    thumbs_dir = out_dir / "thumbs"
    with stage("sampling"):
        progress("sampling", 0.4, f"Sampling {len(wanted)} frames (adaptive around cuts and events)")
        from vanta.analysis import ocr as ocr_mod

        ocr_ok = options.ocr
        done = 0
        with FrameReader(proxy) as reader:
            for index, rgb in reader.iterate():
                if index not in wanted:
                    continue
                check()
                done += 1
                purposes = plan.get(index, [])
                shot_idx = int(shot_of[min(index, len(shot_of) - 1)])
                bucket = per_shot[shot_idx]
                if "thumb" in purposes:
                    faces = detect_faces(rgb)
                    bucket["faces"] = faces
                    bucket["comp"] = composition(rgb, faces)
                    bucket["thumb"] = str(save_jpeg(rgb, thumbs_dir / f"shot_{shot_idx:03d}.jpg", long_edge=480))
                    bucket["thumb_rgb"] = _shrink(rgb, 512)
                    bucket["stats"].append(frame_stats(rgb, face_boxes=[(f["x"], f["y"], f["w"], f["h"]) for f in faces], palette=True))
                if "stats" in purposes:
                    bucket["stats"].append(frame_stats(rgb))
                    q = quality(rgb)
                    bucket["quality"].append((index / sig.fps, q.score, q))
                if "effects" in purposes:
                    bucket["effects"].append(_shrink(rgb, 360))
                if ocr_ok and index in ocr_frames:
                    try:
                        detections.extend(ocr_mod.detect_text(rgb, index / sig.fps))
                    except ocr_mod.OCRUnavailable:
                        ocr_ok = False
                if done % 10 == 0:
                    progress("sampling", 0.4 + 0.25 * done / max(1, len(wanted)), "Colour, composition, quality and OCR")
    with stage("text"):
        progress("text", 0.66, "Tracking on-screen text and typography")
        events = ocr_mod.track_text(detections) if options.ocr else []
        text_events = []
        for event in events:
            if event.duration_s < 0.15 and event.detections < 2:
                continue
            mid = (event.start_s + event.end_s) / 2
            try:
                with FrameReader(proxy) as reader:
                    frame = reader.read_at(mid)
                if frame is not None:
                    event.typography = ocr_mod.describe_typography(frame, event.box, event.text)
            except Exception:
                pass
            text_events.append(event.to_dict())
    shots: list[dict] = []
    for span in spans:
        bucket = per_shot[span.index]
        stats = bucket["stats"] or []
        color = aggregate(stats).to_dict() if stats else None
        motion = summarise_motion(sig, span.start_frame, span.end_frame).to_dict()
        comp = bucket.get("comp")
        q_samples = [(t, s) for t, s, _ in bucket["quality"]]
        q_objs = [q for _, _, q in bucket["quality"]]
        shot = {
            "id": f"{media['id']}_s{span.index:03d}",
            "idx": span.index,
            "start_s": round(span.start_s, 4),
            "end_s": round(span.end_s, 4),
            "start_frame": span.start_frame,
            "end_frame": span.end_frame,
            "duration_s": round(span.duration_s, 4),
            "transition_in": span.transition_in,
            "transition_out": span.transition_out,
            "boundary_confidence": round(span.boundary_confidence, 3),
            "take": span.take,
            "color": color,
            "look": describe_look(aggregate(stats)) if stats else {},
            "motion": motion,
            "composition": comp.to_dict() if comp else None,
            "quality": {
                "score": round(float(np.mean([q.score for q in q_objs])), 4) if q_objs else 0.0,
                "sharpness": round(float(np.mean([q.sharpness for q in q_objs])), 4) if q_objs else 0.0,
                "exposure_ok": round(float(np.mean([q.exposure_ok for q in q_objs])), 4) if q_objs else 0.0,
                "noise": round(float(np.mean([q.noise for q in q_objs])), 4) if q_objs else 0.0,
                "black": bool(q_objs and all(q.black_frame for q in q_objs)),
            },
            "effects": fx.detect_frame_effects(bucket["effects"]) if bucket["effects"] else [],
            "thumb": bucket["thumb"],
            "face_count": len(bucket["faces"] or []),
        }
        if kind == "footage":
            shot["usable_ranges"] = _usable_ranges(q_samples, motion["shake"], span.start_s, span.end_s)
        shot["layers"] = _layer_stack(shot, text_events) if color else {}
        if kind == "footage":
            shot["auto_tags"] = auto_tags(shot)
            shot["hook_score"] = hook_score(shot)
        bucket["effects"] = []  # release frames early on long footage
        shots.append(shot)
    color_profile = None
    if kind == "footage":
        from vanta.core.camera_log import decode_to_display, detect_profile

        key, confidence, reason = detect_profile(info.to_dict(), [s["color"] for s in shots if s.get("color")])
        color_profile = {"key": key, "confidence": confidence, "reason": reason}
        if key != "rec709":
            # log/HDR: tags, embeddings and the VLM must see the image as it will be graded, not flat log
            for shot in shots:
                bucket = per_shot.get(shot["idx"]) or {}
                rgb = bucket.get("thumb_rgb")
                if rgb is None:
                    continue
                decoded = (decode_to_display(rgb.astype(np.float32) / 255, key) * 255 + 0.5).astype(np.uint8)
                bucket["thumb_rgb"] = decoded
                display = frame_stats(decoded)
                shot["auto_tags"] = auto_tags({**shot, "color": {"luma_p50": display.luma_p50, "mid_b": display.mid_b}})
    audio = None
    transcript = None
    if info.has_audio:
        with stage("audio"):
            progress("audio", 0.7, "Analysing audio: tempo, beats, sections, loudness")
            wav = extract_audio(source, out_dir / "audio.wav")
            try:
                audio = analyse_audio(wav).to_dict()
            except Exception as exc:
                audio = {"error": str(exc)}
        if options.transcribe and audio and not audio.get("error") and audio.get("silence_fraction", 1) < 0.95:
            with stage("transcribe"):
                progress("transcribe", 0.75, "Transcribing speech with word timestamps")
                try:
                    from vanta.analysis.transcribe import transcribe

                    t = transcribe(out_dir / "audio.wav")
                    transcript = t.to_dict()
                except Exception as exc:
                    transcript = {"error": f"{type(exc).__name__}: {exc}"}
    cut_times = [s["start_s"] for s in shots[1:]]
    beat_relation = None
    if audio and not audio.get("error") and audio.get("beats"):
        beats = np.asarray([b["time_s"] for b in audio["beats"]])
        downs = np.asarray([b["time_s"] for b in audio["beats"] if b["kind"] == "downbeat"])
        rel = beat_alignment_error(cut_times, beats)
        rel["on_downbeat"] = beat_alignment_error(cut_times, downs)["within_40ms"] if len(downs) else 0.0
        rel["cuts_per_beat"] = round(len(cut_times) / max(1, len(beats)), 3)
        rel["cut_on_drop"] = any(any(abs(c - d) < 0.1 for c in cut_times) for d in audio.get("drops_s", []))
        beat_relation = rel
    pacing = pacing_stats(camera_spans, duration)
    curve = pacing_curve(camera_spans, duration)
    structure = {
        "hook": {
            "first_shot_s": pacing.get("first_shot_s"),
            "text_in_first_second": any(t["start_s"] < 1.0 for t in text_events),
            "cuts_in_first_2s": sum(1 for c in cut_times if c < 2.0),
        },
        "escalation": round(float(np.polyfit(np.arange(len(curve)), curve, 1)[0]), 4) if len(curve) > 2 else 0.0,
        "payoff_at_drop": bool(beat_relation and beat_relation.get("cut_on_drop")),
        "ending": {
            "last_shot_s": shots[-1]["duration_s"] if shots else 0.0,
            "fade_out": shots[-1]["transition_out"] if shots else "end",
            "end_card_text": any(t["end_s"] > duration - 1.5 for t in text_events),
        },
    }
    if options.embeddings and shots:
        with stage("embeddings"):
            progress("embeddings", 0.82, "Visual memory embeddings for retrieval")
            frames = [per_shot[s["idx"]].get("thumb_rgb") for s in shots]
            valid = [(s, f) for s, f in zip(shots, frames, strict=True) if f is not None]
            try:
                from vanta.analysis.embeddings import image_embeddings, semantic_image_embeddings

                model_name, vecs = image_embeddings([f for _, f in valid])
                for (s, _), vec in zip(valid, vecs, strict=True):
                    library.put_embedding(s["id"], model_name, vec)
                    s["embedding_model"] = model_name
                sem = semantic_image_embeddings([f for _, f in valid])
                if sem:
                    sem_name, sem_vecs = sem
                    for (s, _), vec in zip(valid, sem_vecs, strict=True):
                        library.put_embedding(s["id"], sem_name, vec)
            except Exception as exc:
                timings["embeddings_error"] = 0.0
                for s in shots:
                    s.setdefault("notes", []).append(f"embedding failed: {exc}")
    vlm = None
    vlm_meta: dict[str, Any] = {}
    if options.use_vlm and shots:
        with stage("vlm"):
            progress("vlm", 0.88, "Creative director reading the shots (VLM)")
            vlm, vlm_meta = _vlm_reading(kind, shots, per_shot, text_events, audio, pacing, beat_relation)
            if vlm and kind == "footage":
                for reading in vlm.get("shots", []):
                    idx = reading.get("shot")
                    if isinstance(idx, int) and 0 <= idx < len(shots):
                        shots[idx]["vlm"] = reading
                        shots[idx]["description"] = reading.get("description")
    analysis = {
        "schema_version": ANALYSIS_VERSION,
        "media_id": media["id"],
        "kind": kind,
        "color_profile": color_profile,
        "content_key": media["content_key"],
        "title": media.get("title"),
        "source_path": str(source),
        "info": info.to_dict(),
        "proxy": str(proxy),
        "analysis_fps": sig.fps,
        "duration_s": round(duration, 4),
        "shots": shots,
        "boundaries": [b.__dict__ for b in boundaries],
        "pacing": pacing,
        "pacing_curve": curve,
        "text_events": text_events,
        "audio": audio,
        "beat_relation": beat_relation,
        "structure": structure,
        "transcript": transcript,
        "vlm": vlm,
        "vlm_meta": vlm_meta,
        "timings_s": {**timings, "total": round(time.monotonic() - started, 2)},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = out_dir / "analysis.json"
    _atomic_json(path, analysis)
    library.replace_shots(
        media["id"],
        [
            {
                "id": s["id"],
                "idx": s["idx"],
                "start_s": s["start_s"],
                "end_s": s["end_s"],
                "start_frame": s["start_frame"],
                "end_frame": s["end_frame"],
                "description": s.get("description") or ((s.get("vlm") or {}).get("description")),
                "thumb": s["thumb"],
                "data": {k: s.get(k) for k in ("color", "look", "motion", "composition", "quality", "effects", "usable_ranges", "vlm", "layers", "face_count")},
            }
            for s in shots
        ],
    )
    library.update_media(
        media["id"],
        analysis_status="done",
        analysis_version=ANALYSIS_VERSION,
        analysis_path=str(path),
        thumb=shots[0]["thumb"] if shots else None,
    )
    progress("done", 1.0, f"Analysed {len(shots)} shots in {analysis['timings_s']['total']:.0f} s")
    if kind == "reference" and not settings.ingest.keep_reference_media and "downloads" in source.parts:
        # derived features + thumbnails only; the analysis proxy is kept for inspection
        try:
            source.unlink()
        except OSError:
            pass
    return analysis


def _shrink(rgb: np.ndarray, long_edge: int) -> np.ndarray:
    import cv2

    h, w = rgb.shape[:2]
    if max(h, w) <= long_edge:
        return rgb.copy()
    scale = long_edge / max(h, w)
    return cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def auto_tags(shot: dict) -> list[str]:
    """Deterministic, searchable descriptors so selection works even without the VLM."""
    tags: list[str] = []
    comp = shot.get("composition") or {}
    motion = shot.get("motion") or {}
    color = shot.get("color") or {}
    size = comp.get("shot_size")
    if size:
        tags.append(size.replace("_", "-"))
    if shot.get("face_count"):
        tags += ["person", "face"]
    move = motion.get("camera_move")
    if move and move != "static":
        tags.append(move.replace("_", "-"))
    elif move == "static":
        tags.append("locked-off")
    intensity = motion.get("intensity", 0)
    tags.append("high-motion" if intensity > 0.5 else ("calm" if intensity < 0.2 else "moving"))
    luma = color.get("luma_p50", 0.5)
    tags.append("night" if luma < 0.2 else ("bright" if luma > 0.6 else "midtone"))
    if color.get("mid_b", 0) > 6:
        tags.append("warm")
    elif color.get("mid_b", 0) < -3:
        tags.append("cool")
    if comp.get("negative_space", 0) > 0.45:
        tags.append("negative-space")
    if any(e.get("type") == "speed_ramp" for e in motion.get("speed_events", [])):
        tags.append("action-peak")
    return tags


def hook_score(shot: dict) -> float:
    """Heuristic opener strength: sharp, well exposed, striking motion or a clear subject."""
    q = shot.get("quality") or {}
    motion = shot.get("motion") or {}
    comp = shot.get("composition") or {}
    color = shot.get("color") or {}
    score = (
        0.3 * q.get("sharpness", 0.5)
        + 0.2 * q.get("exposure_ok", 0.5)
        + 0.2 * min(1.0, motion.get("intensity", 0.3) * 1.4)
        + 0.15 * (1.0 if shot.get("face_count") or comp.get("subject_size", 0) > 0.08 else 0.3)
        + 0.15 * min(1.0, color.get("contrast", 0.5) / 0.75)
    )
    return round(float(np.clip(score, 0, 1)), 3)


FOOTAGE_BATCH = 16


def _vlm_reading(kind, shots, per_shot, text_events, audio, pacing, beat_relation) -> tuple[dict | None, dict]:
    from vanta.reasoning import prompts
    from vanta.reasoning.llm import (
        ModelOutputInvalid,
        ModelUnavailable,
        contact_sheet,
        dumps_compact,
        get_provider,
    )

    provider = get_provider()
    if kind == "footage":
        return _vlm_footage_batched(provider, shots, per_shot)
    images, labels = [], []
    picks = shots if len(shots) <= 24 else [shots[int(i)] for i in np.linspace(0, len(shots) - 1, 24)]
    for s in picks:
        rgb = per_shot[s["idx"]].get("thumb_rgb")
        if rgb is not None:
            images.append(rgb)
            labels.append(f"#{s['idx']} {s['start_s']:.1f}s")
    if not images:
        return None, {"error": "no key frames"}
    sheet = contact_sheet(images, labels, cols=6 if len(images) > 12 else 4)
    if kind == "reference":
        facts = {
            "pacing": pacing,
            "beat_relation": beat_relation,
            "bpm": audio.get("bpm") if audio and not audio.get("error") else None,
            "drops_s": audio.get("drops_s") if audio and not audio.get("error") else None,
            "text": [{k: t[k] for k in ("text", "start_s", "end_s", "animation", "occlusion_observed")} for t in text_events[:20]],
            "shots": [
                {
                    "i": s["idx"],
                    "t": [s["start_s"], s["end_s"]],
                    "in": s["transition_in"],
                    "cam": s["motion"]["camera_move"],
                    "look": s["look"],
                    "fx": [e["type"] for e in s["effects"]] + [e["type"] for e in s["motion"]["speed_events"]],
                }
                for s in shots[:60]
            ],
        }
    schema, template, version = prompts.ReferenceReading, prompts.REFERENCE_PROMPT, prompts.REFERENCE_PROMPT_VERSION
    try:
        result = provider.generate_json(
            system=prompts.SYSTEM_EDITOR,
            prompt=template.format(facts=dumps_compact(facts)),
            schema=schema,
            images=[sheet],
            prompt_version=version,
            num_ctx=12288,
        )
    except (ModelUnavailable, ModelOutputInvalid) as exc:
        return None, {"error": str(exc), "prompt_version": version}
    return result.data, {"model": result.model, "seconds": result.seconds, "prompt_version": version, "usage": result.usage}


def _vlm_footage_batched(provider, shots, per_shot) -> tuple[dict | None, dict]:
    """Log *every* shot/moment: one contact sheet per 16 moments, merged into one reading."""
    from vanta.reasoning import prompts
    from vanta.reasoning.llm import (
        ModelOutputInvalid,
        ModelUnavailable,
        contact_sheet,
        dumps_compact,
    )

    readings: list[dict] = []
    seconds = 0.0
    errors: list[str] = []
    model = None
    for start in range(0, len(shots), FOOTAGE_BATCH):
        batch = shots[start : start + FOOTAGE_BATCH]
        images, labels = [], []
        for s in batch:
            rgb = per_shot[s["idx"]].get("thumb_rgb")
            if rgb is not None:
                images.append(rgb)
                labels.append(f"#{s['idx']} {s['start_s']:.1f}s")
        if not images:
            continue
        facts = [
            {
                "shot": s["idx"],
                "t": [s["start_s"], s["end_s"]],
                "take": s.get("take"),
                "cam": s["motion"]["camera_move"],
                "size": (s.get("composition") or {}).get("shot_size"),
                "faces": s["face_count"],
                "quality": s["quality"]["score"],
                "look": s["look"],
                "tags": s.get("auto_tags"),
            }
            for s in batch
        ]
        try:
            result = provider.generate_json(
                system=prompts.SYSTEM_EDITOR,
                prompt=prompts.FOOTAGE_PROMPT.format(facts=dumps_compact(facts)),
                schema=prompts.FootageReading,
                images=[contact_sheet(images, labels, cols=4)],
                prompt_version=prompts.FOOTAGE_PROMPT_VERSION,
                num_ctx=12288,
            )
        except ModelUnavailable as exc:
            errors.append(str(exc))
            break  # the provider is down: stop asking, deterministic tags remain
        except ModelOutputInvalid as exc:
            errors.append(str(exc))
            continue
        model = result.model
        seconds += result.seconds
        valid = {s["idx"] for s in batch}
        readings.extend(r for r in result.data.get("shots", []) if r.get("shot") in valid)
    if not readings:
        return None, {"error": "; ".join(errors) or "no readings", "prompt_version": prompts.FOOTAGE_PROMPT_VERSION}
    return {"shots": readings}, {"model": model, "seconds": round(seconds, 2), "prompt_version": prompts.FOOTAGE_PROMPT_VERSION, "batches": (len(shots) + FOOTAGE_BATCH - 1) // FOOTAGE_BATCH, "errors": errors}


def analyse_music(media: dict, library: Library, *, settings: Settings | None = None, progress: Progress = _noop, embeddings: bool = True) -> dict:
    """Music library indexing (manual 14): beat grid, sections, energy, key, loudness, CLAP."""
    settings = settings or get_settings()
    out_dir = analysis_dir(settings, media["content_key"])
    out_dir.mkdir(parents=True, exist_ok=True)
    library.update_media(media["id"], analysis_status="running", error=None)
    progress("audio", 0.1, "Decoding audio")
    wav = extract_audio(media["path"], out_dir / "audio.wav")
    progress("audio", 0.3, "Tempo, beat grid, downbeats, sections, drops")
    audio = analyse_audio(wav, loudness_path=media["path"]).to_dict()
    clap_model = None
    try:
        if not embeddings:
            raise RuntimeError("CLAP embedding disabled for this run")
        from vanta.analysis.audio import load_audio
        from vanta.analysis.embeddings import audio_embeddings

        progress("embeddings", 0.7, "CLAP audio embedding")
        y, sr = load_audio(wav, sr=48000)
        windows = [y[int(a * sr) : int(min(len(y) / sr, a + 10) * sr)] for a in np.arange(0, max(1, len(y) / sr - 5), 10)][:12]
        result = audio_embeddings([w for w in windows if len(w) > sr], sr)
        if result:
            clap_model, vecs = result
            library.put_embedding(media["id"], clap_model, vecs.mean(axis=0))
    except Exception as exc:
        audio.setdefault("extra", {})["clap_error"] = str(exc)
    analysis = {
        "schema_version": ANALYSIS_VERSION,
        "media_id": media["id"],
        "kind": media["kind"],
        "content_key": media["content_key"],
        "title": media.get("title"),
        "source_path": media["path"],
        "duration_s": audio["duration_s"],
        "audio": audio,
        "clap_model": clap_model,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = out_dir / "analysis.json"
    _atomic_json(path, analysis)
    library.update_media(media["id"], analysis_status="done", analysis_version=ANALYSIS_VERSION, analysis_path=str(path))
    progress("done", 1.0, f"{audio['bpm']:.0f} BPM, {len(audio['sections'])} sections, {len(audio['drops_s'])} drops")
    return analysis
