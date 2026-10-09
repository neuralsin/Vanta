"""Style Capsules from a reference analysis (manual 6, 8.4, 8.5, 9.4).

Deterministic measurements become OBSERVED statements; implementation
hypotheses are INFERRED. VLM technique readings are merged by time overlap and
confidence is computed from evidence (detector strength, cross-source
agreement, temporal consistency, deterministic confirmation) — never from a
model's self-report alone.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from vanta.core.schemas import Evidence, StyleCapsule

# technique -> (dimension, implementation template, fallback, default purpose)
TECHNIQUES: dict[str, tuple[str, str | None, str | None, str]] = {
    "hard_cut": ("rhythm", None, None, "maintain momentum"),
    "dissolve": ("rhythm", "resolve:cross_dissolve", "fcpxml:cross_dissolve", "soften a time or mood shift"),
    "fade_black": ("rhythm", "resolve:dip_to_black", "fcpxml:fade", "section break / ending"),
    "fade_white": ("rhythm", "resolve:dip_to_white", "fusion:flash", "dreamy or energetic reset"),
    "flash": ("fusion", "fusion:flash_transition", "resolve:dip_to_white", "punctuate a beat or drop"),
    "whip": ("fusion", "fusion:whip_pan", "resolve:cross_dissolve", "energetic directional transition"),
    "zoom": ("fusion", "fusion:zoom_punch", "resolve:cross_dissolve", "impact transition on a strong beat"),
    "zoom_transition": ("fusion", "fusion:zoom_punch", "resolve:cross_dissolve", "impact transition on a strong beat"),
    "glitch": ("fusion", "fusion:glitch", "resolve:cross_dissolve", "digital energy accent"),
    "speed_ramp": ("motion", "resolve:speed_ramp", "prerender:speed_ramp", "emphasise motion peak"),
    "freeze_frame": ("motion", "fusion:freeze_frame", "resolve:speed_0", "hold attention on a moment"),
    "punch_in": ("motion", "resolve:dynamic_zoom", "fusion:zoom_punch", "emphasis without a new shot"),
    "punch_out": ("motion", "resolve:dynamic_zoom", "fusion:zoom_punch", "reveal context"),
    "text_behind_subject": ("masking", "fusion:text_behind_subject", "fusion:text_2_5d", "spatial integration of title with scene"),
    "subject_cutout": ("masking", "fusion:subject_outline", "global", "separate subject from background"),
    "mask_reveal": ("masking", "fusion:masked_reveal", "fusion:slide", "reveal through scene geometry"),
    "kinetic_typography": ("typography", "fusion:kinetic_words", "fusion:text_basic", "rhythmic emphasis of words"),
    "captions": ("typography", "fusion:word_highlight", "fusion:text_basic", "legibility without sound"),
    "tracked_text": ("typography", "fusion:tracked_callout", "fusion:text_basic", "attach information to a moving subject"),
    "text_3d": ("fusion", "fusion:text3d_hover", "fusion:text_2_5d", "spatial emphasis"),
    "glow": ("fusion", "fusion:subject_glow", "drx:polish", "emphasise practical lights / subject"),
    "bloom": ("fusion", "fusion:cinematic_bloom", "drx:polish", "filmic highlight halation"),
    "vignette": ("color", "drx:polish", "lut", "draw the eye to centre"),
    "grain": ("color", "drx:polish", "lut", "filmic texture"),
    "chromatic_aberration": ("fusion", "fusion:chromatic_aberration", None, "lens-character accent"),
    "letterbox": ("composition", "resolve:output_blanking", "fusion:letterbox", "cinematic framing"),
    "split_screen": ("composition", "fusion:split_screen", None, "compare or show simultaneity"),
    "picture_in_picture": ("composition", "fusion:pip", None, "show context with detail"),
    "parallax": ("fusion", "fusion:parallax", None, "depth from stills"),
    "shake": ("motion", "fusion:camera_shake", None, "impact / energy"),
    "motion_blur": ("motion", "fusion:speed_blur", None, "smooth fast motion"),
}


def _capsule_id(reference_id: str, technique: str, start: float) -> str:
    digest = hashlib.sha1(f"{reference_id}:{technique}:{start:.2f}".encode()).hexdigest()[:12]
    return f"cap_{digest}"


def _evidence(analysis: dict, times: list[float]) -> list[Evidence]:
    out: list[Evidence] = []
    shots = analysis.get("shots", [])
    for t in times:
        shot = next((s for s in shots if s["start_s"] <= t < s["end_s"]), shots[-1] if shots else None)
        if shot and shot.get("thumb"):
            out.append(Evidence(source_id=analysis["media_id"], artifact=shot["thumb"], frame_or_time=f"{t:.2f}s"))
    if not out:
        out.append(Evidence(source_id=analysis["media_id"], artifact="analysis.json", frame_or_time=f"{times[0]:.2f}s" if times else None))
    return out


def _make(
    analysis: dict,
    technique: str,
    window: tuple[float, float],
    observed: str,
    *,
    confidence: float,
    method: str,
    alternative: str = "",
    method_known: bool = True,
    purpose: str | None = None,
    conditions: dict | None = None,
    measurements: dict | None = None,
    sources: list[str] | None = None,
    dimension: str | None = None,
) -> StyleCapsule:
    dim, template, fallback, default_purpose = TECHNIQUES.get(technique, ("fusion", None, None, "style accent"))
    start, end = window
    if end <= start:
        end = start + 0.04
    return StyleCapsule(
        id=_capsule_id(analysis["media_id"], technique, start),
        reference_id=analysis["media_id"],
        technique=technique,
        dimension=dimension or dim,
        window_s=(round(max(0.0, start), 3), round(end, 3)),
        observed=observed,
        inferred={"method": method, "alternative": alternative, "method_known": method_known},
        evidence=_evidence(analysis, [start, (start + end) / 2]),
        purpose=purpose or default_purpose,
        conditions=conditions or {},
        implementation={"template": template, "fallback": fallback},
        confidence=float(np.clip(confidence, 0.0, 0.99)),
        sources=sources or ["deterministic"],
        measurements=measurements or {},
    )


def _shot_conditions(shot: dict) -> dict[str, Any]:
    comp = shot.get("composition") or {}
    motion = shot.get("motion") or {}
    color = shot.get("color") or {}
    return {
        "camera": "stable" if motion.get("stability", 0) > 0.7 else "moving",
        "subject": "person" if shot.get("face_count") else "object_or_scene",
        "negative_space": comp.get("negative_space", 0) > 0.35,
        "shot_size": comp.get("shot_size"),
        "lighting": "night" if color.get("luma_p50", 0.5) < 0.22 else "day",
    }


def build_capsules(analysis: dict) -> list[StyleCapsule]:
    capsules: list[StyleCapsule] = []
    shots = analysis.get("shots", [])
    duration = analysis.get("duration_s", 0.0)
    pacing = analysis.get("pacing", {})
    # ---------------- rhythm: pacing + beat relation (observed statistics)
    if shots:
        beat = analysis.get("beat_relation") or {}
        observed = (
            f"{len(shots)} shots in {duration:.1f}s; median shot {pacing.get('median_s', 0):.2f}s "
            f"(p10 {pacing.get('p10_s', 0):.2f}s, p90 {pacing.get('p90_s', 0):.2f}s), "
            f"{pacing.get('cuts_per_minute', 0):.0f} cuts/min"
        )
        if beat:
            observed += f"; {beat.get('within_40ms', 0) * 100:.0f}% of cuts within 40 ms of a beat"
        capsules.append(
            _make(
                analysis,
                "pacing",
                (0.0, max(duration, 0.1)),
                observed,
                confidence=0.95,
                method="measured shot statistics",
                purpose="rhythm and energy of the edit",
                dimension="rhythm",
                measurements={"pacing": pacing, "curve": analysis.get("pacing_curve"), "beat": beat, "structure": analysis.get("structure")},
            )
        )
        if beat and beat.get("within_40ms", 0) >= 0.6:
            capsules.append(
                _make(
                    analysis,
                    "cut_on_beat",
                    (0.0, max(duration, 0.1)),
                    f"cuts land on the beat grid ({beat['within_40ms'] * 100:.0f}% within 40 ms, {beat.get('on_downbeat', 0) * 100:.0f}% on downbeats)",
                    confidence=0.6 + 0.35 * beat["within_40ms"],
                    method="beat-snapped cutting",
                    purpose="lock picture to music",
                    dimension="rhythm",
                    measurements=beat,
                )
            )
    # ---------------- transitions
    for boundary in analysis.get("boundaries", []):
        kind = boundary["kind"]
        if kind == "hard_cut":
            continue
        t = boundary["frame"] / analysis.get("analysis_fps", 30.0)
        capsules.append(
            _make(
                analysis,
                kind,
                (t - 0.2, t + 0.2),
                f"{kind.replace('_', ' ')} between shots at {t:.2f}s",
                confidence=boundary["confidence"],
                method=f"{kind} transition",
                alternative="hard cut with motion blur" if kind in {"whip", "zoom"} else "",
                measurements=boundary.get("evidence", {}),
            )
        )
    # ---------------- per-shot motion and frame effects
    for shot in shots:
        cond = _shot_conditions(shot)
        for event in (shot.get("motion") or {}).get("speed_events", []):
            t = event["start_s"]
            capsules.append(
                _make(
                    analysis,
                    event["type"],
                    (t, min(shot["end_s"], t + 0.8)),
                    f"{event['type'].replace('_', ' ')} inside shot {shot['idx']}",
                    confidence=event.get("confidence", 0.5),
                    method="retime" if event["type"] == "speed_ramp" else event["type"],
                    alternative="optical-flow interpolated slow motion" if event["type"] == "speed_ramp" else "",
                    conditions=cond,
                    measurements=event,
                )
            )
        for effect in shot.get("effects", []):
            capsules.append(
                _make(
                    analysis,
                    effect["type"],
                    (shot["start_s"], shot["end_s"]),
                    f"{effect['type'].replace('_', ' ')} measured on shot {shot['idx']}",
                    confidence=effect.get("confidence", 0.5),
                    method=f"{effect['type']} applied in finishing",
                    conditions=cond,
                    measurements=effect,
                )
            )
    # ---------------- typography
    for event in analysis.get("text_events", []):
        typo = event.get("typography") or {}
        technique = "captions" if len(event.get("words", [])) >= 3 and event["position"][1] > 0.55 else "kinetic_typography" if event["animation"] in {"word_by_word", "typewriter", "scale"} else "title"
        capsules.append(
            _make(
                analysis,
                technique if technique != "title" else "text_basic",
                (event["start_s"], max(event["end_s"], event["start_s"] + 0.1)),
                f"text '{event['text'][:40]}' ({typo.get('font_class', 'sans')}, {typo.get('case', '')} case) at "
                f"({event['position'][0]:.2f}, {event['position'][1]:.2f}), {event['animation']} animation",
                confidence=min(0.95, 0.5 + 0.4 * event.get("mean_score", 0.7) + 0.02 * event.get("detections", 1)),
                method=f"{event['animation']} text overlay",
                dimension="typography",
                purpose="communicate the hook" if event["start_s"] < 1.5 else "support the story",
                measurements={k: event.get(k) for k in ("box", "typography", "animation", "position", "duration_s")},
            )
        )
        if event.get("occlusion_observed"):
            shot = next((s for s in shots if s["start_s"] <= event["start_s"] < s["end_s"]), None)
            capsules.append(
                _make(
                    analysis,
                    "text_behind_subject",
                    (event["start_s"], max(event["end_s"], event["start_s"] + 0.1)),
                    f"text '{event['text'][:30]}' is partially hidden then reappears while the subject passes",
                    confidence=0.62,
                    method="subject_matte",
                    alternative="precomposited_render",
                    purpose="spatial integration of title with scene",
                    conditions=_shot_conditions(shot) if shot else {},
                    measurements={"text": event["text"]},
                )
            )
    # ---------------- colour (relational look intent per reference)
    looks = [s for s in shots if s.get("color")]
    if looks:
        vec = {k: float(np.median([s["color"][k] for s in looks])) for k in ("shadow_a", "shadow_b", "high_a", "high_b", "contrast", "black_point", "chroma_mean", "luma_p50")}
        skin = [s["color"].get("skin_hue") for s in looks if s["color"].get("skin_hue") is not None]
        separation = vec["high_b"] - vec["shadow_b"]
        parts = []
        if separation > 4:
            parts.append(f"warm highlights against cooler shadows (Δb* {separation:.1f})")
        elif separation < -4:
            parts.append(f"cool highlights over warmer shadows (Δb* {separation:.1f})")
        parts.append(f"contrast {vec['contrast']:.2f}, black point {vec['black_point']:.3f}, mean chroma {vec['chroma_mean']:.1f}")
        if skin:
            parts.append(f"skin hue held near {np.median(skin):.0f}°")
        capsules.append(
            _make(
                analysis,
                "look",
                (0.0, max(duration, 0.1)),
                "; ".join(parts),
                confidence=0.85 if len(looks) >= 3 else 0.6,
                method="primary grade + creative look (node graph not observable)",
                method_known=False,
                purpose="consistent mood across shots",
                dimension="color",
                measurements={"median": vec, "per_shot": [{"shot": s["idx"], **{k: s["color"][k] for k in vec}} for s in looks]},
            )
        )
    # ---------------- composition + motion distributions
    if shots:
        sizes: dict[str, int] = {}
        moves: dict[str, int] = {}
        for s in shots:
            size = (s.get("composition") or {}).get("shot_size", "unknown")
            sizes[size] = sizes.get(size, 0) + 1
            move = (s.get("motion") or {}).get("camera_move", "unknown")
            moves[move] = moves.get(move, 0) + 1
        capsules.append(
            _make(
                analysis,
                "framing",
                (0.0, max(duration, 0.1)),
                "shot sizes " + ", ".join(f"{k} {v}" for k, v in sorted(sizes.items(), key=lambda kv: -kv[1])),
                confidence=0.8,
                method="camera framing choices",
                dimension="composition",
                purpose="visual variety and subject emphasis",
                measurements={"shot_sizes": sizes, "thirds": float(np.mean([(s.get("composition") or {}).get("thirds_score", 0) for s in shots]))},
            )
        )
        capsules.append(
            _make(
                analysis,
                "camera_motion",
                (0.0, max(duration, 0.1)),
                "camera moves " + ", ".join(f"{k} {v}" for k, v in sorted(moves.items(), key=lambda kv: -kv[1])),
                confidence=0.8,
                method="camera movement",
                dimension="motion",
                purpose="energy and continuity",
                measurements={"moves": moves, "intensity": float(np.mean([(s.get("motion") or {}).get("intensity", 0) for s in shots]))},
            )
        )
    # ---------------- audio
    audio = analysis.get("audio") or {}
    if audio and not audio.get("error"):
        capsules.append(
            _make(
                analysis,
                "music",
                (0.0, max(duration, 0.1)),
                f"{audio.get('bpm', 0):.0f} BPM, {len(audio.get('drops_s', []))} drop(s), mean energy {audio.get('mean_energy', 0):.2f}",
                confidence=0.6 + 0.3 * audio.get("bpm_confidence", 0),
                method="music bed (analysis signal only, never reused)",
                dimension="audio",
                purpose="drive pacing and payoff",
                measurements={k: audio.get(k) for k in ("bpm", "drops_s", "mean_energy", "mean_brightness", "key", "sections")},
            )
        )
    return _merge_vlm(analysis, capsules)


def _merge_vlm(analysis: dict, capsules: list[StyleCapsule]) -> list[StyleCapsule]:
    """Fold VLM technique readings in; agreement raises confidence, VLM-only stays modest."""
    reading = analysis.get("vlm") or {}
    model = (analysis.get("vlm_meta") or {}).get("model", "vlm")
    for tech in reading.get("techniques", []):
        name = tech["technique"]
        if name in {"none", "hard_cut"}:
            continue
        start, end = float(tech["start_s"]), float(max(tech["end_s"], tech["start_s"] + 0.1))
        match = next(
            (
                c
                for c in capsules
                if (c.technique == name or (name == "zoom_transition" and c.technique == "zoom"))
                and c.window_s[0] < end + 0.5
                and c.window_s[1] > start - 0.5
            ),
            None,
        )
        if match:
            agreed = min(0.98, 1 - (1 - match.confidence) * (1 - 0.5 * float(tech["confidence"])))
            match.confidence = round(agreed, 4)
            match.sources = sorted(set(match.sources) | {model})
            match.inferred = {
                **match.inferred,
                "method": tech["inferred_method"] or match.inferred.get("method"),
                "alternative": tech.get("alternative_method") or match.inferred.get("alternative", ""),
                "method_known": bool(tech.get("method_known", True)),
            }
            match.purpose = tech.get("purpose") or match.purpose
            if tech.get("conditions"):
                match.conditions = {**match.conditions, "vlm": tech["conditions"]}
        else:
            # VLM-only observation: never above 0.6 without deterministic confirmation
            capsule = _make(
                analysis,
                name,
                (start, end),
                tech["observed"],
                confidence=min(0.6, 0.6 * float(tech["confidence"])),
                method=tech["inferred_method"],
                alternative=tech.get("alternative_method", ""),
                method_known=bool(tech.get("method_known", True)),
                purpose=tech.get("purpose"),
                conditions={"vlm": tech.get("conditions", [])},
                sources=[model],
                dimension=tech.get("dimension"),
            )
            capsules.append(capsule)
    if reading.get("color_intent"):
        for c in capsules:
            if c.technique == "look":
                c.inferred = {**c.inferred, "intent": reading["color_intent"]}
                c.sources = sorted(set(c.sources) | {model})
    for key, technique in (("typography_intent", "text_basic"), ("rhythm_intent", "pacing")):
        if reading.get(key):
            for c in capsules:
                if c.technique == technique:
                    c.inferred = {**c.inferred, "intent": reading[key]}
    return capsules
