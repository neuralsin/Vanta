"""The feature catalogue: everything VANTA can execute, when to use it, and what it needs.

This is the director's manual. The planning prompt is generated from it, and
the planner validates every proposal against it (a feature the director names
but cannot be executed on this footage is dropped with a reason). Keep entries
in sync with the Fusion registry and the executor's handlers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vanta.compile.fusion import load_registry


@dataclass(frozen=True)
class Feature:
    key: str
    kind: str  # text | transition | effect | motion | grade | audio | structure
    purpose: str
    use_when: str
    avoid_when: str
    needs: tuple[str, ...] = ()  # matte | spatial_reason | speech | still | landscape_timeline | sfx_library
    execution: str = ""
    params: dict[str, str] = field(default_factory=dict)


FEATURES: tuple[Feature, ...] = (
    # ---------------------------------------------------------------- text
    Feature("text_basic", "text", "clean title or line of copy", "any title where the image should stay the hero", "the frame is busy behind the text", execution="Fusion Text+ comp on the clip (overlay fallback)"),
    Feature("tracking_reveal", "text", "cinematic title reveal (letters converge)", "openers and end cards with a calm shot", "fast-cut sections"),
    Feature("scale_pop", "text", "energetic hook line", "the first second, beat-synced emphasis", "calm or emotional moments"),
    Feature("slide", "text", "directional title entry", "pans or moving shots matching the slide direction", "static symmetrical frames"),
    Feature("typewriter", "text", "reveal a short phrase letter by letter", "informational lines, tension", "long text"),
    Feature("kinetic_words", "text", "one word at a time on rhythm", "high energy + high cut rate, voice-over emphasis", "slow cinematic pieces"),
    Feature("masked_reveal", "text", "text wiped on through scene geometry", "clean graphic backgrounds", "busy frames"),
    Feature("text_2_5d", "text", "title that drifts with soft shadow (depth without 3D)", "wide shots with negative space; safe fallback for 3D", "close-ups"),
    Feature("text_behind_subject", "text", "title sits behind the subject (spatial integration)", "a clear person/vehicle subject, stable camera, negative space above or behind", "no clear subject, heavy motion blur, hair against busy sky", needs=("matte",), execution="SAM 2.1 matte + Fusion merge"),
    Feature("text3d_hover", "text", "extruded 3D title in scene space", "strong perspective, stable camera, large negative space", "handheld, close-ups, no spatial reason", needs=("spatial_reason",)),
    Feature("word_highlight", "text", "word-level karaoke captions", "speech that matters to the story", "music-only edits", needs=("speech",), execution="VANTA overlay track + SRT"),
    # ----------------------------------------------------------- transitions
    Feature("hard_cut", "transition", "default; maintains momentum", "almost always", "never forbidden"),
    Feature("cross_dissolve", "transition", "soften a time/mood shift", "breakdowns, outros, time passing", "high-energy sections", execution="Resolve AddTransition (G12) or hard cut"),
    Feature("dip_to_black", "transition", "close a section or the story", "the final cut, section breaks", "mid-sequence energy"),
    Feature("zoom_punch", "transition", "impact on a strong beat/drop", "drops; both shots have motion or strong centre subject", "static shots both sides"),
    Feature("whip_pan", "transition", "carry a pan through the cut", "two pans in the same direction", "static shots"),
    Feature("flash_transition", "transition", "punctuate a beat or drop", "drops, energy accents", "dialogue, calm moods"),
    Feature("glitch", "transition", "digital energy accent", "tech/urban styles that use it", "cinematic/nature styles"),
    # --------------------------------------------------------------- effects
    Feature("cinematic_bloom", "effect", "filmic halation on practical lights", "night shots with street lights, neon, headlights", "flat daylight"),
    Feature("subject_glow", "effect", "make the hero subject radiate", "night or dark backgrounds with an isolated subject", "bright busy frames", needs=("matte",)),
    Feature("subject_outline", "effect", "cut-out graphic look around the subject", "stylised energetic edits", "cinematic/realistic styles", needs=("matte",)),
    Feature("speed_blur", "effect", "smooth fast motion", "very high-motion moments", "static shots"),
    Feature("camera_shake", "effect", "impact on a hit/drop", "the drop shot in high-energy edits", "dialogue, calm sections"),
    Feature("freeze_frame", "effect", "hold attention on a moment", "a peak action moment, a reveal", "repeated use"),
    Feature("parallax", "effect", "slow push/drift for life in static shots", "locked-off wide shots, stills", "already moving shots"),
    Feature("letterbox", "effect", "cinematic framing", "landscape timelines with a cinematic style", "vertical reels (cuts the frame)", needs=("landscape_timeline",)),
    # ---------------------------------------------------------------- motion
    Feature("speed_ramp", "motion", "beat impact / emphasise motion", "drop slots with high-motion footage", "dialogue", execution="optical-flow pre-render (or Resolve speed when G13 passes)"),
    Feature("reframe", "motion", "keep the subject in frame when aspect differs", "always applied automatically for landscape->vertical", "never forbidden", execution="subject-aware pan on a fill timeline"),
    # ----------------------------------------------------------------- grade
    Feature("grade", "grade", "the look: relational transfer from the references", "every shot", "never skipped", execution="camera log decode + TECH CDL + LOOK LUT, export-verified"),
    # ----------------------------------------------------------------- audio
    Feature("music_bed", "audio", "drives pacing and payoff", "every edit with licensed music", "unlicensed tracks (blocked at final render)"),
    Feature("sfx_whoosh", "audio", "sell stylised transitions", "whip/zoom/flash transitions", "dialogue scenes", needs=("sfx_library",)),
    Feature("sfx_hit", "audio", "impact on the drop", "the drop cut", "calm edits", needs=("sfx_library",)),
)

BY_KEY = {f.key: f for f in FEATURES}
UNEXECUTABLE = {"picture_in_picture", "screen_replacement", "tracked_callout"}  # need user inputs/tracking VANTA does not have


def available(context: dict) -> list[Feature]:
    """Features whose requirements this edit can satisfy (registry + footage + settings)."""
    registry = set(load_registry())
    out = []
    for feature in FEATURES:
        if feature.kind in {"text", "effect", "transition"} and feature.key not in {"hard_cut", "cross_dissolve", "dip_to_black"} and feature.key not in registry:
            continue
        if "speech" in feature.needs and not context.get("speech"):
            continue
        if "landscape_timeline" in feature.needs and not context.get("landscape"):
            continue
        if "sfx_library" in feature.needs and not context.get("sfx_library"):
            continue
        if "matte" in feature.needs and context.get("masking", 0) < 0.2:
            continue
        if feature.key == "text3d_hover" and context.get("three_d", 0) < 0.3:
            continue
        out.append(feature)
    return out


def prompt_catalogue(features: list[Feature]) -> str:
    """Compact manual for the director prompt."""
    lines = []
    for f in features:
        needs = f" needs:{'+'.join(f.needs)}" if f.needs else ""
        lines.append(f"- {f.key} [{f.kind}] {f.purpose}. USE: {f.use_when}. AVOID: {f.avoid_when}.{needs}")
    return "\n".join(lines)
