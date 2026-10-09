"""Versioned data contracts shared by the planner, solvers, UI and Resolve adapter.

The Edit Decision Graph (EDG) is the source of truth for an edit (manual 10).
Every element carries confidence, rationale and provenance; Resolve is only the
execution target. Schema version 2 adds grades, transitions, text/effect
layers, mattes, captions, markers, speed/reframe and explicit frame-rate
conform decisions while remaining backward compatible with version 1 graphs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

EDG_SCHEMA_VERSION = "2.0.0"


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AutonomyMode(StrEnum):
    AUTO = "AUTO"
    ASSISTED = "ASSISTED"
    MANUAL = "MANUAL"


class RunMode(StrEnum):
    WATCH = "WATCH"
    STEP = "STEP"
    TURBO = "TURBO"


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYABLE = "RETRYABLE"
    CANCELLED = "CANCELLED"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYABLE = "RETRYABLE"
    CANCELLED = "CANCELLED"


class EventStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    VERIFIED = "verified"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    SKIPPED = "skipped"
    INFO = "info"
    WARNING = "warning"


DIMENSIONS = ("rhythm", "color", "typography", "composition", "motion", "masking", "fusion", "audio")


# ============================================================== Edit Spec
class SliderSet(StrictModel):
    pacing: float = Field(default=0.5, ge=0, le=1)
    beat_sync: float = Field(default=0.5, ge=0, le=1)
    color_intensity: float = Field(default=0.5, ge=0, le=1)
    skin_priority: float = Field(default=0.8, ge=0, le=1)
    masking: float = Field(default=0, ge=0, le=1)
    three_d: float = Field(default=0, ge=0, le=1)
    typography_density: float = Field(default=0.35, ge=0, le=1)
    music_priority: float = Field(default=0.5, ge=0, le=1)
    story_clarity: float = Field(default=0.6, ge=0, le=1)
    shot_variety: float = Field(default=0.6, ge=0, le=1)
    contrast: float = Field(default=0.5, ge=0, le=1)
    saturation: float = Field(default=0.5, ge=0, le=1)
    motion_effects: float = Field(default=0.3, ge=0, le=1)
    glow: float = Field(default=0.0, ge=0, le=1)
    speed_ramps: float = Field(default=0.2, ge=0, le=1)
    caption_density: float = Field(default=0.5, ge=0, le=1)
    dialogue_priority: float = Field(default=0.7, ge=0, le=1)
    sfx_priority: float = Field(default=0.3, ge=0, le=1)


class EditSpec(StrictModel):
    schema_version: str = "1.1.0"
    goal: str = Field(min_length=1, max_length=2000)
    platform: str = "instagram_reel"
    duration_s: float = Field(gt=0, le=3600)
    aspect: str = Field(pattern=r"^\d+:\d+$")
    fps: float = Field(gt=0, le=240)
    resolution: tuple[int, int] = (1080, 1920)
    autonomy: AutonomyMode = AutonomyMode.ASSISTED
    run_mode: RunMode = RunMode.WATCH
    sliders: SliderSet = Field(default_factory=SliderSet)
    reference_weights: dict[str, dict[str, float]] = Field(default_factory=dict)
    locks: dict[str, list[str]] = Field(default_factory=dict)
    never: list[str] = Field(default_factory=list)
    profile_id: str | None = None
    aspect_mode: Literal["locked", "guided", "auto", "off"] = "locked"
    captions: bool = True
    allowed_effects: list[str] | None = None  # None = registry default; [] = none
    disabled_references: list[str] = Field(default_factory=list)
    music_id: str | None = None
    footage_ids: list[str] = Field(default_factory=list)
    title_text: str | None = None
    loudness_target_lufs: float = -14.0

    @model_validator(mode="after")
    def validate_reference_weights(self) -> EditSpec:
        for reference, dimensions in self.reference_weights.items():
            if not reference.strip():
                raise ValueError("reference IDs cannot be empty")
            if any(not 0 <= value <= 1 for value in dimensions.values()):
                raise ValueError("reference weights must be between 0 and 1")
            unknown = set(dimensions) - set(DIMENSIONS)
            if unknown:
                raise ValueError(f"unknown reference weight dimension(s): {sorted(unknown)}")
        if min(self.resolution) < 1:
            raise ValueError("resolution dimensions must be positive")
        return self


# ==================================================================== EDG
class Provenance(StrictModel):
    source: Literal["planner", "solver", "user", "critic", "repair", "rule", "default", "patch"] = "solver"
    rules: list[str] = Field(default_factory=list)
    exemplars: list[str] = Field(default_factory=list)
    model: str | None = None
    prompt_version: str | None = None
    solver: str | None = None
    note: str | None = None


class SourceMedia(StrictModel):
    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str | None = None
    media_type: Literal["video", "audio", "audiovisual", "still"] = "video"
    duration_frames: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)
    read_only: bool = True
    width: int | None = None
    height: int | None = None
    library_id: str | None = None
    color_space: str | None = None  # e.g. rec709, hlg, slog3


class Reframe(StrictModel):
    """Static reframe on a fill-to-frame (scale-to-crop) timeline.

    ``pan``/``tilt`` move the image by a fraction of the timeline width/height
    (positive pan = image moves right, positive tilt = image moves up); ``zoom``
    is extra scale on top of the fill. ``keys`` (normalised clip time, pan,
    tilt) make it a subject-follow: VANTA bakes that move in its 16-bit
    pre-render at timeline resolution (the clip then needs no Resolve sizing).
    """

    zoom: float = Field(default=1.0, ge=0.5, le=4.0)
    pan: float = Field(default=0.0, ge=-2.0, le=2.0)
    tilt: float = Field(default=0.0, ge=-2.0, le=2.0)
    rotation_deg: float = Field(default=0.0, ge=-45, le=45)
    dynamic_track: str | None = None  # subject track id for keyframed follow
    keys: list[tuple[float, float, float]] = Field(default_factory=list)  # (u 0..1, pan, tilt) subject-follow path

    @property
    def dynamic(self) -> bool:
        return len(self.keys) >= 2


class SpeedRamp(StrictModel):
    curve: list[tuple[float, float]] = Field(min_length=2, description="(normalised time, speed %) points")
    purpose: Literal["emphasis", "transition", "energy", "motion_exaggeration", "beat_impact"]
    route: Literal["resolve_api", "prerender", "fcpxml"] = "prerender"


class Shot(StrictModel):
    id: str = Field(default_factory=lambda: f"shot_{uuid4().hex[:12]}")
    source_media_id: str
    source_in_frame: int = Field(ge=0)
    source_out_frame: int = Field(gt=0)
    timeline_start_frame: int = Field(ge=0)
    track_index: int = Field(default=1, ge=1)
    confidence: float = Field(default=1, ge=0, le=1)
    rationale: list[str] = Field(default_factory=list)
    # v2
    timeline_duration_frames: int | None = Field(
        default=None, gt=0, description="explicit conform decision when source and timeline rates differ"
    )
    conform: Literal["exact", "resolve_realtime"] = "exact"
    footage_shot_id: str | None = None
    slot: int | None = None
    role: str | None = None
    speed_percent: float = Field(default=100.0, gt=0, le=1000)
    speed_ramp: SpeedRamp | None = None
    reframe: Reframe | None = None
    audio_enabled: bool = False
    audio_gain_db: float = Field(default=0.0, ge=-60, le=12)
    fade_in_frames: int = Field(default=0, ge=0)
    fade_out_frames: int = Field(default=0, ge=0)
    beat_aligned: bool = False
    locked: bool = False
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def valid_ranges(self) -> Shot:
        if self.source_out_frame <= self.source_in_frame:
            raise ValueError("source_out_frame must be after source_in_frame")
        return self

    @property
    def duration_frames(self) -> int:
        return self.source_out_frame - self.source_in_frame


class MusicBed(StrictModel):
    source_media_id: str
    source_in_frame: int = Field(default=0, ge=0)
    source_out_frame: int | None = Field(default=None, gt=0)
    timeline_start_frame: int = Field(default=0, ge=0)
    track_index: int = Field(default=2, ge=1)
    rights_source: str | None = None
    licence_confirmed: bool = False
    # v2
    gain_db: float = Field(default=0.0, ge=-60, le=12)
    fade_in_frames: int = Field(default=0, ge=0)
    fade_out_frames: int = Field(default=0, ge=0)
    duck_under_dialogue_db: float = Field(default=0.0, le=0)
    beat_grid_s: list[float] = Field(default_factory=list)
    downbeats_s: list[float] = Field(default_factory=list)
    drop_s: float | None = None
    bpm: float | None = None
    guide_only: bool = False  # silent guide for platform-library sounds


class SfxCue(StrictModel):
    """A sound effect from the user's licensed SFX library, placed to support a cut (manual 14)."""

    id: str = Field(default_factory=lambda: f"sfx_{uuid4().hex[:10]}")
    source_media_id: str
    kind: Literal["whoosh", "hit", "riser", "impact", "swell"] = "whoosh"
    timeline_frame: int = Field(ge=0)
    source_in_frame: int = Field(default=0, ge=0)
    source_out_frame: int = Field(gt=0)
    gain_db: float = Field(default=-6.0, ge=-60, le=6)
    purpose: str = ""
    licence_confirmed: bool = False


