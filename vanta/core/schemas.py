"""Versioned data contracts shared by the planner, UI, and Resolve adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class SliderSet(StrictModel):
    pacing: float = Field(default=0.5, ge=0, le=1)
    beat_sync: float = Field(default=0.5, ge=0, le=1)
    color_intensity: float = Field(default=0.5, ge=0, le=1)
    skin_priority: float = Field(default=0.8, ge=0, le=1)
    masking: float = Field(default=0, ge=0, le=1)
    three_d: float = Field(default=0, ge=0, le=1)
    typography_density: float = Field(default=0.35, ge=0, le=1)
    music_priority: float = Field(default=0.5, ge=0, le=1)


class EditSpec(StrictModel):
    schema_version: str = "1.0.0"
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

    @model_validator(mode="after")
    def validate_reference_weights(self) -> EditSpec:
        for reference, dimensions in self.reference_weights.items():
            if not reference.strip():
                raise ValueError("reference IDs cannot be empty")
            if any(not 0 <= value <= 1 for value in dimensions.values()):
                raise ValueError("reference weights must be between 0 and 1")
        if min(self.resolution) < 1:
            raise ValueError("resolution dimensions must be positive")
        return self


class SourceMedia(StrictModel):
    id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: str | None = None
    media_type: Literal["video", "audio", "audiovisual", "still"] = "video"
    duration_frames: int | None = Field(default=None, gt=0)
    fps: float | None = Field(default=None, gt=0)
    read_only: bool = True


class Shot(StrictModel):
    id: str = Field(default_factory=lambda: f"shot_{uuid4().hex[:12]}")
    source_media_id: str
    source_in_frame: int = Field(ge=0)
    source_out_frame: int = Field(gt=0)
    timeline_start_frame: int = Field(ge=0)
    track_index: int = Field(default=1, ge=1)
    confidence: float = Field(default=1, ge=0, le=1)
    rationale: list[str] = Field(default_factory=list)

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


class EditDecisionGraph(StrictModel):
    schema_version: str = "1.0.0"
    id: str = Field(default_factory=lambda: f"edg_{uuid4().hex}")
    project_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    fps: float = Field(gt=0, le=240)
    timeline_name: str = Field(min_length=1)
    sources: list[SourceMedia] = Field(min_length=1)
    shots: list[Shot] = Field(default_factory=list)
    music: MusicBed | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

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
        return self

    def _validate_track_layout(self) -> None:
        """Reject ambiguous same-track overlap in the portable edit graph.

        Intentional gaps are allowed and must survive compilation. Overlaps need a
        transition/composite representation and cannot be smuggled into the Phase 1
        hard-cut assembly.
        """
        from vanta.core.timing import TimingError, source_frames_to_timeline_frames

        sources = {source.id: source for source in self.sources}
        ends_by_track: dict[int, int] = {}
        ordered = sorted(
            self.shots, key=lambda shot: (shot.track_index, shot.timeline_start_frame, shot.id)
        )
        for shot in ordered:
            source = sources[shot.source_media_id]
            try:
                duration = source_frames_to_timeline_frames(
                    shot.duration_frames,
                    source.fps or self.fps,
                    self.fps,
                )
            except TimingError as exc:
                raise ValueError(f"shot {shot.id}: {exc}") from exc
            previous_end = ends_by_track.get(shot.track_index, 0)
            if shot.timeline_start_frame < previous_end:
                raise ValueError(
                    f"shot {shot.id} overlaps a prior shot on video track {shot.track_index}; "
                    "represent transitions/composites explicitly after their gate passes"
                )
            ends_by_track[shot.track_index] = shot.timeline_start_frame + duration


class Evidence(StrictModel):
    source_id: str
    artifact: str
    frame_or_time: str | None = None


class StyleCapsule(StrictModel):
    schema_version: str = "1.0.0"
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

    @model_validator(mode="after")
    def ordered_window(self) -> StyleCapsule:
        if self.window_s[0] < 0 or self.window_s[1] <= self.window_s[0]:
            raise ValueError("capsule window must have nonnegative start and positive duration")
        return self


class StyleRule(StrictModel):
    schema_version: str = "1.0.0"
    id: str
    dimension: str
    profile: str
    when: dict[str, Any] = Field(default_factory=dict)
    prefer: dict[str, Any] = Field(default_factory=dict)
    avoid: list[str] = Field(default_factory=list)
    exceptions: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    support: int = Field(default=0, ge=0)
    contradictions: int = Field(default=0, ge=0)
    approval_state: str = "candidate"


class Patch(StrictModel):
    schema_version: str = "1.0.0"
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
    schema_version: str = "1.0.0"
    run_id: str = Field(
        default_factory=lambda: f"run_{uuid4().hex}", pattern=r"^run_[A-Za-z0-9_-]{1,80}$"
    )
    project_id: str
    autonomy: AutonomyMode = AutonomyMode.ASSISTED
    run_mode: RunMode = RunMode.WATCH
    watch_delay_s: float = Field(default=0.7, ge=0, le=30)
    created_at: datetime = Field(default_factory=utc_now)
    code_version: str = "0.1.0"
    schema_versions: dict[str, str] = Field(default_factory=dict)
    model_versions: dict[str, str] = Field(default_factory=dict)
    solver_versions: dict[str, str] = Field(default_factory=dict)
    style_kb_version: str | None = None
    prompt_version: str | None = None
    resolve_version: str | None = None
    seed: int | None = None
    artifact_root: str
    plan_hash: str | None = None


class QCFinding(StrictModel):
    level: int = Field(ge=0, le=5)
    kind: str
    severity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    description: str
    affected_range_s: tuple[float, float] | None = None
    repair_patch_id: str | None = None


class QCResult(StrictModel):
    schema_version: str = "1.0.0"
    id: str = Field(default_factory=lambda: f"qc_{uuid4().hex}")
    run_id: str
    status: str
    findings: list[QCFinding] = Field(default_factory=list)
    blocking: bool = False
    iteration: int = Field(default=0, ge=0, le=3)
    artifact: str | None = None
