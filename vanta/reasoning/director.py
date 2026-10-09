"""Director passes (manual 10): Observe -> Classify -> Compare -> Choose.

Each pass asks the VLM for *intent* in a fixed schema and falls back to a
deterministic director built from the Style Brain when the model is
unavailable, times out, or returns invalid output. Outputs always record which
path produced them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from vanta.config import get_settings
from vanta.core.schemas import EditSpec
from vanta.reasoning import prompts
from vanta.reasoning.footage_graph import FootageGraph
from vanta.reasoning.llm import (
    LLMProvider,
    ModelOutputInvalid,
    ModelUnavailable,
    contact_sheet,
    dumps_compact,
)
from vanta.solvers.cut import Slot
from vanta.style.brain import StylePackage

ANIMATION_TO_TEMPLATE = {
    "static": "text_basic",
    "slide": "slide",
    "scale": "scale_pop",
    "typewriter": "typewriter",
    "word_by_word": "kinetic_words",
    "fade": "text_basic",
}


@dataclass
class DirectorResult:
    data: dict[str, Any]
    source: str  # model name or "deterministic"
    seconds: float = 0.0
    prompt_version: str | None = None
    error: str | None = None


def _brief_words(spec: EditSpec) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", spec.goal.casefold()) if len(w) > 3}


def title_template(pkg: StylePackage, spec: EditSpec, registry: set[str]) -> str:
    anim = (pkg.typography or {}).get("animation", "static")
    candidate = ANIMATION_TO_TEMPLATE.get(anim, "text_basic")
    if spec.sliders.masking >= 0.35 and (pkg.masking or {}).get("text_behind_subject_share", 0) > 0 and "text_behind_subject" in registry:
        candidate = "text_behind_subject"
    if spec.sliders.three_d >= 0.6 and "text3d_hover" in registry:
        candidate = "text3d_hover"  # still needs a spatial reason; effects planner downgrades if absent
    if "cinematic" in spec.goal.casefold() and candidate == "text_basic" and "tracking_reveal" in registry:
        candidate = "tracking_reveal"
    return candidate if candidate in registry else "text_basic"


def deterministic_assembly(spec: EditSpec, pkg: StylePackage, slots: list[Slot], graph: FootageGraph, registry: set[str]) -> dict:
    words = _brief_words(spec)
    tags = {t.casefold() for c in graph.candidates for t in c.tags if t}
    preferred = sorted(words & tags)[:6]
    slot_intents = []
    for slot in slots:
        if slot.role == "hook":
            intent, section = "strongest opener: arresting motion or the hero subject, readable in under a second", "hook"
        elif slot.is_drop:
            intent, section = "biggest visual change of the edit on the drop: high motion, new angle", "drop"
        elif slot.role == "outro":
            intent, section = "resolving shot: wider, calmer, leaves space for the end card", "outro"
        elif slot.section == "build":
            intent, section = "rising energy: progressively tighter or faster shots", "build"
        elif slot.section in {"breakdown", "intro"}:
            intent, section = "breather: atmosphere and context", "breather"
        else:
            intent, section = "story continuity: varied angles of the subject", "setup"
        slot_intents.append(
            {"slot": slot.index, "section": section, "intent": intent, "preferred_tags": preferred, "preferred_shots": [], "avoid_shots": [], "energy": slot.energy}
        )
    texts = []
    title = spec.title_text or next(iter(spec.locks.get("text", [])), None)
    if title:
        texts.append({"text": title, "role": "title", "slot": 0, "template": title_template(pkg, spec, registry), "reason": "brief title shown during the hook"})
    for extra in spec.locks.get("text", [])[1:3]:
        target = max(1, len(slots) // 2) if len(slots) > 2 else 0
        texts.append({"text": extra, "role": "hook_line", "slot": target, "template": "text_basic", "reason": "locked text from the brief"})
    return {
        "story": f"{spec.goal}: open on the strongest image, build with the music, pay off on the drop, resolve calmly.",
        "hook_strategy": "fast first cut on the hero subject with the title",
        "slots": slot_intents,
        "texts": texts,
        "effects": [],
        "color_intent": (pkg.color or {}).get("intent") or "keep natural skin, deepen blacks slightly, keep the reference's warm/cool relationship",
        "rationale": ["deterministic director (style statistics + brief keywords)"],
    }


def assembly_pass(
    provider: LLMProvider,
    spec: EditSpec,
    pkg: StylePackage,
    slots: list[Slot],
    graph: FootageGraph,
    *,
    music_desc: str,
    registry: set[str],
    features: str | None = None,
) -> DirectorResult:
    fallback = deterministic_assembly(spec, pkg, slots, graph, registry)
    images, labels = [], []
    shot_labels = graph.labels()
    for cand in graph.showcase(24):
        thumb = graph.shots[cand.shot_id].get("thumb")
        if thumb:
            img = cv2.imread(thumb)
            if img is not None:
                images.append(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                labels.append(shot_labels[cand.shot_id])
    prompt = prompts.PLAN_PROMPT.format(
        duration=spec.duration_s,
        platform=spec.platform,
        goal=spec.goal,
        locks=dumps_compact(spec.locks),
        never=dumps_compact(spec.never),
        style=dumps_compact(pkg.summary_for_prompt()),
        music=music_desc,
        slots=dumps_compact([{"slot": s.index, "t": [round(s.start_s, 2), round(s.end_s, 2)], "section": s.section, "beat": s.beat_kind, "drop": s.is_drop} for s in slots]),
        footage="\n".join(graph.summary_lines()),
        features=features or ", ".join(sorted(registry)),
    )
    try:
        result = provider.generate_json(
            system=prompts.SYSTEM_EDITOR,
            prompt=prompt,
            schema=prompts.AssemblyPlan,
            images=[contact_sheet(images, labels, cols=6)] if images else None,
            prompt_version=prompts.PLAN_PROMPT_VERSION,
            num_ctx=16384,
            think=get_settings().models.director_think,
        )
    except (ModelUnavailable, ModelOutputInvalid) as exc:
        return DirectorResult(fallback, "deterministic", error=str(exc), prompt_version=prompts.PLAN_PROMPT_VERSION)
    data = result.data
    # validate model choices against reality: unknown shots/templates are dropped, never trusted
    inverse = {label: sid for sid, label in shot_labels.items()}
    slot_ids = {s.index for s in slots}
    clean_slots = {}
    for item in data.get("slots", []):
        if item.get("slot") not in slot_ids:
            continue
        for key in ("preferred_shots", "avoid_shots"):
            item[key] = [sid for sid in (graph.resolve_label(s, inverse) for s in item.get(key, [])) if sid]
        clean_slots[item["slot"]] = item
    data["slots"] = [clean_slots.get(s.index) or fb for s, fb in zip(slots, fallback["slots"], strict=True)]
    from vanta.reasoning.features import BY_KEY

    text_keys = {k for k in registry if k in BY_KEY and BY_KEY[k].kind == "text"} | {"text_basic"}
    data["texts"] = [t for t in data.get("texts", []) if t.get("template") in text_keys and t.get("slot") in slot_ids] or fallback["texts"]
    # locked text from the brief always wins over model wording (precedence: brief > model)
    if fallback["texts"] and fallback["texts"][0]["text"] not in {t["text"] for t in data["texts"]}:
        data["texts"] = fallback["texts"] + [t for t in data["texts"] if t["role"] != "title"]
    data["effects"] = [e for e in data.get("effects", []) if e.get("template") in registry and e.get("slot") in slot_ids]
    return DirectorResult(data, result.model, result.seconds, result.prompt_version)


def deterministic_grade_intents(pkg: StylePackage, shot_ids: list[str], spec: EditSpec) -> dict:
    relation = (pkg.color or {}).get("relations", {}).get("warm_cool_separation", 0.0)
    separation = "strong" if abs(relation) > 8 else ("subtle" if abs(relation) > 3 else "none")
    target = (pkg.color or {}).get("target_look", {})
    contrast = "punchier" if target.get("contrast", 0.6) > 0.72 else ("softer" if target.get("contrast", 0.6) < 0.5 else "keep")
    saturation = "richer" if target.get("chroma_mean", 20) > 28 else ("muted" if target.get("chroma_mean", 20) < 14 else "keep")
    deepen = target.get("black_point", 0.03) < 0.02
    return {
        "global_intent": (pkg.color or {}).get("intent") or "natural, consistent look",
        "avoid": [n for n in pkg.negatives if any(k in n for k in ("teal", "skin", "orange", "saturation"))],
        "shots": [
            {"shot_id": sid, "intent": "follow the profile's tonal relationships", "warmth": "neutral", "contrast": contrast, "saturation": saturation, "separation": separation, "protect_skin": spec.sliders.skin_priority > 0.4, "deepen_blacks": deepen}
            for sid in shot_ids
        ],
    }


def grade_pass(provider: LLMProvider, pkg: StylePackage, spec: EditSpec, shots: list[dict]) -> DirectorResult:
    fallback = deterministic_grade_intents(pkg, [s["id"] for s in shots], spec)
    prompt = prompts.GRADE_PROMPT.format(
        reference=dumps_compact({"intent": (pkg.color or {}).get("intent"), "relations": (pkg.color or {}).get("relations"), "target": (pkg.color or {}).get("target_look")}),
        rules=dumps_compact([r.get("statement") for r in pkg.rules if r.get("dimension") == "color"][:10] + pkg.negatives),
        intensity=spec.sliders.color_intensity,
        skin=spec.sliders.skin_priority,
        shots="\n".join(
            f"{s['id']}: {s.get('look')} faces={s.get('faces', 0)} {s.get('description', '')}"
            + (f" [camera {s['profile']}: stats are log-encoded; it is decoded to Rec.709 before grading]" if s.get("profile", "rec709") != "rec709" else "")
            for s in shots
        ),
    )
    sheet = _decoded_sheet(shots)
    try:
        result = provider.generate_json(
            system=prompts.SYSTEM_EDITOR,
            prompt=prompt,
            schema=prompts.GradeIntentPlan,
            images=[sheet] if sheet is not None else None,
            prompt_version=prompts.GRADE_PROMPT_VERSION,
            num_ctx=12288,
            think=get_settings().models.director_think,
        )
    except (ModelUnavailable, ModelOutputInvalid) as exc:
        return DirectorResult(fallback, "deterministic", error=str(exc), prompt_version=prompts.GRADE_PROMPT_VERSION)
    known = {s["id"] for s in shots}
    by_id = {s["shot_id"]: s for s in result.data.get("shots", []) if s.get("shot_id") in known}
    result.data["shots"] = [by_id.get(fb["shot_id"], fb) for fb in fallback["shots"]]
    return DirectorResult(result.data, result.model, result.seconds, result.prompt_version)


def _decoded_sheet(shots: list[dict]) -> np.ndarray | None:
    """Contact sheet of the timeline shots as the grader will see them (camera log decoded)."""
    from vanta.core.camera_log import decode_to_display

    images, labels = [], []
    for s in shots[:24]:
        img = cv2.imread(s["thumb"]) if s.get("thumb") else None
        if img is None:
            continue
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if s.get("profile", "rec709") != "rec709":
            rgb = (decode_to_display(rgb.astype(np.float32) / 255, s["profile"]) * 255).round().astype(np.uint8)
        images.append(rgb)
        labels.append(s["id"])
    return contact_sheet(images, labels, cols=6) if images else None


def seeded_keys(seed: int, start: int, end: int, *, amplitude: float, every: int = 1, centre: float = 0.5) -> list[dict]:
    """Deterministic shake/glitch keyframes (no randomness at runtime -> replayable)."""
    rng = np.random.default_rng(seed)
    keys = []
    for frame in range(start, max(start + 1, end), max(1, every)):
        decay = 1 - (frame - start) / max(1, end - start)
        keys.append({"frame": frame, "x": round(centre + float(rng.normal(0, amplitude * decay)), 5), "y": round(centre + float(rng.normal(0, amplitude * decay)), 5)})
    keys.append({"frame": end, "x": centre, "y": centre})
    return keys