class GradeSpec(StrictModel):
    """Grading intent plus solved numbers (manual 11). The tree always exists; nodes toggle."""

    shot_id: str
    intent: str = ""
    params: dict[str, Any] = Field(default_factory=dict)  # LookParams
    tech_cdl: dict[str, Any] | None = None
    lut_path: str | None = None  # full transform (decode -> CDL -> look): LUT-only route + verification target
    look_lut_path: str | None = None  # LOOK node of the 8-node DRX route
    decode_lut_path: str | None = None  # TECH node camera decode (log/HDR footage only)
    route: Literal["lut_only", "drx_template", "none"] = "lut_only"
    drx_template: str | None = None
    node_enabled: dict[str, bool] = Field(default_factory=dict)  # TECH..POLISH
    matte_id: str | None = None
    input_profile: str = "rec709"  # camera log/HDR decode baked ahead of the look
    target_stats: dict[str, float] = Field(default_factory=dict)
    predicted_stats: dict[str, float] = Field(default_factory=dict)
    loss: float | None = None
    confidence: float = Field(default=0.8, ge=0, le=1)
    rationale: list[str] = Field(default_factory=list)
    locked: bool = False
    provenance: Provenance = Field(default_factory=Provenance)


class Transition(StrictModel):
    id: str = Field(default_factory=lambda: f"tr_{uuid4().hex[:10]}")
    from_shot: str
    to_shot: str
    kind: Literal["cross_dissolve", "dip_to_black", "dip_to_white", "whip", "zoom_punch", "flash", "glitch", "smooth_cut"]
    duration_frames: int = Field(ge=1, le=120)
    route: Literal["resolve_api", "fusion_template", "fcpxml"] = "resolve_api"
    template: str | None = None
    purpose: str = ""
    confidence: float = Field(default=0.8, ge=0, le=1)
    provenance: Provenance = Field(default_factory=Provenance)


