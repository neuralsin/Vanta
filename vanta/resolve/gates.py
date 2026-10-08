"""Opt-in, disposable-project capability-gate runners.

These runners collect evidence. They never mark `capabilities.yaml` as passed;
only a reviewed result may update the gate ledger after the operator inspects it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from vanta.core.schemas import StrictModel
from vanta.resolve.adapter import ResolveAdapter, ResolveUnavailable, VerificationFailed


class GateClip(StrictModel):
    source_path: str = Field(min_length=1)
    source_in_frame: int = Field(ge=0)
    source_out_frame: int = Field(gt=0)
    timeline_start_frame: int = Field(ge=0)
    track_index: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def valid_range(self) -> GateClip:
        if self.source_out_frame <= self.source_in_frame:
            raise ValueError("source_out_frame must be after source_in_frame")
        return self


class G1Fixture(StrictModel):
    """Five already-imported clips in a manually selected disposable project."""

    project_name: str = Field(pattern=r"^VANTA_GATE_[A-Za-z0-9_-]{1,80}$")
    baseline_timeline_name: str = Field(min_length=1)
    timeline_name: str = Field(pattern=r"^VANTA_GATE_[A-Za-z0-9_-]+_ASSEMBLY_V[1-9][0-9]*$")
    clips: list[GateClip] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def safe_fixture(self) -> G1Fixture:
        paths = [str(Path(clip.source_path).expanduser().resolve()) for clip in self.clips]
        if len(paths) != len(set(paths)):
            raise ValueError("G1 fixture requires five distinct source files")
        if any(clip.track_index != 1 for clip in self.clips):
            raise ValueError("G1 currently measures video trackIndex=1 only")
        previous_end = 0
        for clip in sorted(self.clips, key=lambda item: item.timeline_start_frame):
            if clip.timeline_start_frame < previous_end:
                raise ValueError("G1 fixture clips must not overlap")
            previous_end = clip.timeline_start_frame + (
                clip.source_out_frame - clip.source_in_frame
            )
        return self


class GateOutcome(StrictModel):
    gate: str
    status: str
    evidence: dict[str, Any]
    error: str | None = None


def _verify(expected: dict[str, Any], observed: dict[str, Any]) -> None:
    mismatch = {
        key: {"expected": value, "observed": observed.get(key)}
        for key, value in expected.items()
        if observed.get(key) != value
    }
    if mismatch:
        raise VerificationFailed(f"gate read-back mismatch: {mismatch}")


def run_g1(
    adapter: ResolveAdapter, fixture: G1Fixture, *, artifact_root: str | Path
) -> GateOutcome:
    """Exercise direct assembly with five clips and always remove the gate timeline.

    Preconditions are intentionally strict: Resolve must already be open with Local
    scripting enabled, the current project name must have the disposable `VANTA_GATE_`
    prefix, the baseline timeline must already exist, and the five sources must have
    been imported manually. The runner does not create a project, import media, or
    launch Resolve.
    """
    root = Path(artifact_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("artifact_root must already exist")
    for clip in fixture.clips:
        if not Path(clip.source_path).expanduser().is_file():
            raise FileNotFoundError(f"G1 source is absent: {clip.source_path}")
    project_state = adapter.read_state({"scope": "project"})
    if project_state.get("project_name") != fixture.project_name:
        raise ResolveUnavailable(
            "current Resolve project does not match the explicit disposable G1 project"
        )
    timelines = project_state.get("timelines", [])
    if not any(item.get("name") == fixture.baseline_timeline_name for item in timelines):
        raise ResolveUnavailable(
            "configured G1 baseline timeline is absent from the current project"
        )
    if any(item.get("name") == fixture.timeline_name for item in timelines):
        raise ResolveUnavailable(
            "G1 timeline name already exists; inspect or remove the prior gate artifact manually"
        )

    snapshot_id: str | None = None
    created = False
    operations: list[dict[str, Any]] = []
    outcome: GateOutcome
    try:
        snapshot = adapter.snapshot("g1_preflight", export_path=str(root / "g1_preflight.drp"))
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        if not snapshot_id or snapshot.get("verified") is not True:
            raise VerificationFailed("G1 snapshot did not verify")
        created_result = adapter.create_timeline(fixture.timeline_name, snapshot_id=snapshot_id)
        timeline_id = str(created_result.get("timeline_id") or "")
        if not timeline_id or created_result.get("verified") is not True:
            raise VerificationFailed("G1 did not receive a verified VANTA timeline identity")
        created = True
        _verify(
            {"name": fixture.timeline_name, "timeline_id": timeline_id},
            adapter.read_state({"scope": "timeline", "timeline_id": timeline_id}),
        )
        for index, clip in enumerate(fixture.clips, start=1):
            source_path = str(Path(clip.source_path).expanduser().resolve())
            adapter.append_clip(
                timeline_id,
                source_path,
                clip.source_in_frame,
                clip.source_out_frame,
                clip.timeline_start_frame,
                clip.track_index,
            )
            expected = {
                "source_path": source_path,
                "source_in_frame": clip.source_in_frame,
                "source_out_frame": clip.source_out_frame,
                "timeline_start_frame": clip.timeline_start_frame,
                "track_index": clip.track_index,
            }
            _verify(
                expected,
                adapter.read_state(
                    {
                        "scope": "timeline_item",
                        "timeline_id": timeline_id,
                        "source_path": source_path,
                        "source_in_frame": clip.source_in_frame,
                        "timeline_start_frame": clip.timeline_start_frame,
                        "track_index": clip.track_index,
                    }
                ),
            )
            marker_key = f"vanta:g1:clip:{index}"
            adapter.add_marker(
                timeline_id,
                clip.timeline_start_frame,
                "Green",
                f"VANTA G1 clip {index}",
                "verified source trim and record frame",
                marker_key,
            )
            _verify(
                {
                    "frame": clip.timeline_start_frame,
                    "color": "Green",
                    "name": f"VANTA G1 clip {index}",
                    "note": "verified source trim and record frame",
                    "custom_data": marker_key,
                },
                adapter.read_state(
                    {"scope": "marker", "timeline_id": timeline_id, "custom_data": marker_key}
                ),
            )
            operations.append({"clip": index, **expected, "marker_custom_data": marker_key})
        before_retry = adapter.read_state({"scope": "project"}).get("timelines", [])
        try:
            adapter.create_timeline(fixture.timeline_name, snapshot_id=snapshot_id)
        except ResolveUnavailable:
            after_retry = adapter.read_state({"scope": "project"}).get("timelines", [])
            if after_retry != before_retry:
                raise VerificationFailed("G1 idempotence retry changed the project's timeline list")
        else:
            raise VerificationFailed(
                "G1 idempotence retry created or reused a timeline without explicit verification"
            )
        outcome = GateOutcome(
            gate="G1",
            status="PASS_CANDIDATE",
            evidence={
                "fixture": fixture.model_dump(mode="json"),
                "snapshot": snapshot,
                "operations": operations,
                "idempotence": "duplicate timeline creation rejected without changing timeline list",
            },
        )
    except Exception as exc:  # noqa: BLE001 - gate evidence must capture vendor and verification failures
        outcome = GateOutcome(
            gate="G1",
            status="FAIL",
            evidence={"operations": operations},
            error=f"{type(exc).__name__}: {exc}",
        )
    if created and snapshot_id:
        try:
            rollback = adapter.rollback(snapshot_id)
            if (
                rollback.get("verified") is not True
                or rollback.get("restored_snapshot_id") != snapshot_id
            ):
                raise VerificationFailed("G1 rollback was not verified")
            outcome.evidence["rollback"] = rollback
        except Exception as exc:  # noqa: BLE001 - rollback failure must turn a gate candidate into failure
            return GateOutcome(
                gate="G1",
                status="FAIL",
                evidence={
                    "prior_outcome": outcome.model_dump(mode="json"),
                    "operations": operations,
                },
                error=f"rollback failure: {type(exc).__name__}: {exc}",
            )
    return outcome
