"""Versioned prompts and output schemas for every director (VLM) call.

The VLM interprets and chooses; it never computes numbers the solvers own.
Each schema uses fixed vocabularies so outputs stay comparable and testable.
Prompt versions are recorded in run manifests for reproducibility.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EFFECT_VOCABULARY = (
    "hard_cut",
    "dissolve",
    "whip",
    "zoom_transition",
    "flash",
    "glitch",
    "speed_ramp",
    "freeze_frame",
    "motion_blur",
    "mask_reveal",
    "subject_cutout",
    "text_behind_subject",
    "tracked_text",
    "screen_replacement",
    "text_3d",
    "camera_move_3d",
    "glow",
    "bloom",
    "chromatic_aberration",
    "grain",
    "shake",
    "parallax",
    "particles",
    "vignette",
    "split_screen",
    "picture_in_picture",
    "kinetic_typography",
    "captions",
    "letterbox",
    "none",
)
Effect = Literal[EFFECT_VOCABULARY]  # type: ignore[valid-type]
Dimension = Literal["rhythm", "color", "typography", "composition", "motion", "masking", "fusion", "audio"]

SYSTEM_EDITOR = (
    "You are VANTA's creative director: a senior short-form video editor and colourist. "
    "You reason about purpose, conditions and technique, never about vague taste. "
    "Separate what is OBSERVED in the frames from what you INFER about how it was made. "
    "Use only the provided vocabularies. Measured numbers supplied to you are ground truth; "
    "do not contradict or re-estimate them. Return only JSON that matches the schema."
)

# --------------------------------------------------------------------- reference
REFERENCE_PROMPT_VERSION = "reference-v3"


class TechniqueObservation(BaseModel):
    technique: Effect
    dimension: Dimension
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    observed: str = Field(description="what is visible, without speculation", max_length=300)
    inferred_method: str = Field(description="likely implementation", max_length=200)
    alternative_method: str = Field(default="", max_length=200)
    method_known: bool = Field(description="false when the implementation cannot be determined")
    purpose: str = Field(description="the visual problem this solves", max_length=200)
    conditions: list[str] = Field(default_factory=list, description="when this works (subject, camera, space)")
    confidence: float = Field(ge=0, le=1)


class ShotReading(BaseModel):
    shot: int
    subject: str = Field(max_length=120)
    action: str = Field(max_length=120)
    setting: str = Field(max_length=120)
    lighting: Literal["daylight", "golden_hour", "night", "indoor_practical", "studio", "mixed", "overcast", "neon"]
    story_role: Literal["hook", "setup", "build", "payoff", "breather", "outro", "b_roll"]
    mood: str = Field(max_length=60)


class ReferenceReading(BaseModel):
    summary: str = Field(max_length=500)
    genre: str = Field(max_length=80)
    profile_hint: str = Field(description="short kebab-case style profile, e.g. night-moto-cinematic", max_length=48)
    hook: str = Field(max_length=200)
    payoff: str = Field(max_length=200)
    ending: str = Field(max_length=200)
    color_intent: str = Field(description="grading intent in relational terms, not pixel values", max_length=300)
    typography_intent: str = Field(max_length=200)
    rhythm_intent: str = Field(max_length=200)
    shots: list[ShotReading]
    techniques: list[TechniqueObservation]


REFERENCE_PROMPT = """Analyze this reference reel as a professional editor. Do not describe it generally.
The contact sheet shows one key frame per shot, labelled with the shot index and start time.
Measured facts (ground truth, from deterministic analysis):
{facts}

For each technique you can see give timestamps, observable evidence, observed vs inferred
implementation, purpose (what visual problem it solves), the conditions under which it would
work, and a confidence. If the method cannot be known, set method_known=false.
Do not assume the treatment fits unrelated footage. Describe colour as relationships
(e.g. warm subject against cool environment), never absolute hues copied from the reel.
Return JSON matching the schema and nothing else."""

# ----------------------------------------------------------------------- footage
FOOTAGE_PROMPT_VERSION = "footage-v2"


class FootageShotReading(BaseModel):
    shot: int
    description: str = Field(max_length=200)
    subject: str = Field(max_length=80)
    subject_box: list[float] = Field(
        default_factory=list, description="[x0,y0,x1,y1] normalised 0..1 box of the main subject, or empty"
    )
    action: str = Field(max_length=80)
    setting: str = Field(max_length=80)
    lighting: Literal["daylight", "golden_hour", "night", "indoor_practical", "studio", "mixed", "overcast", "neon"]
    tags: list[str] = Field(default_factory=list, max_length=12)
    hook_potential: float = Field(ge=0, le=1)
    emotional_tone: str = Field(max_length=40)
    usable: bool
    issues: list[str] = Field(default_factory=list, max_length=6)


class FootageReading(BaseModel):
    shots: list[FootageShotReading]


FOOTAGE_PROMPT = """You are logging raw footage for an editor. The contact sheet shows key frames
labelled shot index and start time. Measured facts per shot:
{facts}

