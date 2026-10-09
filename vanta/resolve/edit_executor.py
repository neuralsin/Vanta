"""EDG -> Resolve execution (manual 4.4, 10, 16).

Every step: event(planned) -> confidence/approval gate -> run-control checkpoint
(Pause/Step/Stop/Skip) -> page switch in WATCH/STEP -> write -> read back ->
verify -> breadcrumb marker + clip colour -> event(verified|failed). Failures
roll back the VANTA timeline and raise a Mission Control alert. Operation
classes whose capability gate has not passed are routed to the manual's
fallbacks instead of being attempted.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import numpy as np

from vanta.analysis.media import probe, read_rgb_fixed, save_jpeg
from vanta.compile.fusion import TemplateError, chain_comps, load_registry, render_template
from vanta.core.confidence import ConfidenceDisposition, decide_confidence
from vanta.core.schemas import (
    AutonomyMode,
    EditDecisionGraph,
    EventStatus,
    RunMode,
    Shot,
    VantaEvent,
)
from vanta.live.run_control import RunControl, RunStopped
from vanta.render.prerender import render_overlay_movie, render_speed_ramp
from vanta.resolve.adapter import ResolveAdapter, ResolveUnavailable, VerificationFailed
from vanta.resolve.ledger import Ledger
from vanta.solvers.grade import NODE_LABELS
from vanta.solvers.lut import read_cube, transform_error

EXECUTOR_VERSION = "edit-executor-v2"
MARKER = {"decision": "Blue", "verified": "Green", "low": "Yellow", "failed": "Red", "override": "Purple", "note": "Cyan"}
FUSION_SIZE_FACTOR = 1.35  # Text+ Size per cap-height fraction; calibrated by G3 (ledger.calibration)


class ExecutionBlocked(RuntimeError):
    pass


@dataclass
class Step:
    id: str
    kind: str
    stage: str
    description: str
    target: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    page: str | None = None
    confidence: float = 1.0
    because: list[str] = field(default_factory=list)
    high_impact: bool = False
    pre_approved: bool = False
    marker_frame: int | None = None


@dataclass
class ExecutionReport:
    run_id: str
    timeline_id: str | None
    timeline_name: str
    snapshot_id: str | None
    verified: int = 0
    skipped: int = 0
    failed: int = 0
    fallbacks: list[str] = field(default_factory=list)
    item_ids: dict[str, str] = field(default_factory=dict)
    render_path: str | None = None
    render_verified: bool = False
    grade_checks: dict[str, dict] = field(default_factory=dict)
    undo_stack: list[dict] = field(default_factory=list)
    calibration: dict[str, Any] = field(default_factory=dict)
    comp_chains: dict[str, list] = field(default_factory=dict)  # shot id -> [[order, seq, comp path]]
    comp_names: dict[str, str] = field(default_factory=dict)  # shot id -> active VANTA comp name

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class MaskMisplaced(RuntimeError):
    """Magic Mask was created on a node other than SKIN."""


EventSink = Callable[[VantaEvent], None]
Approval = Callable[[Step], Awaitable[bool]]


# steps whose failure is isolated (reported, the build continues); everything else is structural
ISOLATED_STEPS = {"text", "effect", "transition", "marker", "overlay", "fades", "reframe", "grade", "append_sfx"}

# order of layers inside a clip's single Fusion comp: picture effects, then
# titles, then transition halves (they move the whole frame), then letterbox
COMP_ORDER = {"effect": 10, "text": 20, "transition": 30, "letterbox": 40}


def _fusion_y(y: float) -> float:
    return round(1.0 - y, 5)


class EditExecutor:
    def __init__(
        self,
        adapter: ResolveAdapter,
        ledger: Ledger,
        *,
        run_id: str,
        run_dir: Path,
        emit: EventSink,
        control: RunControl,
        autonomy: AutonomyMode = AutonomyMode.ASSISTED,
        approval: Approval | None = None,
        plan_approved: bool = False,
        resolve_version: str | None = None,
    ):
        self.adapter = adapter
        self.ledger = ledger
        self.run_id = run_id
        self.run_dir = run_dir
        self.emit = emit
        self.control = control
        self.autonomy = autonomy
        self.approval = approval
        self.plan_approved = plan_approved
        self.version = resolve_version
        self.registry = load_registry()
        self.report: ExecutionReport | None = None
        self._timeline_id: str | None = None

    # ------------------------------------------------------------ helpers
    def _event(self, step: Step | None, status: EventStatus, *, stage: str | None = None, action: str | None = None, error: str | None = None, details: dict | None = None, message: str | None = None, artifact: str | None = None, undo: str | None = None, before: str | None = None, after: str | None = None, progress: float | None = None) -> None:
        self.emit(
            VantaEvent(
                run_id=self.run_id,
                stage=stage or (step.stage if step else "execute"),
                action=action or (step.kind if step else "execute"),
                target=step.target if step else {},
                page_hint=step.page if step else None,
                because=step.because if step else [],
                confidence=step.confidence if step else None,
                status=status,
                step_id=step.id if step else None,
                error=error,
                details=details or {},
                message=message or (step.description if step else None),
                artifact=artifact,
                undo_token=undo,
                before_thumbnail=before,
                after_thumbnail=after,
                progress=progress,
            )
        )

    def allows(self, operation: str) -> tuple[bool, str]:
        return self.ledger.allows(operation, current_version=self.version)

    # ----------------------------------------------------------- compile
    def compile_steps(self, edg: EditDecisionGraph, *, include_render: bool = True) -> list[Step]:
        steps: list[Step] = []
        n = 0

        def add(kind: str, stage: str, description: str, **kw: Any) -> Step:
            nonlocal n
            n += 1
            step = Step(id=f"step_{n:04d}_{kind}", kind=kind, stage=stage, description=description, **kw)
            steps.append(step)
            return step

        add("import_media", "structure", "Import source media, music and overlays into the VANTA media folder", page="media")
        add("create_timeline", "structure", f"Create {edg.timeline_name} ({edg.width}x{edg.height} @ {edg.fps:g} fps)", page="edit")
        overlay_tracks = 1 + (1 if (edg.texts or edg.captions) else 0)
        add("ensure_tracks", "structure", "Prepare video/audio tracks", params={"video": overlay_tracks, "audio": 3 if edg.sfx else 2})
        for shot in edg.ordered_shots(1):
            add(
                "append_shot",
                "assembly",
                f"Place {shot.id} ({shot.role or 'shot'}) at {shot.timeline_start_frame / edg.fps:.2f}s",
                target={"shot_id": shot.id},
                page="edit",
                confidence=shot.confidence,
                because=shot.rationale[:3],
                pre_approved=self.plan_approved,
                marker_frame=shot.timeline_start_frame,
            )
        if edg.music and not edg.music.guide_only:
            add("append_music", "audio", "Lay the music bed on A1", target={"music": edg.music.source_media_id}, page="edit", pre_approved=True)
        for cue in edg.sfx:
            add("append_sfx", "audio", f"SFX {cue.kind} at {cue.timeline_frame / edg.fps:.2f}s ({cue.purpose})", target={"sfx_id": cue.id}, pre_approved=True)
        for shot in edg.ordered_shots(1):
            if shot.audio_enabled:
                add("append_dialogue", "audio", f"Linked dialogue for {shot.id} on A2", target={"shot_id": shot.id}, pre_approved=True)
        for shot in edg.ordered_shots(1):
            if shot.reframe:
                add("reframe", "assembly", f"Reframe {shot.id}", target={"shot_id": shot.id}, confidence=shot.confidence, pre_approved=self.plan_approved)
            if shot.speed_percent != 100.0 and not shot.speed_ramp and not (shot.reframe and shot.reframe.dynamic):  # pre-renders carry their timing
                add("speed", "assembly", f"Constant speed {shot.speed_percent:.0f}% on {shot.id}", target={"shot_id": shot.id}, confidence=shot.confidence, pre_approved=self.plan_approved)
            if shot.fade_in_frames or shot.fade_out_frames:
                add("fades", "assembly", f"Fades on {shot.id}", target={"shot_id": shot.id}, pre_approved=True)
        for transition in edg.transitions:
            add(
                "transition",
                "transitions",
                f"{transition.kind.replace('_', ' ')} into {transition.to_shot} ({transition.purpose})",
                target={"transition_id": transition.id, "shot_id": transition.to_shot},
                page="edit",
                confidence=transition.confidence,
                because=[transition.purpose],
                pre_approved=self.plan_approved,
            )
        for grade in edg.grades:
            shot = edg.shot(grade.shot_id)
            add(
                "grade",
                "grade",
                f"Grade {grade.shot_id}: {grade.intent[:60] or 'solved look'}",
                target={"shot_id": grade.shot_id},
                page="color",
                confidence=grade.confidence,
                because=grade.provenance.rules[:3] + grade.rationale[-1:],
                high_impact=True,
                pre_approved=self.plan_approved,
                marker_frame=shot.timeline_start_frame + 1,
            )
        for layer in edg.texts:
            add(
                "text",
                "fusion",
                f"Title '{layer.text[:30]}' via {layer.template}",
                target={"text_id": layer.id, "shot_id": layer.shot_id},
                page="fusion",
                confidence=layer.confidence,
                because=[layer.purpose],
                high_impact=layer.template.startswith("text3d") or layer.behind_subject,
                pre_approved=self.plan_approved,
                marker_frame=layer.start_frame + 2,
            )
        for effect in edg.effects:
            add("effect", "fusion", f"{effect.template} on {effect.shot_id} ({effect.purpose})", target={"effect_id": effect.id, "shot_id": effect.shot_id}, page="fusion", confidence=effect.confidence, because=[effect.purpose], high_impact=effect.template in {"subject_glow", "subject_outline"}, pre_approved=self.plan_approved)
        if edg.captions or any(not self._fusion_ok(t) for t in edg.texts):
            add("overlay", "fusion", "Overlay track (captions / pre-rendered titles)", page="edit", pre_approved=True)
        for marker in edg.markers:
            add("marker", "markers", f"Marker {marker.name}", params={"marker": marker.model_dump()}, pre_approved=True)
        add("save", "finish", "Save the Resolve project", pre_approved=True)
        if include_render:
            add("render_proxy", "render", "Proxy render 540x960 for QC", page="deliver", pre_approved=True)
        return steps

    def _fusion_ok(self, layer) -> bool:
        ok, _ = self.allows("fusion_text")
        if layer.behind_subject:
            ok = ok and self.allows("behind_subject")[0]
        return ok and layer.template in self.registry and self.registry[layer.template].route == "clip_comp"

    # ----------------------------------------------------------- prepare
    def prepare(self, edg: EditDecisionGraph, progress: Callable[[str, float], None] | None = None) -> tuple[EditDecisionGraph, dict[str, str]]:
        """Pre-renders needed before Resolve: speed ramps (unless G13) and the overlay track."""
        media: dict[str, str] = {}
        prerender_dir = self.run_dir / "prerender"
        speed_ok, why = self.allows("speed")
        for shot in edg.shots:
            follow = bool(shot.reframe and shot.reframe.dynamic)
            if follow or (shot.speed_ramp and (shot.speed_ramp.route == "prerender" or not speed_ok)):
                what = " + ".join(x for x, on in (("optical-flow speed ramp", bool(shot.speed_ramp)), ("subject-follow reframe", follow)) if on)
                self._event(None, EventStatus.RUNNING, stage="prerender", action="conform", message=f"Rendering {what} for {shot.id} (16-bit, source colour preserved)")
                try:
                    path = render_speed_ramp(edg, shot, prerender_dir / f"{shot.id}_conform.mov")
                except (RuntimeError, OSError, ValueError) as exc:
                    if shot.speed_ramp:
                        raise
                    # a failed follow must not stop the build: keep the static subject framing
                    edg = edg.model_copy(deep=True)
                    edg.shot(shot.id).reframe = shot.reframe.model_copy(update={"keys": []})
                    self._event(None, EventStatus.WARNING, stage="prerender", action="conform", message=f"{shot.id}: subject-follow render failed ({exc}); using static subject framing")
                    continue
                media[f"ramp:{shot.id}"] = str(path)
                if follow:
                    media[f"follow:{shot.id}"] = str(path)
        if edg.captions or any(not self._fusion_ok(t) for t in edg.texts):
            self._event(None, EventStatus.RUNNING, stage="prerender", action="overlay", message="Rendering the title/caption overlay track (ProRes 4444 + alpha)")
            fallback_edg = edg.model_copy(update={"texts": [t for t in edg.texts if not self._fusion_ok(t)]})
            movie = render_overlay_movie(fallback_edg, prerender_dir / "overlay.mov", progress=(lambda p: progress("overlay", p)) if progress else None)
            if movie:
                media["overlay"] = str(movie)
        self._thumbnails(edg)
        return edg, media

    def _thumbnails(self, edg: EditDecisionGraph) -> None:
        """Before/after thumbnails rendered by VANTA itself (playhead control is unreliable)."""
        folder = self.run_dir / "thumbs"
        folder.mkdir(parents=True, exist_ok=True)
        for grade in edg.grades:
            shot = edg.shot(grade.shot_id)
            src = edg.source(shot.source_media_id)
            cap = cv2.VideoCapture(src.path)
            cap.set(cv2.CAP_PROP_POS_FRAMES, (shot.source_in_frame + shot.source_out_frame) // 2)
            ok, frame = cap.read()
            cap.release()
            if not ok:
                continue
            rgb = read_rgb_fixed(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), src.path)
            save_jpeg(rgb, folder / f"{shot.id}_before.jpg", long_edge=480)
            if grade.lut_path and Path(grade.lut_path).is_file():
                after = read_cube(grade.lut_path).apply(rgb.astype(np.float32) / 255)
                save_jpeg((np.clip(after, 0, 1) * 255).astype(np.uint8), folder / f"{shot.id}_after.jpg", long_edge=480)

    # ----------------------------------------------------------- execute
    async def execute(self, edg: EditDecisionGraph, *, include_render: bool = True, progress: Callable[[str, float], None] | None = None) -> ExecutionReport:
        live = self.adapter.capabilities()
        if not live.get("connected"):
            raise ResolveUnavailable(live.get("reason", "Resolve is not connected"))
        self.version = self.version or live.get("version_string")
        ok, why = self.allows("structure")
        if not ok:
            self._event(None, EventStatus.FAILED, stage="capability_gate", action="check_ledger", error=why)
            raise ExecutionBlocked(f"Resolve structure gate not passed ({why}). Run Settings > Resolve compatibility check first.")
        edg, prerendered = self.prepare(edg, progress)
        steps = self.compile_steps(edg, include_render=include_render)
        report = ExecutionReport(run_id=self.run_id, timeline_id=None, timeline_name=edg.timeline_name, snapshot_id=None)
        self.report = report
        snap_dir = self.run_dir / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        label = f"vanta_pre_{self.run_id}"
        self._event(None, EventStatus.RUNNING, stage="snapshot", action="create_snapshot", message="Exporting a .drp recovery snapshot of your project")
        snapshot = self.adapter.snapshot(label, export_path=str(snap_dir / f"{label}.drp"))
        report.snapshot_id = label
        self._event(None, EventStatus.VERIFIED, stage="snapshot", action="create_snapshot", artifact=snapshot.get("snapshot_path"), message="Snapshot verified")
        total = len(steps)
        try:
            for index, step in enumerate(steps):
                if progress:
                    progress("execute", index / max(1, total))
                await self._run_step(edg, step, report, prerendered, index, total)
        except BaseException as exc:
            report.failed += 1
            self._event(None, EventStatus.FAILED, stage="execute", action="abort", error=f"{type(exc).__name__}: {exc}", message="Execution stopped; rolling back the VANTA timeline")
            if report.timeline_id:
                try:
                    self.adapter.rollback(label)
                    self._event(None, EventStatus.ROLLED_BACK, stage="rollback", action="rollback_vanta_timelines", message="VANTA timeline removed; your original timelines are untouched")
                except Exception as rollback_error:
                    self._event(None, EventStatus.FAILED, stage="rollback", action="rollback_vanta_timelines", error=str(rollback_error), message="Rollback failed; restore from the .drp snapshot")
            raise
        if progress:
            progress("execute", 1.0)
        (self.run_dir / "execution_report.json").write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
        return report

    async def _gate_step(self, step: Step) -> bool:
        """Confidence + approval gate; returns False when the step must be skipped."""
        decision = decide_confidence(step.confidence, self.autonomy, high_impact=step.high_impact)
        self._event(step, EventStatus.PLANNED, details={"confidence_gate": decision.disposition.value, "reason": decision.reason})
        if decision.disposition == ConfidenceDisposition.BLOCKED:
            self._event(step, EventStatus.SKIPPED, message=f"Skipped: {decision.reason} (safe fallback: leave as is, flagged for you)")
            return False
        if decision.disposition == ConfidenceDisposition.REQUIRES_APPROVAL and not (step.pre_approved and step.confidence >= 0.7):
            if self.approval is None or not await self.approval(step):
                self._event(step, EventStatus.SKIPPED, message="Not approved")
                return False
        return True

    def _missing_dependency(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport) -> str | None:
        """Steps that act on a placed clip are skipped when that clip was not placed."""
        if step.kind in {"append_shot", "import_media", "create_timeline", "ensure_tracks", "marker", "save", "render_proxy", "overlay", "append_music", "append_sfx"}:
            return None
        needed = []
        if step.kind == "transition":
            transition = next(t for t in edg.transitions if t.id == step.target["transition_id"])
            needed = [transition.from_shot, transition.to_shot]
        elif step.kind == "text":
            return None  # titles attach to whichever covered clips exist
        elif step.target.get("shot_id"):
            needed = [step.target["shot_id"]]
        missing = [sid for sid in needed if sid not in report.item_ids]
        return f"skipped: depends on {', '.join(missing)}, which was not placed" if missing else None

    async def _run_step(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str], index: int, total: int) -> None:
        missing = self._missing_dependency(edg, step, report)
        if missing:
            report.skipped += 1
            self._event(step, EventStatus.SKIPPED, message=missing)
            return
        if not await self._gate_step(step):
            report.skipped += 1
            return
        await self.control.checkpoint()
        if self.control.consume_skip():
            report.skipped += 1
            self._event(step, EventStatus.SKIPPED, message="Skipped by you (STEP mode)")
            return
        if step.page and self.control.run_mode in {RunMode.WATCH, RunMode.STEP}:
            try:
                self.adapter.open_page(step.page)
            except ResolveUnavailable:
                pass  # follow-along is best effort (G11); breadcrumbs remain
        before = after = None
        shot_id = step.target.get("shot_id")
        if shot_id and step.kind == "grade":
            before = str(self.run_dir / "thumbs" / f"{shot_id}_before.jpg")
            after = str(self.run_dir / "thumbs" / f"{shot_id}_after.jpg")
        self._event(step, EventStatus.RUNNING, before=before, after=after, progress=index / max(1, total))
        started = time.monotonic()
        handler = getattr(self, f"_do_{step.kind}")
        try:
            result = handler(edg, step, report, prerendered)
            if asyncio.iscoroutine(result):
                result = await result
        except (ResolveUnavailable, RunStopped):
            raise
        except (TemplateError, VerificationFailed, RuntimeError, ValueError, KeyError, OSError) as exc:
            if step.kind not in ISOLATED_STEPS:
                raise  # structure is all-or-nothing: rolled back by execute()
            # one layer failing must not discard hours of work: report it loudly and keep building
            report.failed += 1
            report.fallbacks.append(f"{step.id}: FAILED ({type(exc).__name__}: {exc})")
            if step.kind == "grade" and step.target.get("shot_id") in report.item_ids:
                item_id = report.item_ids[step.target["shot_id"]]
                for action, extra in (("reset_grade", {}), ("set_clip_color", {"color": "Orange"})):
                    try:
                        self.adapter.call(action, timeline_id=report.timeline_id, item_id=item_id, **extra)
                    except Exception:
                        pass
            self._event(step, EventStatus.FAILED, error=f"{type(exc).__name__}: {exc}", message=f"{step.description}: not applied (the rest of the build continues; see the report)")
            return
        status = result.get("status", "verified")
        if status == "fallback":
            report.fallbacks.append(f"{step.id}: {result.get('message')}")
            self._event(step, EventStatus.WARNING, message=result.get("message"), details=result)
            return
        if status == "skipped":
            report.skipped += 1
            self._event(step, EventStatus.SKIPPED, message=result.get("message"), details=result)
            return
        report.verified += 1
        undo = result.get("undo")
        token = None
        if undo:
            token = f"undo_{len(report.undo_stack):05d}"
            report.undo_stack.append({"token": token, "step": step.id, **undo})
        self._event(step, EventStatus.VERIFIED, details={k: v for k, v in result.items() if k not in {"undo"}} | {"seconds": round(time.monotonic() - started, 3)}, undo=token, before=before, after=after, progress=(index + 1) / max(1, total))
        if step.marker_frame is not None and report.timeline_id and step.kind in {"append_shot", "grade", "text"}:
            color = MARKER["verified"] if step.confidence >= 0.7 else MARKER["low"]
            note = f"because {', '.join(step.because[:3]) or step.description} | conf {step.confidence:.2f}"
            try:
                self.adapter.add_marker(report.timeline_id, step.marker_frame, color, f"AP:{step.kind}"[:30], note[:240], f"vanta:{self.run_id}:{step.id}")
            except ResolveUnavailable:
                pass

    # ------------------------------------------------------------ handlers
    def _do_import_media(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        paths = sorted({s.path for s in edg.sources} | set(prerendered.values()))
        result = self.adapter.call("import_media", paths=paths, folder_name=f"VANTA_{edg.project_id[-12:]}")
        if result.get("verified") is not True:
            raise VerificationFailed("media import did not verify")
        return {"imported": result.get("imported"), "already_present": result.get("already_present")}

    def _do_create_timeline(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        fps = f"{edg.fps:g}" if abs(edg.fps - round(edg.fps)) > 1e-6 else str(int(round(edg.fps)))
        result = self.adapter.create_timeline(edg.timeline_name, snapshot_id=report.snapshot_id or "", fps=fps, width=edg.width, height=edg.height)
        report.timeline_id = result["timeline_id"]
        self._timeline_id = report.timeline_id
        report.calibration["color_science"] = result.get("color_science") or ""
        return {"timeline_id": report.timeline_id, "undo": {"action": "rollback"}}

    def _do_ensure_tracks(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        result = self.adapter.call("ensure_tracks", timeline_id=report.timeline_id, video_tracks=step.params["video"], audio_tracks=step.params["audio"])
        return {"tracks": result}

    def _source_for(self, shot: Shot, edg: EditDecisionGraph, prerendered: dict[str, str]) -> tuple[str, int, int]:
        ramp = prerendered.get(f"ramp:{shot.id}")
        if ramp:
            frames = edg.timeline_duration(shot)
            return ramp, 0, frames
        return edg.source(shot.source_media_id).path, shot.source_in_frame, shot.source_out_frame

    def _do_append_shot(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        shot = edg.shot(step.target["shot_id"])
        path, src_in, src_out = self._source_for(shot, edg, prerendered)
        result = self.adapter.append_clip(report.timeline_id or "", path, src_in, src_out, shot.timeline_start_frame, 1, media_type=1)
        item_id = result.get("item_id")
        if not item_id:
            raise VerificationFailed(f"append of {shot.id} returned no item")
        if result.get("verified") is False:
            raise VerificationFailed(f"read-back mismatch for {shot.id}: {result}")
        report.item_ids[shot.id] = item_id
        report.calibration.setdefault("source_end_convention", result.get("end_convention"))
        try:
            self.adapter.call("set_clip_color", timeline_id=report.timeline_id, item_id=item_id, color="Orange")
        except ResolveUnavailable:
            pass
        return {"item_id": item_id, "observed": {k: result.get(k) for k in ("timeline_start_frame", "source_in_frame", "source_out_frame", "duration")}}

    def _do_append_music(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        music = edg.music
        assert music is not None
        src = edg.source(music.source_media_id)
        end = music.source_out_frame or src.duration_frames or 0
        result = self.adapter.append_clip(report.timeline_id or "", src.path, music.source_in_frame, end, music.timeline_start_frame, music.track_index, media_type=2)
        if result.get("item_id"):
            report.item_ids["music"] = result["item_id"]
        if music.gain_db and result.get("item_id"):
            self.adapter.call("set_properties", timeline_id=report.timeline_id, item_id=result["item_id"], properties={"AudioVolume": music.gain_db})
        return {"item_id": result.get("item_id")}

    def _do_append_sfx(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        cue = next(c for c in edg.sfx if c.id == step.target["sfx_id"])
        src = edg.source(cue.source_media_id)
        result = self.adapter.append_clip(report.timeline_id or "", src.path, cue.source_in_frame, cue.source_out_frame, cue.timeline_frame, 3, media_type=2)
        if result.get("item_id"):
            self.adapter.call("set_properties", timeline_id=report.timeline_id, item_id=result["item_id"], properties={"AudioVolume": cue.gain_db})
        return {"item_id": result.get("item_id")}

    def _do_append_dialogue(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        shot = edg.shot(step.target["shot_id"])
        src = edg.source(shot.source_media_id)
        result = self.adapter.append_clip(report.timeline_id or "", src.path, shot.source_in_frame, shot.source_out_frame, shot.timeline_start_frame, 2, media_type=2)
        return {"item_id": result.get("item_id")}

    def _do_reframe(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        shot = edg.shot(step.target["shot_id"])
        rf = shot.reframe
        assert rf is not None
        if prerendered.get(f"follow:{shot.id}"):
            return {"status": "skipped", "message": f"{shot.id}: subject-follow is baked into the pre-render (no Resolve sizing needed)"}
        # sign conventions are calibrated by G1 evidence (ledger.calibration["pan_sign"])
        pan_sign = float(self.ledger.calibration.get("pan_sign", 1.0))
        tilt_sign = float(self.ledger.calibration.get("tilt_sign", 1.0))
        props = {"ZoomX": rf.zoom, "ZoomY": rf.zoom, "Pan": pan_sign * rf.pan * edg.width, "Tilt": tilt_sign * rf.tilt * edg.height, "RotationAngle": rf.rotation_deg}
        result = self.adapter.call("set_properties", timeline_id=report.timeline_id, item_id=report.item_ids[shot.id], properties=props)
        if not result.get("verified"):
            raise VerificationFailed(f"reframe read-back mismatch: {result.get('mismatch')}")
        return {}

    def _do_speed(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        ok, why = self.allows("speed")
        if not ok:
            return {"status": "fallback", "message": f"constant speed not applied in Resolve: {why}"}
        shot = edg.shot(step.target["shot_id"])
        result = self.adapter.call("set_speed", timeline_id=report.timeline_id, item_id=report.item_ids[shot.id], percentage=shot.speed_percent)
        if not result.get("verified"):
            raise VerificationFailed(f"speed read-back {result.get('observed')} != {shot.speed_percent}")
        return {}

    def _do_fades(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        shot = edg.shot(step.target["shot_id"])
        result = self.adapter.call("set_fades", timeline_id=report.timeline_id, item_id=report.item_ids[shot.id], fade_in=shot.fade_in_frames, fade_out=shot.fade_out_frames)
        if not result.get("verified"):
            raise VerificationFailed("fade read-back mismatch")
        return {}

    def _do_transition(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        transition = next(t for t in edg.transitions if t.id == step.target["transition_id"])
        if transition.route == "resolve_api":
            ok, why = self.allows("transitions")
            if not ok:
                return {"status": "fallback", "message": f"{transition.kind} kept as a hard cut: {why}"}
            kind = {"cross_dissolve": "Cross Dissolve", "dip_to_black": "Dip To Color Dissolve", "dip_to_white": "Dip To Color Dissolve", "smooth_cut": "Smooth Cut"}.get(transition.kind, "Cross Dissolve")
            result = self.adapter.call("add_transition", timeline_id=report.timeline_id, item_id=report.item_ids[transition.to_shot], type=kind, position="start", alignment="center", duration=transition.duration_frames)
            if not result.get("verified"):
                raise VerificationFailed(f"transition duration read-back {result.get('duration')} != {transition.duration_frames}")
            return {}
        ok, why = self.allows("fusion_text")
        if not ok or not transition.template or transition.template not in self.registry:
            return {"status": "fallback", "message": f"stylised {transition.kind} kept as a hard cut: {why}"}
        half = max(2, transition.duration_frames // 2)
        undo = []
        for shot_id, edge in ((transition.from_shot, "tail"), (transition.to_shot, "head")):
            shot = edg.shot(shot_id)
            length = edg.timeline_duration(shot)
            params = self._transition_params(transition.template, edge, length, half, transition.id)
            comp = render_template(self.registry[transition.template], params, self.run_dir / "fusion" / f"{transition.id}_{edge}.comp", width=edg.width, height=edg.height, duration_frames=length)
            undo.append(self._attach_comp(report, shot_id, comp, COMP_ORDER["transition"]))
        return {"undo": {"action": "multi", "items": undo}}

    def _attach_comp(self, report: ExecutionReport, shot_id: str, comp: Path, order: int) -> dict:
        """Add a layer to the clip's single VANTA comp (Resolve renders only the active comp)."""
        item_id = report.item_ids[shot_id]
        chain = report.comp_chains.setdefault(shot_id, [])
        previous = [list(entry) for entry in chain]
        chain.append([order, len(chain), str(comp)])
        chain.sort()
        try:
            name = self._import_chain(report, shot_id, chain)
        except Exception:
            report.comp_chains[shot_id] = previous  # the clip keeps its previous, still-active comp
            raise
        return {"action": "replace_comp", "item_id": item_id, "shot_id": shot_id, "comp_name": name, "previous": previous}

    def _import_chain(self, report: ExecutionReport, shot_id: str, chain: list) -> str | None:
        item_id = report.item_ids[shot_id]
        old = report.comp_names.get(shot_id)  # replaced only once the new comp is on the clip
        name = None
        if chain:
            if len(chain) == 1:
                path = Path(chain[0][2])
            else:
                path = self.run_dir / "fusion" / f"chain_{shot_id}_{len(chain)}_{int(time.time() * 1000) % 10**8}.comp"
                path.write_text(chain_comps([Path(entry[2]).read_text(encoding="utf-8") for entry in chain]), encoding="utf-8")
            result = self.adapter.call("import_fusion_comp", timeline_id=report.timeline_id, item_id=item_id, comp_path=str(path), comp_name=f"VANTA_{shot_id}_{len(chain)}_{uuid4().hex[:6]}")
            name = result.get("comp_name")
        if name:
            report.comp_names[shot_id] = name
        else:
            report.comp_names.pop(shot_id, None)
        if old and old != name:
            self.adapter.call("delete_fusion_comp", timeline_id=report.timeline_id, item_id=item_id, comp_name=old)
        return name



    @staticmethod
    def _transition_params(template: str, edge: str, length: int, half: int, seed_id: str) -> dict:
        tail = edge == "tail"
        in_frame, out_frame = (length - half, length - 1) if tail else (0, half)
        base = {"in_frame": max(0, in_frame), "out_frame": max(1, out_frame), "edge": edge}
        if template == "zoom_punch":
            base.update(size_a=1.0 if tail else 1.35, size_b=1.35 if tail else 1.0, blur_a=0.0 if tail else 0.12, blur_b=0.12 if tail else 0.0)
        elif template == "whip_pan":
            base.update(x_a=0.5 if tail else 0.8, x_b=0.2 if tail else 0.5, blur_a=0.0 if tail else 0.25, blur_b=0.25 if tail else 0.0)
        elif template == "flash_transition":
            base.update(gain_a=1.0 if tail else 3.0, gain_b=3.0 if tail else 1.0)
        elif template == "glitch":
            import zlib

            from vanta.reasoning.director import seeded_keys

            keys = seeded_keys(zlib.crc32(f"{seed_id}{edge}".encode()) % 10000, base["in_frame"], base["out_frame"], amplitude=0.02, every=1)
            base["keys"] = keys
        return base

    def _text_params(self, edg: EditDecisionGraph, layer, shot: Shot, clip_in: int, clip_out: int, matte_path: str | None) -> dict:
        factor = float(self.ledger.calibration.get("text_size_factor", FUSION_SIZE_FACTOR))
        params: dict[str, Any] = {
            "text": layer.text,
            "font": layer.font or "Arial",
            "style": "Bold",
            "size": round(layer.size * edg.height / edg.width * factor, 5),
            "color": layer.color.upper() if layer.color.startswith("#") else "#FFFFFF",
            "center": [layer.x, _fusion_y(layer.y)],
            "shadow_opacity": 0.55 if layer.shadow else 0.0,
            "outline": 1.0 if layer.outline else 0.0,
            "in_frame": clip_in,
            "out_frame": clip_out,
        }
        template = layer.template
        manifest = self.registry[template]
        if template == "slide":
            params["from_offset"] = [0.25, 0.5]
        if template == "typewriter":
            n = len(layer.text)
            span = max(1, int((clip_out - clip_in) * 0.55))
            keys, prev = [], "TypeBase"
            for i in range(1, n + 1):
                start = clip_in + int(span * (i - 1) / n)
                end = clip_in + int(span * i / n) if i < n else clip_out
                keys.append({"text_part": layer.text[:i], "start_frame": start, "end_frame": end, "prev_node": prev})
                prev = f"TypeMerge{i}"
            params.update(keys=keys, last_node=prev)
        if template == "kinetic_words":
            words = layer.text.split()
            per = max(1, (clip_out - clip_in) // max(1, len(words)))
            items, prev = [], "KinBase"
            for i, word in enumerate(words, 1):
                items.append({"text": word, "start_frame": clip_in + (i - 1) * per, "end_frame": clip_in + i * per, "prev_node": prev})
                prev = f"WordMerge{i}"
            params.update(words=items, last_node=prev, word_size=round(params["size"] * 1.6, 5))
            params.pop("text")
            params.pop("center")
        if template == "text_behind_subject":
            if not matte_path:
                raise TemplateError("behind-subject text needs a verified matte")
            params["matte"] = matte_path
        if template in {"tracked_callout", "text3d_hover"}:
            params.pop("center", None)
        allowed = {p.name for p in manifest.params} | {"keys", "words", "points"}
        return {k: v for k, v in params.items() if k in allowed}

    def _do_text(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        layer = next(t for t in edg.texts if t.id == step.target["text_id"])
        if not self._fusion_ok(layer):
            return {"status": "fallback", "message": f"'{layer.text}' rendered in the overlay track (Fusion text gate not passed)"}
        matte_path = None
        if layer.matte_id:
            matte = next((m for m in edg.mattes if m.id == layer.matte_id), None)
            if matte and matte.method != "pending" and (Path(matte.directory) / (matte.pattern % 0)).is_file():
                matte_path = str(Path(matte.directory) / (matte.pattern % 0))
        undo = []
        for shot in edg.ordered_shots(1):
            s0, s1 = shot.timeline_start_frame, edg.shot_end(shot)
            if layer.end_frame <= s0 or layer.start_frame >= s1 or shot.id not in report.item_ids:
                continue
            clip_in, clip_out = max(0, layer.start_frame - s0), min(s1 - s0, layer.end_frame - s0)
            shot_matte = matte_path if shot.id == layer.shot_id else None
            template = layer.template
            if template == "text_behind_subject" and not shot_matte:
                # no verified matte on this clip (not generated, or the title spans into another shot): 2.5D
                template = "text_2_5d"
                params = self._text_params(edg, layer.model_copy(update={"template": "text_2_5d"}), shot, clip_in, clip_out, None)
            else:
                params = self._text_params(edg, layer, shot, clip_in, clip_out, shot_matte)
            comp = render_template(self.registry[template], params, self.run_dir / "fusion" / f"{layer.id}_{shot.id}.comp", width=edg.width, height=edg.height, duration_frames=s1 - s0)
            undo.append(self._attach_comp(report, shot.id, comp, COMP_ORDER["text"]))
        return {"undo": {"action": "multi", "items": undo}, "clips": len(undo)}

    def _do_effect(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        effect = next(e for e in edg.effects if e.id == step.target["effect_id"])
        ok, why = self.allows("fusion_text")
        manifest = self.registry.get(effect.template)
        if not ok or manifest is None:
            return {"status": "fallback", "message": f"{effect.template} not applied: {why}"}
        matte = next((m for m in edg.mattes if m.id == effect.matte_id), None) if effect.matte_id else None
        if manifest.requires_matte and (matte is None or matte.method == "pending" or not (Path(matte.directory) / (matte.pattern % 0)).is_file()):
            return {"status": "skipped", "message": f"{effect.template} skipped: its subject matte has not been generated (Generate mattes first)"}
        if manifest.requires_matte and matte.review_required and matte.confidence < 0.6:
            return {"status": "skipped", "message": f"{effect.template} skipped: matte confidence {matte.confidence:.2f} is too low for a clean edge"}
        shot = edg.shot(effect.shot_id) if effect.shot_id else None
        if shot is None:
            return {"status": "skipped", "message": "effect is not attached to a shot"}
        s0 = shot.timeline_start_frame
        length = edg.timeline_duration(shot)
        params = {k: v for k, v in effect.params.items() if k in {p.name for p in manifest.params} | {"keys", "words", "points"}}
        params.update(in_frame=max(0, effect.start_frame - s0), out_frame=min(length, effect.end_frame - s0))
        if manifest.requires_matte and matte is not None:
            params["matte"] = str(Path(matte.directory) / (matte.pattern % 0))
        comp = render_template(manifest, params, self.run_dir / "fusion" / f"{effect.id}.comp", width=edg.width, height=edg.height, duration_frames=length)
        order = COMP_ORDER["letterbox"] if effect.template == "letterbox" else COMP_ORDER["effect"]
        return {"undo": self._attach_comp(report, shot.id, comp, order)}

    def _do_grade(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        grade = edg.grade_for(step.target["shot_id"])
        assert grade is not None
        ok, why = self.allows("grade_lut")
        if not ok:
            return {"status": "fallback", "message": f"grade for {grade.shot_id} not applied: {why}"}
        science = str(report.calibration.get("color_science") or "").casefold()
        if science and science != "davinciyrgb":
            # VANTA's shot LUTs carry the camera decode; a managed project would decode twice
            return {"status": "fallback", "message": f"grade for {grade.shot_id} not applied: the project uses colour management ({science}); set Colour science to DaVinci YRGB for this project and rebuild"}
        if not grade.lut_path or not Path(grade.lut_path).is_file():
            raise VerificationFailed(f"LUT for {grade.shot_id} is missing")
        item_id = report.item_ids[grade.shot_id]
        drx = self._drx_template()
        if drx and grade.look_lut_path and Path(grade.look_lut_path).is_file() and grade.tech_cdl:
            people = float((grade.params or {}).get("skin_protect", 0.0)) > 0 and self.allows("magic_mask")[0]
            try:
                check = self._grade_via_drx(grade, item_id, drx, report, magic_mask=people)
            except MaskMisplaced as exc:
                # the mask landed on the wrong node: rebuild the tree cleanly without it
                self._event(step, EventStatus.WARNING, message=f"Magic Mask not placed on SKIN ({exc}); building the tree without it")
                self.adapter.call("reset_grade", timeline_id=report.timeline_id, item_id=item_id)
                try:
                    check = self._grade_via_drx(grade, item_id, drx, report, magic_mask=False)
                except (VerificationFailed, RuntimeError, ValueError, KeyError) as retry:
                    check = {"passed": False, "error": f"{type(retry).__name__}: {retry}"}
            except (VerificationFailed, RuntimeError, ValueError, KeyError) as exc:
                check = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
            if check.get("passed"):
                check["route"] = "drx_template"
                report.grade_checks[grade.shot_id] = check
                self.adapter.call("set_clip_color", timeline_id=report.timeline_id, item_id=item_id, color="Green")
                return {"lut": grade.look_lut_path, "route": "drx_template", "export_check": check, "undo": {"action": "reset_grade", "item_id": item_id}}
            # the editable tree did not reproduce the intent: reset and use the single verified LUT
            self.adapter.call("reset_grade", timeline_id=report.timeline_id, item_id=item_id)
            reason = check.get("error") or f"{check.get('mean_de')} dE from intent"
            self._event(step, EventStatus.WARNING, message=f"8-node grade did not verify ({reason}); using the single verified LUT")
        result = self.adapter.call("set_lut", timeline_id=report.timeline_id, item_id=item_id, node_index=1, lut_path=grade.lut_path)
        if not result.get("verified"):
            raise VerificationFailed(f"GetLUT read-back {result.get('observed_lut')} != {grade.lut_path}")
        check = self._export_check(grade, item_id, report)
        check["route"] = "lut_only"
        report.grade_checks[grade.shot_id] = check
        color = "Green" if check["passed"] else "Yellow"
        self.adapter.call("set_clip_color", timeline_id=report.timeline_id, item_id=item_id, color=color)
        if not check["passed"]:
            self._event(step, EventStatus.WARNING, message=f"exported LUT differs from intent by {check.get('mean_de')} dE (needs review)")
        return {"lut": grade.lut_path, "route": "lut_only", "export_check": check, "undo": {"action": "reset_grade", "item_id": item_id}}

    def _drx_template(self) -> str | None:
        """The validated 8-node template, when G4b has passed on this Resolve version."""
        ok, _ = self.allows("grade_drx")
        template = (self.ledger.calibration.get("drx_template") or {}).get("path")
        return template if ok and template and Path(template).is_file() else None

    def _export_check(self, grade, item_id: str, report: ExecutionReport) -> dict:
        """Export the clip's whole grade from Resolve and compare it with the intended full transform."""
        export_path = self.run_dir / "verify" / f"{grade.shot_id}_exported.cube"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        intended = read_cube(grade.lut_path)
        size = intended.size if intended.size in (17, 33, 65) else 33
        export = self.adapter.call("export_lut", timeline_id=report.timeline_id, item_id=item_id, export_path=str(export_path), size=size)
        if not export.get("verified"):
            return {"mean_de": None, "passed": False}
        check = {**transform_error(read_cube(export_path).apply, intended.apply), "passed": False}
        check["passed"] = check["mean_de"] < 1.0
        return check

    def _grade_via_drx(self, grade, item_id: str, drx: str, report: ExecutionReport, *, magic_mask: bool = False) -> dict:
        """Editable 8-node tree: TECH = camera decode, BALANCE = CDL, LOOK = look LUT, the rest are handles.

        With ``magic_mask`` the identity SKIN node carries a tracked Magic Mask of the people in the
        shot: a ready isolation for the colourist that does not change the verified image.
        """
        tl = report.timeline_id
        applied = self.adapter.call("apply_drx", timeline_id=tl, item_id=item_id, drx_path=drx, grade_mode=0)
        labels = [str(label or "").strip().upper() for label in applied.get("labels", [])]
        if not applied.get("verified") or labels[: len(NODE_LABELS)] != list(NODE_LABELS):
            raise VerificationFailed(f"DRX template nodes {labels} != {list(NODE_LABELS)}")
        node = {label: i + 1 for i, label in enumerate(NODE_LABELS)}
        if grade.decode_lut_path:
            res = self.adapter.call("set_lut", timeline_id=tl, item_id=item_id, node_index=node["TECH"], lut_path=grade.decode_lut_path)
            if not res.get("verified"):
                raise VerificationFailed("TECH decode LUT did not read back")
        cdl = grade.tech_cdl or {}
        self.adapter.call("set_cdl", timeline_id=tl, item_id=item_id, node_index=node["BALANCE"], slope=str(cdl["Slope"]), offset=str(cdl["Offset"]), power=str(cdl["Power"]), saturation=float(cdl.get("Saturation", 1.0)))
        res = self.adapter.call("set_lut", timeline_id=tl, item_id=item_id, node_index=node["LOOK"], lut_path=grade.look_lut_path)
        if not res.get("verified"):
            raise VerificationFailed("LOOK LUT did not read back")
        for label in ("TONE", "SEP", "BG", "SKIN", "POLISH"):  # identity handles for the colourist
            self.adapter.call("set_node_enabled", timeline_id=tl, item_id=item_id, node_index=node[label], enabled=bool(grade.node_enabled.get(label, True)))
        check = self._export_check(grade, item_id, report)
        if magic_mask and check.get("passed"):
            mask = self.adapter.call("create_magic_mask", timeline_id=tl, item_id=item_id, node_index=node["SKIN"], mode="BI")
            if not mask.get("verified"):
                raise MaskMisplaced(f"mask on nodes {mask.get('nodes_with_mask')}")
            check["magic_mask"] = "SKIN"
        return check

    def _do_overlay(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        movie = prerendered.get("overlay")
        if not movie:
            return {"status": "skipped", "message": "no overlay content"}
        info = probe(movie)
        frames = info.frame_count or edg.duration_frames
        result = self.adapter.append_clip(report.timeline_id or "", movie, 0, frames, 0, 2, media_type=1)
        return {"item_id": result.get("item_id")}

    def _do_marker(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        marker = step.params["marker"]
        custom = f"vanta:{self.run_id}:{step.id}"
        self.adapter.add_marker(report.timeline_id or "", marker["frame"], marker["color"], marker["name"][:30], marker.get("note") or marker["name"], custom)
        observed = self.adapter.read_state({"scope": "marker", "timeline_id": report.timeline_id, "custom_data": custom})
        if observed.get("frame") != marker["frame"]:
            raise VerificationFailed(f"marker read-back frame {observed.get('frame')} != {marker['frame']}")
        return {"undo": {"action": "delete_marker", "custom_data": custom}}

    def _do_save(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        result = self.adapter.call("save_project")
        if not result.get("verified"):
            raise VerificationFailed("project save did not verify")
        return {}

    async def _do_render_proxy(self, edg: EditDecisionGraph, step: Step, report: ExecutionReport, prerendered: dict[str, str]) -> dict:
        ok, why = self.allows("render")
        if not ok:
            return {"status": "fallback", "message": why}
        target = self.run_dir / "proxy"
        name = f"{edg.timeline_name}_proxy"
        started = self.adapter.call("render_start", timeline_id=report.timeline_id, target_dir=str(target), custom_name=name, width=540, height=960, quality="Medium", format="mp4", codec="H264")
        job_id = started["job_id"]
        last_pct, last_change = -1, time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            if self.control.stopped:
                self.adapter.call("render_stop")
                raise RunStopped("render stopped by user")
            status = self.adapter.call("render_status", job_id=job_id)
            state = (status.get("status") or {}).get("JobStatus", "")
            pct = int((status.get("status") or {}).get("CompletionPercentage") or 0)
            if pct != last_pct:
                last_pct, last_change = pct, time.monotonic()
                self._event(step, EventStatus.RUNNING, message=f"Rendering proxy {pct}%", progress=pct / 100)
            if state == "Complete":
                break
            if state in {"Failed", "Cancelled"}:
                raise VerificationFailed(f"render {state}: {(status.get('status') or {}).get('Error')}")
            if time.monotonic() - last_change > 180:
                self.adapter.call("render_stop")
                raise VerificationFailed("render watchdog: no progress for 3 minutes")
        files = sorted(target.glob(f"{name}*"), key=lambda p: p.stat().st_mtime)
        if not files:
            raise VerificationFailed("render reported Complete but no file exists")
        path = files[-1]
        info = probe(path)
        expected = edg.duration_frames / edg.fps
        verified = abs(info.duration_s - expected) <= 2 / edg.fps + 0.05 and info.has_video and (info.has_audio or not edg.music)
        report.render_path = str(path)
        report.render_verified = verified
        if not verified:
            raise VerificationFailed(f"proxy render verification failed: duration {info.duration_s:.2f}s vs {expected:.2f}s, audio={info.has_audio}")
        return {"path": str(path), "duration_s": info.duration_s, "fps": info.fps}

    # ---------------------------------------------------------------- undo
    def undo_last(self) -> dict:
        report = self.report
        if not report or not report.undo_stack:
            raise ValueError("nothing to undo")
        entry = report.undo_stack.pop()
        items = entry["items"] if entry.get("action") == "multi" else [entry]
        for item in items:
            action = item["action"]
            if action == "rollback":
                self.adapter.rollback(report.snapshot_id or "")
            elif action == "delete_marker":
                self.adapter.call("delete_marker", timeline_id=report.timeline_id, custom_data=item["custom_data"])
            elif action == "replace_comp":
                report.comp_chains[item["shot_id"]] = [list(entry) for entry in item["previous"]]
                self._import_chain(report, item["shot_id"], report.comp_chains[item["shot_id"]])
            elif action == "delete_fusion_comp" and item.get("comp_name"):
                self.adapter.call("delete_fusion_comp", timeline_id=report.timeline_id, item_id=item["item_id"], comp_name=item["comp_name"])
            elif action == "reset_grade":
                self.adapter.call("reset_grade", timeline_id=report.timeline_id, item_id=item["item_id"])
                self.adapter.call("clear_clip_color", timeline_id=report.timeline_id, item_id=item["item_id"])
        self._event(None, EventStatus.ROLLED_BACK, stage="undo", action="undo_last", message=f"Undid {entry['step']}")
        return entry
