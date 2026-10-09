"""Natural-language control (manual 15.2): instructions -> scoped patches / Edit Spec diffs.

A deterministic grammar covers the common phrasings with exact semantics
("make the grade 15% less aggressive" -> grade intensity x0.85 on every shot,
nothing else). Anything else goes to the director under a strict schema; its
operations are validated and always shown for confirmation before running.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from vanta.core.schemas import EditDecisionGraph, Patch
from vanta.reasoning import prompts
from vanta.reasoning.llm import LLMProvider, ModelOutputInvalid, ModelUnavailable, dumps_compact


@dataclass
class NLResult:
    understood: str
    patches: list[Patch] = field(default_factory=list)
    spec_changes: dict[str, Any] = field(default_factory=dict)
    replan: dict[str, Any] | None = None  # e.g. {"range": [a, b], "pacing_delta": 0.25}
    undo: str | None = None  # dimension to undo
    confidence: float = 0.9
    needs_confirmation: bool = True
    source: str = "grammar"
    question: str | None = None


def _pct(text: str, default: float) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    return float(match.group(1)) / 100 if match else default


def _range_for(text: str, edg: EditDecisionGraph) -> str:
    total = edg.duration_frames
    if "second half" in text:
        return f"range:{total // 2}-{total}"
    if "first half" in text:
        return f"range:0-{total // 2}"
    if "intro" in text or "beginning" in text or "opening" in text:
        return f"range:0-{int(edg.fps * 3)}"
    if "end" in text or "ending" in text or "outro" in text:
        return f"range:{max(0, total - int(edg.fps * 3))}-{total}"
    return "all"


def _section_range(text: str, edg: EditDecisionGraph) -> tuple[int, int] | None:
    sections = (edg.metadata.get("grid") or {}).get("sections") or []
    fps = edg.fps
    if any(w in text for w in ("chorus", "drop", "hook of the song", "peak")):
        drops = (edg.metadata.get("grid") or {}).get("drops") or []
        if drops:
            d = drops[0]
            nxt = next((s["end_s"] for s in sections if s["start_s"] <= d < s["end_s"]), d + 6)
            return int(d * fps), int(nxt * fps)
        loudest = max(sections, key=lambda s: s.get("energy", 0), default=None)
        if loudest:
            return int(loudest["start_s"] * fps), int(loudest["end_s"] * fps)
    if "build" in text:
        build = next((s for s in sections if s.get("label") == "build"), None)
        if build:
            return int(build["start_s"] * fps), int(build["end_s"] * fps)
    if "intro" in text:
        return 0, int(3 * fps)
    return None


def _patch(edg: EditDecisionGraph, dimension: str, operation: str, target: str, changes: dict, reason: str, confidence: float = 0.9) -> Patch:
    return Patch(target_edg_id=edg.id, base_revision=edg.revision, dimension=dimension, operation=operation, target_id=target, changes=changes, reason=reason, confidence=confidence, requires_approval=True, source="chat")


def parse_instruction(text: str, edg: EditDecisionGraph, *, references: list[str] | None = None) -> NLResult | None:
    """Deterministic interpretation; returns None when the grammar does not cover the text."""
    t = text.casefold().strip()
    rng = _range_for(t, edg)
    # ---------------------------------------------------------- grade
    if re.search(r"\b(grade|look|colou?r|grading)\b", t) and re.search(r"less|more|softer|stronger|aggressive|intense|subtle", t):
        amount = _pct(t, 0.15)
        less = bool(re.search(r"less|softer|subtle|tone it down|weaker", t))
        factor = 1 - amount if less else 1 + amount
        return NLResult(f"Scale the creative look to {factor * 100:.0f}% on {('all shots' if rng == 'all' else rng)}; cut, text and music untouched.", [_patch(edg, "grade", "adjust", rng, {"intensity": factor}, text)])
    sat = re.search(r"(reduce|lower|less|decrease|desaturate|more|increase|boost|richer|muted)\b.*\bsaturat|saturat\w*\b.*\b(down|up)", t)
    if sat or "desaturate" in t:
        amount = _pct(t, 0.15)
        down = bool(re.search(r"reduce|lower|less|decrease|desaturate|muted|down", t))
        return NLResult(f"Saturation {'-' if down else '+'}{amount * 100:.0f}% (grade only).", [_patch(edg, "grade", "adjust", rng, {"saturation": -amount if down else amount}, text)])
    if "contrast" in t or "punchier" in t or "flatter" in t:
        down = bool(re.search(r"less|lower|reduce|flatter|softer", t))
        return NLResult(f"{'Lower' if down else 'Raise'} contrast (grade only).", [_patch(edg, "grade", "adjust", rng, {"contrast": -0.08 if down else 0.08}, text)])
    if re.search(r"\bwarmer\b|\bcooler\b|\bcolder\b", t):
        warm = "warmer" in t
        return NLResult(f"Shift the look {'warmer' if warm else 'cooler'} (grade only).", [_patch(edg, "grade", "adjust", rng, {"warmth": 1.0 if warm else -1.0}, text)])
    if re.search(r"\bbrighter\b|\bdarker\b|\bexposure\b", t):
        bright = "brighter" in t or "up" in t
        return NLResult(f"Exposure {'+' if bright else '-'}0.25 stop.", [_patch(edg, "grade", "adjust", rng, {"exposure": 0.25 if bright else -0.25}, text)])
    if "black" in t and re.search(r"crush|deeper|deep|richer", t):
        return NLResult("Deepen the blacks.", [_patch(edg, "grade", "adjust", rng, {"black_point": -0.02}, text)])
    if "black" in t and re.search(r"lift|matte|fade|milky", t):
        return NLResult("Lift the blacks (matte).", [_patch(edg, "grade", "adjust", rng, {"black_point": 0.03}, text)])
    if "cinematic" in t and "more" in t:
        return NLResult("More cinematic: +contrast, -saturation, deeper blacks (grade only).", [_patch(edg, "grade", "adjust", rng, {"contrast": 0.06, "saturation": -0.1, "black_point": -0.01}, text, 0.75)])
    # ---------------------------------------------------------- text
    if re.search(r"\b(text|title|caption|captions|words|overlays?)\b", t):
        if re.search(r"\bremove|delete|no\b|get rid", t):
            if "caption" in t:
                return NLResult("Remove captions.", spec_changes={"captions": False})
            return NLResult(f"Remove text layers ({rng}).", [_patch(edg, "text", "remove", rng, {}, text)])
        if re.search(r"\bup\b|higher|raise", t):
            return NLResult("Move text up.", [_patch(edg, "text", "adjust", rng, {"dy": -0.08}, text)])
        if re.search(r"\bdown\b|lower", t):
            return NLResult("Move text down.", [_patch(edg, "text", "adjust", rng, {"dy": 0.08}, text)])
        if re.search(r"bigger|larger", t):
            return NLResult("Enlarge text 20%.", [_patch(edg, "text", "adjust", rng, {"size_scale": 1.2}, text)])
        if "smaller" in t:
            return NLResult("Shrink text 15%.", [_patch(edg, "text", "adjust", rng, {"size_scale": 0.85}, text)])
        if re.search(r"readable|legible|outline", t):
            return NLResult("Add outline and backing for legibility.", [_patch(edg, "text", "adjust", rng, {"outline": True, "backing": True}, text)])
        match = re.search(r"(?:say|read|change (?:the )?(?:title|text) to)\s+[\"']?(.+?)[\"']?$", text, re.I)
        if match:
            target = edg.texts[0].id if edg.texts else "all"
            return NLResult(f"Change the title text to “{match.group(1)}”.", [_patch(edg, "text", "adjust", target, {"text": match.group(1)}, text)])
    # ---------------------------------------------------------- effects
    for word, template in (("glow", "subject_glow"), ("bloom", "cinematic_bloom"), ("shake", "camera_shake"), ("glitch", "glitch"), ("flash", "flash_transition"), ("zoom", "zoom_punch"), ("whip", "whip_pan")):
        if word in t and re.search(r"remove|no |less|without|drop|delete", t):
            return NLResult(f"Remove {word} effects; add '{word}' to never.", [_patch(edg, "effect", "remove", "none", {"template": template}, text)], spec_changes={"never_add": [f"no {word}"]})
    if "transition" in t and re.search(r"remove|fewer|less|no ", t):
        return NLResult("Remove stylised transitions (hard cuts instead).", [_patch(edg, "effect", "remove", "none", {"template": tr.kind}, text) for tr in edg.transitions] or [], spec_changes={"sliders": {"motion_effects": 0.1}})
    if re.search(r"no 3d|without 3d|remove 3d", t):
        return NLResult("No 3D unless the composition supports it.", spec_changes={"never_add": ["no 3d"], "sliders": {"three_d": 0.0}})
    # ---------------------------------------------------------- pacing / cut
    if re.search(r"cut faster|faster cuts|more cuts|speed up|quicker", t):
        section = _section_range(t, edg)
        if section:
            return NLResult(f"Re-cut frames {section[0]}-{section[1]} with faster pacing; the rest of the edit is unchanged.", replan={"range": list(section), "pacing_delta": 0.25})
        return NLResult("Faster pacing across the edit (re-cut).", spec_changes={"sliders": {"pacing": "+0.2"}})
    if re.search(r"cut slower|slower cuts|fewer cuts|let (?:it|shots) breathe|slow down", t):
        section = _section_range(t, edg)
        if section:
            return NLResult(f"Re-cut frames {section[0]}-{section[1]} slower.", replan={"range": list(section), "pacing_delta": -0.25})
        return NLResult("Slower pacing across the edit (re-cut).", spec_changes={"sliders": {"pacing": "-0.2"}})
    match = re.search(r"make it (\d+(?:\.\d+)?)\s*(?:s|sec|seconds)", t)
    if match:
        return NLResult(f"Change duration to {match.group(1)} s (re-plan from cached analysis).", spec_changes={"duration_s": float(match.group(1))})
    match = re.search(r"(?:replace|swap|change) shot (\d+)", t)
    if match:
        idx = int(match.group(1)) - 1
        ordered = edg.ordered_shots(1)
        if 0 <= idx < len(ordered):
            return NLResult(f"Replace shot {idx + 1} with the next-best candidate for its slot.", [_patch(edg, "cut", "replace", ordered[idx].id, {"footage_shot_id": "__next_best__"}, text)])
    # ---------------------------------------------------------- music
    if re.search(r"\bmusic\b|\bsong\b|\btrack\b", t):
        if re.search(r"louder|up|more", t):
            return NLResult("Music +3 dB.", [_patch(edg, "music", "adjust", "music", {"gain_db": 3.0}, text)])
        if re.search(r"quieter|lower|down|less", t):
            return NLResult("Music -3 dB.", [_patch(edg, "music", "adjust", "music", {"gain_db": -3.0}, text)])
        if re.search(r"different|another|change|replace", t):
            return NLResult("Pick the next music candidate (re-times the cut to its beat grid).", spec_changes={"music_next": True})
    # ---------------------------------------------------------- references
    refs = references or []
    match = re.findall(r"(colou?r|visual language|grade|look|typography|text|rhythm|pacing|fusion|effects|composition|motion|audio)[^0-9]*(?:reference|reel|ref)\s*#?(\d+)", t)
    if not match:
        match = [(dim, num) for num, dim in re.findall(r"(?:reference|reel|ref)\s*#?(\d+)[^a-z]*(?:'s)?\s*(colou?r|visual language|look|typography|text|rhythm|pacing|effects)", t)]
    if match and refs:
        weights: dict[str, dict[str, float]] = {}
        dim_map = {"color": "color", "colour": "color", "visual language": "color", "grade": "color", "look": "color", "typography": "typography", "text": "typography", "rhythm": "rhythm", "pacing": "rhythm", "fusion": "fusion", "effects": "fusion", "composition": "composition", "motion": "motion", "audio": "audio"}
        for dim, num in match:
            i = int(num) - 1
            if 0 <= i < len(refs):
                weights.setdefault(refs[i], {})[dim_map[dim]] = 1.0
        if weights:
            return NLResult("Borrow dimensions from specific references: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in weights.items()), spec_changes={"reference_weights": weights})
    if re.search(r"\bundo\b", t):
        for dim in ("mask", "grade", "text", "cut", "effect", "music"):
            if dim in t:
                return NLResult(f"Undo the last {dim} change.", undo=dim)
        return NLResult("Undo the last change.", undo="last")
    match = re.search(r"never (.+)", t)
    if match:
        return NLResult(f"Add a prohibition: never {match.group(1)}.", spec_changes={"never_add": [match.group(1)]})
    return None


def interpret(text: str, edg: EditDecisionGraph, provider: LLMProvider | None, *, references: list[str] | None = None) -> NLResult:
    result = parse_instruction(text, edg, references=references)
    if result:
        return result
    if provider is None:
        return NLResult("I could not map that to a scoped change.", confidence=0.0, question="Try phrasing it as a change to grade, text, pacing, music or effects.")
    summary = {
        "duration_s": edg.duration_frames / edg.fps,
        "shots": len(edg.shots),
        "texts": [t.text for t in edg.texts],
        "effects": [e.template for e in edg.effects] + [t.kind for t in edg.transitions],
        "music": bool(edg.music),
    }
    try:
        res = provider.generate_json(system=prompts.SYSTEM_EDITOR, prompt=prompts.NL_PROMPT.format(summary=dumps_compact(summary), instruction=text), schema=prompts.NLPatchPlan, prompt_version=prompts.NL_PROMPT_VERSION)
    except (ModelUnavailable, ModelOutputInvalid) as exc:
        return NLResult("The director is unavailable to interpret that instruction.", confidence=0.0, question=str(exc))
    data = res.data
    out = NLResult(data["understood"], confidence=float(data["confidence"]), source=res.model, question=data.get("question") or None)
    for op in data.get("operations", []):
        dim = op["dimension"]
        if dim == "grade" and op.get("field") in {"contrast", "saturation", "exposure", "warmth", "intensity", "black_point"}:
            out.patches.append(_patch(edg, "grade", "adjust", op.get("target") or "all", {op["field"]: op.get("delta") or float(op.get("value") or 0)}, op["reason"], data["confidence"]))
        elif dim == "text" and op["op"] == "remove":
            out.patches.append(_patch(edg, "text", "remove", op.get("target") or "all", {}, op["reason"], data["confidence"]))
        elif dim == "effect" and op["op"] == "remove":
            out.patches.append(_patch(edg, "effect", "remove", "none", {"template": op.get("value") or op.get("target")}, op["reason"], data["confidence"]))
        elif dim == "music" and op.get("field") == "gain_db":
            out.patches.append(_patch(edg, "music", "adjust", "music", {"gain_db": op.get("delta") or 0.0}, op["reason"], data["confidence"]))
        elif dim == "spec" and op.get("field"):
            out.spec_changes[op["field"]] = op.get("value")
    if not out.patches and not out.spec_changes:
        out.question = out.question or "I understood the intent but could not express it as a safe scoped change."
        out.confidence = min(out.confidence, 0.4)
    return out