class TextLayer(StrictModel):
    id: str = Field(default_factory=lambda: f"txt_{uuid4().hex[:10]}")
    text: str = Field(min_length=1, max_length=200)
    role: Literal["title", "hook_line", "caption", "callout", "end_card", "lower_third"] = "title"
    template: str
    params: dict[str, Any] = Field(default_factory=dict)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    track_index: int = Field(default=2, ge=2)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.4, ge=0, le=1)
    font: str | None = None
    size: float = Field(default=0.06, gt=0, le=0.5, description="cap height as fraction of frame height")
    color: str = "#FFFFFF"
    outline: bool = False
    shadow: bool = True
    behind_subject: bool = False
    matte_id: str | None = None
    shot_id: str | None = None
    placement_score: float | None = None
    purpose: str = ""
    confidence: float = Field(default=0.8, ge=0, le=1)
    locked: bool = False
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def ordered(self) -> TextLayer:
        if self.end_frame <= self.start_frame:
            raise ValueError("text layer end_frame must be after start_frame")
        return self


class EffectLayer(StrictModel):
    id: str = Field(default_factory=lambda: f"fx_{uuid4().hex[:10]}")
    template: str
    shot_id: str | None = None
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    params: dict[str, Any] = Field(default_factory=dict)
    matte_id: str | None = None
    purpose: str = Field(min_length=1, description="what visual problem this effect solves")
    confidence: float = Field(default=0.7, ge=0, le=1)
    locked: bool = False
    provenance: Provenance = Field(default_factory=Provenance)