For every shot return a concise description, the main subject and its bounding box (normalised),
the action, setting, lighting class, up to 12 search tags, hook potential (could this open a reel?),
emotional tone, whether it is usable and any issues (blur, bad exposure, obstruction).
Return JSON matching the schema and nothing else."""

# ----------------------------------------------------------------------- planner
PLAN_PROMPT_VERSION = "plan-v4"


class SlotIntent(BaseModel):
    slot: int
    section: Literal["hook", "setup", "build", "drop", "payoff", "breather", "outro"]
    intent: str = Field(max_length=160)
    preferred_tags: list[str] = Field(default_factory=list, max_length=8)
    preferred_shots: list[str] = Field(default_factory=list, max_length=6)
    avoid_shots: list[str] = Field(default_factory=list, max_length=6)
    energy: float = Field(ge=0, le=1)


class TextIntent(BaseModel):
    text: str = Field(max_length=60)
    role: Literal["title", "hook_line", "caption", "callout", "end_card"]
    slot: int
    template: str = Field(description="a text feature key from the feature manual")
    reason: str = Field(max_length=160)


class EffectIntent(BaseModel):
    slot: int
    template: str = Field(description="an effect feature key from the feature manual")
    purpose: str = Field(max_length=160, description="what visual problem this solves")
    confidence: float = Field(ge=0, le=1)


class AssemblyPlan(BaseModel):
    story: str = Field(max_length=400)
    hook_strategy: str = Field(max_length=200)
    slots: list[SlotIntent]
    texts: list[TextIntent] = Field(default_factory=list)
    effects: list[EffectIntent] = Field(default_factory=list)
    color_intent: str = Field(max_length=300)
    rationale: list[str] = Field(default_factory=list, max_length=12)


PLAN_PROMPT = """Plan a {duration:.0f}-second {platform} edit.
Brief: {goal}
Locks: {locks}
Never: {never}
Style package (approved rules and measured reference statistics):
{style}
Music: {music}
Slots to fill (timing already solved from the beat grid; you choose intent, not timing):
{slots}
Footage shots available (id: description [tags] quality hook):
{footage}
Feature manual: the ONLY tools you may use, with when to use and avoid each:
{features}

Reason: OBSERVE the footage, CLASSIFY it against the style rules, COMPARE options, CHOOSE.
Every text and effect needs a purpose that matches its USE condition in THIS footage and none of its
AVOID conditions; 3D or glow without a spatial/visual reason is not allowed. Restraint is quality:
a hard cut is the default, never repeat the same effect on consecutive slots, and leave the grade,
reframing, speed ramps, transitions and music to the solvers (they are applied automatically).
Return JSON matching the schema and nothing else."""

# ------------------------------------------------------------------------- grade
GRADE_PROMPT_VERSION = "grade-intent-v2"


class ShotGradeIntent(BaseModel):
    shot_id: str
    intent: str = Field(max_length=240)
    warmth: Literal["cooler", "neutral", "warmer"]
    contrast: Literal["softer", "keep", "punchier"]
    saturation: Literal["muted", "keep", "richer"]
    separation: Literal["none", "subtle", "strong"]
    protect_skin: bool
    deepen_blacks: bool


class GradeIntentPlan(BaseModel):
    global_intent: str = Field(max_length=300)
    avoid: list[str] = Field(default_factory=list, max_length=8)
    shots: list[ShotGradeIntent]


GRADE_PROMPT = """You receive reference look observations, target shot measurements and the user's
preferences. Return a grading INTENT per shot (relationships, not numbers): what to retain, what to
separate, what to protect. A solver will compute parameters.
Reference look intent: {reference}
User preferences / rules: {rules}
Colour intensity slider: {intensity:.2f}; skin priority: {skin:.2f}
Target shots:
{shots}
Return JSON matching the schema and nothing else."""

# ------------------------------------------------------------------------ critic
CRITIC_PROMPT_VERSION = "critic-v2"


class CriticIssue(BaseModel):
    type: Literal["editing", "color", "text", "audio", "effect", "story", "technical"]
    severity: float = Field(ge=0, le=1)
    at_s: float = Field(ge=0)
    description: str = Field(max_length=200)
    repair: Literal[
        "REPLACE_SHOT",
        "RETIME_CUT",
        "MOVE_TEXT",
        "REMOVE_TEXT",
        "ADJUST_GRADE",
        "REMOVE_EFFECT",
        "ADJUST_MUSIC",
        "NONE",
    ]
    confidence: float = Field(ge=0, le=1)


class CriticReport(BaseModel):
    status: Literal["pass", "needs_revision"]
    hook_works: bool
    issues: list[CriticIssue] = Field(default_factory=list)
    notes: str = Field(default="", max_length=400)


CRITIC_PROMPT = """You are the finishing critic (QC level L5, editorial rubric). The contact sheet shows
frames sampled from the rendered proxy at the labelled times. Deterministic QC already found:
{qc}
Edit summary: {summary}
Answer: does the first second hook? Are cuts too frequent or awkward? Is skin damaged? Are transitions
overused? Is any 3D/glow unjustified? Does the music peak match the visual peak? Is text readable?
Be specific and structured; never just say it looks good. Return JSON matching the schema."""

# ---------------------------------------------------------------- natural language
NL_PROMPT_VERSION = "nl-patch-v2"


class NLOperation(BaseModel):
    op: Literal["replace", "modify", "remove", "add", "set_spec"]
    dimension: Literal["cut", "grade", "text", "mask", "music", "effect", "pacing", "spec", "reference_weight"]
    target: str = Field(description="shot id, 'all', text id, slot range like 'chorus', or spec field")
    field: str = Field(default="")
    value: str = Field(default="")
    delta: float = Field(default=0.0)
    reason: str = Field(max_length=160)


class NLPatchPlan(BaseModel):
    understood: str = Field(max_length=200)
    operations: list[NLOperation]
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool = False
    question: str = Field(default="", max_length=200)


NL_PROMPT = """Convert the user's instruction into scoped edit operations. Change only what the
instruction refers to; never rebuild the whole edit. Current edit summary:
{summary}
Instruction: "{instruction}"
Return JSON matching the schema and nothing else."""
