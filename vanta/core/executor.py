"""Validated EDG execution with explicit bindings and ownership-safe rollback."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import Field

from vanta.core.confidence import ConfidenceDisposition, decide_confidence
from vanta.core.policy import validate_edg
from vanta.core.schemas import (
    AutonomyMode,
    EditDecisionGraph,
    EventStatus,
    RunMode,
    StrictModel,
    VantaEvent,
)
from vanta.live.run_control import RunControl
from vanta.resolve.adapter import ResolveAdapter, ResolveUnavailable, VerificationFailed


class PlannedAction(StrictModel):
    """A fixed adapter operation, never executable code or raw Resolve arguments."""

    id: str = Field(pattern=r"^step_[A-Za-z0-9_-]{1,80}$")
    action: str
    stage: str
    target: dict[str, Any]
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    because: list[str] = Field(default_factory=list)
    page_hint: str | None = None
    high_impact: bool = False


class ExecutionPlan(StrictModel):
    run_id: str
    project_id: str
    artifact_root: str
    autonomy: AutonomyMode = AutonomyMode.ASSISTED
    run_mode: RunMode = RunMode.WATCH
    watch_delay_s: float = Field(default=0.7, ge=0, le=30)
    edg: EditDecisionGraph
    actions: list[PlannedAction] = Field(min_length=1)


Approval = Callable[[PlannedAction], Awaitable[bool]]
EventSink = Callable[[VantaEvent], None]
SUPPORTED_ACTIONS = frozenset({"create_timeline", "append_clip", "add_marker"})
HIGH_IMPACT_ACTIONS = frozenset()
TIMELINE_NAME = re.compile(
    r"^VANTA_[A-Za-z0-9_-]+_(?:AI_EDIT|ASSEMBLY|COLOR|FUSION|FINAL_CANDIDATE|APPROVED)_V[1-9][0-9]*$"
)


class PlanRejected(RuntimeError):
    """Raised when schema, policy, confidence, or approval gates reject a plan."""


def _plan_hash(plan: ExecutionPlan) -> str:
    payload = plan.model_dump(mode="json", exclude={"run_id"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _event(
    plan: ExecutionPlan,
    step: PlannedAction,
    status: EventStatus,
    *,
    error: str | None = None,
    artifact: str | None = None,
    details: dict[str, Any] | None = None,
) -> VantaEvent:
    return VantaEvent(
        run_id=plan.run_id,
        stage=step.stage,
        action=step.action,
        target=step.target,
        page_hint=step.page_hint,
        because=step.because,
        confidence=step.confidence,
        status=status,
        artifact=artifact,
        step_id=step.id,
        details=details or {},
        error=error,
    )


def _verify(expected: dict[str, Any], observed: dict[str, Any]) -> None:
    mismatches = {
        key: {"expected": value, "observed": observed.get(key)}
        for key, value in expected.items()
        if observed.get(key) != value
    }
    if mismatches:
        raise VerificationFailed(
            "Read-back state did not match the plan: " + json.dumps(mismatches, sort_keys=True)
        )


def _timeline_name_is_safe(name: str) -> bool:
    return bool(TIMELINE_NAME.fullmatch(name))


def _expected_for_step(
    step: PlannedAction,
    *,
    sources: dict[str, Any],
    timeline_bindings: set[str],
    run_id: str,
) -> dict[str, Any]:
    """Compile the only allowed read-back predicate for each operation."""
    params = step.parameters
    if step.action == "create_timeline":
        timeline_ref = params.get("timeline_ref")
        name = params.get("name")
        if not isinstance(timeline_ref, str) or not timeline_ref:
            raise PlanRejected("create_timeline requires a nonempty logical timeline_ref")
        if timeline_ref in timeline_bindings:
            raise PlanRejected(f"timeline_ref {timeline_ref!r} is created more than once")
        if not isinstance(name, str) or not _timeline_name_is_safe(name):
            raise PlanRejected(
                "new timeline name must use the VANTA_<project>_<stage>_V<n> ownership format"
            )
        return {"name": name}
    timeline_ref = params.get("timeline_ref")
    if not isinstance(timeline_ref, str) or timeline_ref not in timeline_bindings:
        raise PlanRejected(
            f"{step.action} refers to a timeline_ref that was not created earlier in this plan"
        )
    if step.action == "append_clip":
        source_id = params.get("source_id")
        source = sources.get(source_id)
        if source is None:
            raise PlanRejected("append_clip source_id is not an EDG source")
        try:
            source_in = int(params["source_in_frame"])
            source_out = int(params["source_out_frame"])
            timeline_start = int(params["timeline_start_frame"])
            track = int(params.get("track_index", 1))
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanRejected(
                "append_clip requires integer source and timeline frame bounds"
            ) from exc
        if source_in < 0 or source_out <= source_in or timeline_start < 0 or track < 1:
            raise PlanRejected("append_clip frame bounds or track index are invalid")
        return {
            "source_path": str(Path(source.path).expanduser().resolve()),
            "source_in_frame": source_in,
            "source_out_frame": source_out,
            "timeline_start_frame": timeline_start,
            "track_index": track,
        }
    if step.action == "add_marker":
        try:
            frame = int(params["frame"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanRejected("add_marker requires an integer frame") from exc
        color = params.get("color")
        name = params.get("name")
        note = params.get("note")
        if frame < 0 or not all(
            isinstance(value, str) and value.strip() for value in (color, name, note)
        ):
            raise PlanRejected(
                "add_marker requires a nonnegative frame and nonempty colour, name, and note"
            )
        return {
            "frame": frame,
            "color": color,
            "name": name,
            "note": note,
            "custom_data": f"vanta:{run_id}:{step.id}",
        }
    raise PlanRejected(f"Action {step.action!r} is not in the executor allowlist")


def _validate_steps(plan: ExecutionPlan) -> None:
    sources = {source.id: source for source in plan.edg.sources}
    shots = {shot.id: shot for shot in plan.edg.shots}
    timeline_bindings: set[str] = set()
    seen_ids: set[str] = set()
    for step in plan.actions:
        if step.id in seen_ids:
            raise PlanRejected(f"duplicate action id {step.id!r}")
        seen_ids.add(step.id)
        expected = _expected_for_step(
            step, sources=sources, timeline_bindings=timeline_bindings, run_id=plan.run_id
        )
        if step.expected != expected:
            raise PlanRejected(
                f"{step.id} has a read-back predicate that differs from the fixed adapter contract"
            )
        if step.action == "create_timeline":
            if step.parameters["name"] != plan.edg.timeline_name:
                raise PlanRejected(
                    "create_timeline name must equal authoritative EDG timeline_name"
                )
            timeline_bindings.add(str(step.parameters["timeline_ref"]))
        elif step.action == "append_clip":
            shot = shots.get(str(step.target.get("shot_id", "")))
            if shot is None:
                raise PlanRejected("append_clip target must identify one EDG shot")
            params = step.parameters
            if (
                params.get("source_id") != shot.source_media_id
                or params.get("source_in_frame") != shot.source_in_frame
                or params.get("source_out_frame") != shot.source_out_frame
                or params.get("timeline_start_frame") != shot.timeline_start_frame
                or params.get("track_index", 1) != shot.track_index
            ):
                raise PlanRejected(f"append_clip parameters for {shot.id} disagree with the EDG")


def _apply(
    adapter: ResolveAdapter,
    step: PlannedAction,
    *,
    snapshot_id: str,
    timeline_ids: dict[str, str],
    source_paths: dict[str, str],
    custom_data: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = step.parameters
    if step.action == "create_timeline":
        write = adapter.create_timeline(str(params["name"]), snapshot_id=snapshot_id)
        timeline_id = write.get("timeline_id")
        if not isinstance(timeline_id, str) or not timeline_id:
            raise VerificationFailed(
                "Resolve did not return a stable ID for the VANTA-created timeline"
            )
        timeline_ids[str(params["timeline_ref"])] = timeline_id
        return write, {"scope": "timeline", "timeline_id": timeline_id}
    timeline_id = timeline_ids[str(params["timeline_ref"])]
    if step.action == "append_clip":
        source_path = source_paths[str(params["source_id"])]
        write = adapter.append_clip(
            timeline_id,
            source_path,
            int(params["source_in_frame"]),
            int(params["source_out_frame"]),
            int(params["timeline_start_frame"]),
            int(params.get("track_index", 1)),
        )
        return write, {
            "scope": "timeline_item",
            "timeline_id": timeline_id,
            "source_path": source_path,
            "source_in_frame": int(params["source_in_frame"]),
            "timeline_start_frame": int(params["timeline_start_frame"]),
            "track_index": int(params.get("track_index", 1)),
        }
    if step.action == "add_marker":
        assert custom_data is not None
        write = adapter.add_marker(
            timeline_id,
            int(params["frame"]),
            str(params["color"]),
            str(params["name"]),
            str(params["note"]),
            custom_data,
        )
        return write, {"scope": "marker", "timeline_id": timeline_id, "custom_data": custom_data}
    raise PlanRejected(f"Action {step.action!r} is not in the executor allowlist")


async def execute_plan(
    plan: ExecutionPlan,
    adapter: ResolveAdapter,
    *,
    capabilities: dict[str, Any],
    project_root: str | Path,
    event_sink: EventSink,
    approval: Approval | None = None,
    control: RunControl | None = None,
) -> dict[str, Any]:
    """Apply a fixed assembly plan without modifying any baseline timeline.

    Live adapters remain unavailable until G1/G10/G16 evidence exists. Tests for
    this layer prove orchestration semantics only, never Resolve compatibility.
    """
    unknown = [step.action for step in plan.actions if step.action not in SUPPORTED_ACTIONS]
    if unknown:
        raise PlanRejected("unrecognized or unimplemented planned action(s): " + ", ".join(unknown))
    _validate_steps(plan)
    errors = validate_edg(plan.edg, capabilities=capabilities, project_root=Path(project_root))
    if errors:
        reason = "EDG execution blocked by policy: " + "; ".join(errors)
        event_sink(
            VantaEvent(
                run_id=plan.run_id,
                stage="policy",
                action="validate_edg",
                status=EventStatus.FAILED,
                error=reason,
            )
        )
        raise PlanRejected(reason)
    live = adapter.capabilities()
    if not live.get("connected") or not live.get("verified"):
        reason = "Resolve adapter is not live and gate-verified; no mutation was attempted"
        event_sink(
            VantaEvent(
                run_id=plan.run_id,
                stage="capability_gate",
                action="check_live_adapter",
                status=EventStatus.FAILED,
                error=reason,
            )
        )
        raise ResolveUnavailable(reason)
    artifact_root = Path(plan.artifact_root).resolve()
    if not artifact_root.is_dir():
        raise PlanRejected("execution artifact_root must already exist as a run-scoped directory")
    snapshot_path = artifact_root / "snapshots" / f"vanta_pre_{plan.run_id}.drp"
    snapshot_path.parent.mkdir(exist_ok=True)
    digest = _plan_hash(plan)
    event_sink(
        VantaEvent(
            run_id=plan.run_id,
            stage="snapshot",
            action="create_snapshot",
            status=EventStatus.RUNNING,
            artifact=digest,
        )
    )
    try:
        snapshot = adapter.snapshot(f"vanta_pre_{plan.run_id}", export_path=str(snapshot_path))
        snapshot_id = snapshot.get("snapshot_id")
        if (
            not isinstance(snapshot_id, str)
            or not snapshot_id
            or snapshot.get("verified") is not True
        ):
            raise VerificationFailed(
                "Snapshot did not prove project export and baseline timeline read-back"
            )
        event_sink(
            VantaEvent(
                run_id=plan.run_id,
                stage="snapshot",
                action="create_snapshot",
                status=EventStatus.VERIFIED,
                artifact=str(snapshot_path),
                target={"snapshot_id": snapshot_id},
            )
        )
    except Exception as exc:
        event_sink(
            VantaEvent(
                run_id=plan.run_id,
                stage="snapshot",
                action="create_snapshot",
                status=EventStatus.FAILED,
                artifact=str(snapshot_path),
                error=str(exc),
            )
        )
        raise
    run_control = control or RunControl(plan.run_mode, plan.watch_delay_s)
    sources = {
        source.id: str(Path(source.path).expanduser().resolve()) for source in plan.edg.sources
    }
    timeline_ids: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    current_step: PlannedAction | None = None
    wrote = False
    try:
        for step in plan.actions:
            current_step = step
            confidence = decide_confidence(
                step.confidence,
                plan.autonomy,
                high_impact=(step.high_impact or step.action in HIGH_IMPACT_ACTIONS),
            )
            event_sink(
                _event(
                    plan,
                    step,
                    EventStatus.PLANNED,
                    artifact=digest,
                    details={
                        "confidence_gate": confidence.disposition.value,
                        "reason": confidence.reason,
                    },
                )
            )
            if confidence.disposition == ConfidenceDisposition.BLOCKED:
                raise PlanRejected(confidence.reason)
            if confidence.disposition == ConfidenceDisposition.REQUIRES_APPROVAL and (
                approval is None or not await approval(step)
            ):
                raise PlanRejected(f"Approval gate rejected step {step.id}: {confidence.reason}")
            await run_control.checkpoint()
            if step.page_hint and plan.run_mode in {RunMode.WATCH, RunMode.STEP}:
                page_result = adapter.open_page(step.page_hint)
                if page_result.get("verified") is not True:
                    raise VerificationFailed(
                        f"Resolve did not verify page switch to {step.page_hint}"
                    )
            event_sink(_event(plan, step, EventStatus.RUNNING, artifact=digest))
            wrote = True
            custom_data = f"vanta:{plan.run_id}:{step.id}" if step.action == "add_marker" else None
            write_result, read_target = _apply(
                adapter,
                step,
                snapshot_id=snapshot_id,
                timeline_ids=timeline_ids,
                source_paths=sources,
                custom_data=custom_data,
            )
            observed = adapter.read_state(read_target)
            _verify(step.expected, observed)
            results.append(
                {
                    "step_id": step.id,
                    "action": step.action,
                    "write_result": write_result,
                    "observed": observed,
                }
            )
            event_sink(_event(plan, step, EventStatus.VERIFIED, artifact=digest))
    except BaseException as exc:
        if wrote:
            try:
                rollback_result = adapter.rollback(snapshot_id)
                rollback_state = adapter.read_state(
                    {"scope": "snapshot", "snapshot_id": snapshot_id}
                )
                if (
                    rollback_result.get("verified") is not True
                    or rollback_state.get("restored_snapshot_id") != snapshot_id
                ):
                    raise VerificationFailed(
                        "Rollback read-back did not prove baseline timeline selection and VANTA-only deletion"
                    )
                event_sink(
                    VantaEvent(
                        run_id=plan.run_id,
                        stage="rollback",
                        action="rollback_vanta_timelines",
                        target={"snapshot_id": snapshot_id},
                        status=EventStatus.ROLLED_BACK,
                        artifact=digest,
                    )
                )
            except Exception as rollback_error:  # noqa: BLE001
                event_sink(
                    VantaEvent(
                        run_id=plan.run_id,
                        stage="rollback",
                        action="rollback_vanta_timelines",
                        target={"snapshot_id": snapshot_id},
                        status=EventStatus.FAILED,
                        error=f"Rollback failed: {rollback_error}",
                        artifact=digest,
                    )
                )
        if current_step:
            event_sink(
                _event(plan, current_step, EventStatus.FAILED, error=str(exc), artifact=digest)
            )
        raise
    return {
        "run_id": plan.run_id,
        "plan_hash": digest,
        "snapshot_id": snapshot_id,
        "verified_steps": len(results),
        "results": results,
    }