class MatteRef(StrictModel):
    id: str = Field(default_factory=lambda: f"matte_{uuid4().hex[:10]}")
    shot_id: str
    directory: str
    pattern: str = "matte_%05d.png"
    frame_count: int = Field(gt=0)
    method: str = "sam2"
    prompt: str | None = None
    confidence: float = Field(ge=0, le=1)
    review_required: bool = False
    approved: bool = False


class CaptionChunk(StrictModel):
    id: str = Field(default_factory=lambda: f"cap_{uuid4().hex[:10]}")
    text: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    words: list[dict[str, Any]] = Field(default_factory=list)  # {text, start_frame, end_frame}
    y: float = Field(default=0.7, ge=0, le=1)


class TimelineMarker(StrictModel):
    frame: int = Field(ge=0)
    color: Literal["Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose", "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream"] = "Blue"
    name: str
    note: str = ""
    kind: Literal["decision", "verified", "low_confidence", "failed", "override", "note", "sync_point", "section"] = "decision"


class ColorManagement(StrictModel):
    science: Literal["davinci_yrgb", "davinci_yrgb_color_managed", "aces"] = "davinci_yrgb"
    timeline_space: str = "Rec.709 Gamma 2.4"
    output_space: str = "Rec.709 Gamma 2.4"
    input_spaces: dict[str, str] = Field(default_factory=dict)  # source id -> space


class EditDecisionGraph(StrictModel):
    schema_version: str = EDG_SCHEMA_VERSION
    id: str = Field(default_factory=lambda: f"edg_{uuid4().hex}")
    project_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    fps: float = Field(gt=0, le=240)
    timeline_name: str = Field(min_length=1)
    sources: list[SourceMedia] = Field(min_length=1)
    shots: list[Shot] = Field(default_factory=list)
    music: MusicBed | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # v2
    width: int = Field(default=1080, ge=16)
    height: int = Field(default=1920, ge=16)
    grades: list[GradeSpec] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    texts: list[TextLayer] = Field(default_factory=list)
    effects: list[EffectLayer] = Field(default_factory=list)
    mattes: list[MatteRef] = Field(default_factory=list)
    captions: list[CaptionChunk] = Field(default_factory=list)
    sfx: list[SfxCue] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list)
    color_management: ColorManagement = Field(default_factory=ColorManagement)
    locks: dict[str, list[str]] = Field(default_factory=dict)
    prohibitions: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = None
    patch_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_graph(self) -> EditDecisionGraph:
        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source media IDs must be unique")
        known = set(source_ids)
        shot_ids: set[str] = set()
        for shot in self.shots:
            if shot.source_media_id not in known:
                raise ValueError(f"shot {shot.id} refers to unknown source media")
            if shot.id in shot_ids:
                raise ValueError("shot IDs must be unique")
            shot_ids.add(shot.id)
            source = next(item for item in self.sources if item.id == shot.source_media_id)
            if source.duration_frames and shot.source_out_frame > source.duration_frames:
                raise ValueError(f"shot {shot.id} exceeds source duration")
        self._validate_track_layout()
        if self.music and self.music.source_media_id not in known:
            raise ValueError("music bed refers to unknown source media")
        if self.music:
            source = next(item for item in self.sources if item.id == self.music.source_media_id)
            if source.media_type not in {"audio", "audiovisual"}:
                raise ValueError("music bed must refer to an audio or audiovisual source")
            end = self.music.source_out_frame or source.duration_frames
            if end is None:
                raise ValueError("music source needs a known duration or explicit source_out_frame")
            if end <= self.music.source_in_frame:
                raise ValueError("music source_out_frame must be after source_in_frame")
            if source.duration_frames and end > source.duration_frames:
                raise ValueError("music source_out_frame exceeds source duration")
        for cue in self.sfx:
            if cue.source_media_id not in known:
                raise ValueError(f"sfx cue {cue.id} refers to unknown source media")
        for grade in self.grades:
            if grade.shot_id not in shot_ids:
                raise ValueError(f"grade refers to unknown shot {grade.shot_id}")
        for transition in self.transitions:
            if transition.from_shot not in shot_ids or transition.to_shot not in shot_ids:
                raise ValueError(f"transition {transition.id} refers to unknown shots")
        matte_ids = {m.id for m in self.mattes}
        for matte in self.mattes:
            if matte.shot_id not in shot_ids:
                raise ValueError(f"matte {matte.id} refers to unknown shot")
        for layer in [*self.texts, *self.effects]:
            if layer.matte_id and layer.matte_id not in matte_ids:
                raise ValueError(f"layer {layer.id} refers to unknown matte {layer.matte_id}")
            if layer.shot_id and layer.shot_id not in shot_ids:
                raise ValueError(f"layer {layer.id} refers to unknown shot {layer.shot_id}")
        self._validate_overlay_tracks()
        return self

    def timeline_duration(self, shot: Shot) -> int:
        """Timeline frames a shot occupies (explicit conform decisions are honoured)."""
        from vanta.core.timing import TimingError, fps_fraction, source_frames_to_timeline_frames

        source = next(item for item in self.sources if item.id == shot.source_media_id)
        source_fps = source.fps or self.fps
        if shot.timeline_duration_frames is not None:
            exact = shot.duration_frames * fps_fraction(self.fps) / fps_fraction(source_fps)
            exact = exact * 100 / shot.speed_percent
            if abs(float(exact) - shot.timeline_duration_frames) > 1.0 + 1e-9:
                raise ValueError(
                    f"shot {shot.id}: explicit timeline duration {shot.timeline_duration_frames} "
                    f"disagrees with source span ({float(exact):.2f} frames at {shot.speed_percent}% speed)"
                )
            return shot.timeline_duration_frames
        if shot.speed_percent != 100.0:
            raise ValueError(f"shot {shot.id}: speed changes need an explicit timeline_duration_frames")
        try:
            return source_frames_to_timeline_frames(shot.duration_frames, source_fps, self.fps)
        except TimingError as exc:
            raise ValueError(f"shot {shot.id}: {exc}") from exc

    def shot_end(self, shot: Shot) -> int:
        return shot.timeline_start_frame + self.timeline_duration(shot)

    @property
    def duration_frames(self) -> int:
        return max((self.shot_end(shot) for shot in self.shots), default=0)

    def _validate_track_layout(self) -> None:
        """Reject ambiguous same-track overlap; transitions sit on butt-joined cuts."""
        ends_by_track: dict[int, int] = {}
        ordered = sorted(self.shots, key=lambda shot: (shot.track_index, shot.timeline_start_frame, shot.id))
        for shot in ordered:
            duration = self.timeline_duration(shot)
            previous_end = ends_by_track.get(shot.track_index, 0)
            if shot.timeline_start_frame < previous_end:
                raise ValueError(
                    f"shot {shot.id} overlaps a prior shot on video track {shot.track_index}; "
                    "represent transitions/composites explicitly after their gate passes"
                )
            ends_by_track[shot.track_index] = shot.timeline_start_frame + duration

    def _validate_overlay_tracks(self) -> None:
        by_track: dict[int, list[tuple[int, int, str]]] = {}
        for layer in self.texts:
            by_track.setdefault(layer.track_index, []).append((layer.start_frame, layer.end_frame, layer.id))
        for track, spans in by_track.items():
            spans.sort()
            for (_, a1, aid), (b0, _, bid) in zip(spans, spans[1:], strict=False):
                if b0 < a1:
                    raise ValueError(f"text layers {aid} and {bid} overlap on track {track}")

    # ------------------------------------------------------------- helpers
    def shot(self, shot_id: str) -> Shot:
        for shot in self.shots:
            if shot.id == shot_id:
                return shot
        raise KeyError(shot_id)

    def grade_for(self, shot_id: str) -> GradeSpec | None:
        return next((g for g in self.grades if g.shot_id == shot_id), None)

    def source(self, source_id: str) -> SourceMedia:
        return next(s for s in self.sources if s.id == source_id)

    def ordered_shots(self, track: int = 1) -> list[Shot]:
        return sorted((s for s in self.shots if s.track_index == track), key=lambda s: s.timeline_start_frame)


# ======================================================= Style knowledge
class Evidence(StrictModel):
    source_id: str
    artifact: str
    frame_or_time: str | None = None


class StyleCapsule(StrictModel):
    schema_version: str = "1.1.0"
    id: str = Field(default_factory=lambda: f"capsule_{uuid4().hex}")
    reference_id: str
    technique: str
    window_s: tuple[float, float]
    observed: str = Field(min_length=1)
    inferred: dict[str, Any]
    evidence: list[Evidence] = Field(min_length=1)
    purpose: str
    conditions: dict[str, Any] = Field(default_factory=dict)
    implementation: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    approved: bool = False
    dimension: str = "fusion"
    sources: list[str] = Field(default_factory=lambda: ["deterministic"])
    measurements: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ordered_window(self) -> StyleCapsule:
        if self.window_s[0] < 0 or self.window_s[1] <= self.window_s[0]:
            raise ValueError("capsule window must have nonnegative start and positive duration")
        return self


class StyleRule(StrictModel):
    schema_version: str = "1.1.0"
    id: str
    dimension: str
    profile: str
    kind: Literal["principle", "conditional", "negative", "exception", "preference"] = "conditional"
    when: dict[str, Any] = Field(default_factory=dict)
    prefer: dict[str, Any] = Field(default_factory=dict)
    avoid: list[str] = Field(default_factory=list)
    exceptions: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    support: int = Field(default=0, ge=0)
    contradictions: int = Field(default=0, ge=0)
    approval_state: str = "candidate"
    statement: str = ""
    effect_size: float | None = None
    validation_gain: float | None = None
    reels: int = 0
    counter_examples: list[str] = Field(default_factory=list)
    last_confirmed: str | None = None
    origin: Literal["induction", "principle_mining", "corrections", "user", "feedback"] = "induction"


class Patch(StrictModel):
    schema_version: str = "1.1.0"
    id: str = Field(default_factory=lambda: f"patch_{uuid4().hex}")
    target_edg_id: str
    base_revision: int = Field(default=1, ge=1)
    dimension: str
    operation: str
    target_id: str
    changes: dict[str, Any]
    reason: str
    confidence: float = Field(ge=0, le=1)
    requires_approval: bool = True
    group_id: str | None = None
    source: Literal["chat", "critic", "review", "steer", "marker", "learning", "user"] = "chat"


# ================================================================ runtime
class VantaEvent(StrictModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex}")
    run_id: str
    timestamp: datetime = Field(default_factory=utc_now)
    stage: str
    action: str
    target: dict[str, Any] = Field(default_factory=dict)
    page_hint: str | None = None
    because: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: EventStatus
    artifact: str | None = None
    step_id: str | None = None
    before_thumbnail: str | None = None
    after_thumbnail: str | None = None
    undo_token: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    retry_count: int = Field(default=0, ge=0)
    message: str | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    model: str | None = None
    duration_s: float | None = None


class Job(StrictModel):
    id: str = Field(default_factory=lambda: f"job_{uuid4().hex}")
    run_id: str
    kind: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    attempts: int = Field(default=0, ge=0)
    error: str | None = None


class RunManifest(StrictModel):
    schema_version: str = "1.1.0"
    run_id: str = Field(
        default_factory=lambda: f"run_{uuid4().hex}", pattern=r"^run_[A-Za-z0-9_-]{1,80}$"
    )
    project_id: str
    autonomy: AutonomyMode = AutonomyMode.ASSISTED
    run_mode: RunMode = RunMode.WATCH
    watch_delay_s: float = Field(default=0.7, ge=0, le=30)
    created_at: datetime = Field(default_factory=utc_now)
    code_version: str = "0.2.0"
    schema_versions: dict[str, str] = Field(default_factory=dict)
    model_versions: dict[str, str] = Field(default_factory=dict)
    solver_versions: dict[str, str] = Field(default_factory=dict)
    style_kb_version: str | None = None
    prompt_version: str | None = None
    resolve_version: str | None = None
    seed: int | None = None
    artifact_root: str
    plan_hash: str | None = None
    kind: str = "edit"


class QCFinding(StrictModel):
    level: int = Field(ge=0, le=5)
    kind: str
    severity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    description: str
    affected_range_s: tuple[float, float] | None = None
    repair_patch_id: str | None = None
    blocking: bool = False
    target_id: str | None = None
    repair: str | None = None
    evidence: str | None = None
    suggested: dict[str, Any] = Field(default_factory=dict)  # measured repair (e.g. grade deltas)


class QCResult(StrictModel):
    schema_version: str = "1.1.0"
    id: str = Field(default_factory=lambda: f"qc_{uuid4().hex}")
    run_id: str
    status: str
    findings: list[QCFinding] = Field(default_factory=list)
    blocking: bool = False
    iteration: int = Field(default=0, ge=0, le=3)
    artifact: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
