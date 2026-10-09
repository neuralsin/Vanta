"""VANTA service: the one object the desktop app drives.

It owns the library, event ledger, job runner, Style Brain and run artifacts,
and exposes every stage of the manual's loop as a job:

import -> analyse -> style -> plan (steer board) -> mattes -> preview ->
execute in Resolve (WATCH/STEP/TURBO) -> proxy render -> QC -> critic ->
repair (<= 3 iterations) -> final candidate -> corrections -> preferences.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vanta import __version__
from vanta.analysis.pipeline import AnalysisOptions, analyse_media, analyse_music, load_analysis
from vanta.config import Settings, get_settings
from vanta.core.schemas import (
    AutonomyMode,
    EditDecisionGraph,
    EditSpec,
    EventStatus,
    RunManifest,
    RunMode,
    RunStatus,
    VantaEvent,
)
from vanta.ingest.downloader import extract_urls
from vanta.ingest.library_import import import_file, import_links
from vanta.live.run_control import RunControl
from vanta.storage.artifacts import ArtifactStore
from vanta.storage.event_store import EventStore
from vanta.storage.library import Library
from vanta.style.brain import StyleBrain
from vanta.workers.jobs import JobCancelled, JobHandle, JobRunner

LIBRARY_RUN = "run_library"


@dataclass
class RunState:
    run_id: str
    project_id: str
    run_dir: Path
    control: RunControl
    edg: EditDecisionGraph | None = None
    plan: Any = None
    report: Any = None
    executor: Any = None
    stage: str = "queued"
    progress: float = 0.0
    qc: list[dict] = field(default_factory=list)
    preview: str | None = None
    pending_approval: dict | None = None
    approval_event: threading.Event = field(default_factory=threading.Event)
    approval_answer: bool = False
    warnings: list[str] = field(default_factory=list)


class VantaService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        self.settings.apply_model_cache_env()
        self.library = Library(self.settings.db_path)
        self.events = EventStore(self.settings.db_path)
        self.artifacts = ArtifactStore(self.settings.data_dir)
        self.brain = StyleBrain(self.library, self.settings)
        self._subscribers: list[Callable[[dict], None]] = []
        self._job_subscribers: list[Callable[[JobHandle], None]] = []
        self.jobs = JobRunner(self.events, on_update=self._job_update)
        self.runs: dict[str, RunState] = {}
        self._lock = threading.RLock()
        self._ensure_library_run()

    # ------------------------------------------------------------ events
    def _ensure_library_run(self) -> None:
        if not self.events.get_run(LIBRARY_RUN):
            self.events.create_run(RunManifest(run_id=LIBRARY_RUN, project_id="library", artifact_root=str(self.settings.data_dir), kind="library"))

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        self._subscribers.append(callback)

    def subscribe_jobs(self, callback: Callable[[JobHandle], None]) -> None:
        self._job_subscribers.append(callback)

    def _job_update(self, handle: JobHandle) -> None:
        for cb in list(self._job_subscribers):
            try:
                cb(handle)
            except Exception:
                pass

    def emit(self, event: VantaEvent) -> None:
        try:
            sequence = self.events.add_event(event)
        except Exception:
            sequence = -1
        payload = {"sequence": sequence, **event.model_dump(mode="json")}
        state = self.runs.get(event.run_id)
        if state:
            state.stage = event.stage
            if event.progress is not None:
                state.progress = event.progress
            mirror = state.run_dir / "events" / "events.jsonl"
            mirror.parent.mkdir(parents=True, exist_ok=True)
            with mirror.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        for cb in list(self._subscribers):
            try:
                cb(payload)
            except Exception:
                pass

    def _say(self, run_id: str, stage: str, message: str, *, status: EventStatus = EventStatus.INFO, progress: float | None = None, action: str = "progress", details: dict | None = None, error: str | None = None) -> None:
        self.emit(VantaEvent(run_id=run_id, stage=stage, action=action, status=status, message=message, progress=progress, details=details or {}, error=error))

    def _progress_for(self, handle: JobHandle, run_id: str, stage_prefix: str = ""):
        def progress(stage: str, fraction: float, message: str) -> None:
            if handle.cancelled:
                raise JobCancelled("cancelled")
            handle.progress = fraction
            handle.message = message
            self._job_update(handle)
            self._say(run_id, f"{stage_prefix}{stage}", message, status=EventStatus.RUNNING, progress=fraction)

        return progress

    # ----------------------------------------------------------- library
    def import_links(self, text: str, *, profile_name: str | None = None, analyse: bool = True) -> JobHandle | None:
        urls = extract_urls(text)
        if not urls:
            return None
        profile = self.library.ensure_profile(profile_name) if profile_name else None

        def job(handle: JobHandle) -> dict:
            def progress(url: str, fraction: float, status: str) -> None:
                handle.progress = fraction
                handle.message = f"{status} {url}"
                self._job_update(handle)

            records, failures = import_links(self.library, urls, settings=self.settings, profile_id=profile["id"] if profile else None, progress=progress)
            for url, error in failures:
                self._say(LIBRARY_RUN, "ingest", f"Could not download {url}: {error}", status=EventStatus.WARNING, error=error)
            for record in records:
                self._say(LIBRARY_RUN, "ingest", f"Downloaded '{record.get('title')}'", status=EventStatus.VERIFIED)
                if analyse:
                    self.analyse(record["id"])
            return {"records": [r["id"] for r in records], "failures": failures}

        return self.jobs.submit(LIBRARY_RUN, "download", f"Download {len(urls)} reel(s)", job, lane="light", retries=1)

    def import_files(self, paths: list[str], kind: str, *, analyse: bool = True, profile_name: str | None = None, rights: dict | None = None) -> list[dict]:
        records, errors = [], []
        profile = self.library.ensure_profile(profile_name) if profile_name else None
        for path in paths:
            try:
                record = import_file(self.library, path, kind, profile_id=profile["id"] if profile else None, rights=rights)
                records.append(record)
                if analyse and record.get("analysis_status") != "done":
                    self.analyse(record["id"])
            except Exception as exc:
                errors.append((path, str(exc)))
                self._say(LIBRARY_RUN, "ingest", f"Could not import {Path(path).name}: {exc}", status=EventStatus.WARNING, error=str(exc))
        return records

    def analyse(self, media_id: str, *, options: AnalysisOptions | None = None) -> JobHandle:
        media = self.library.get_media(media_id)
        if not media:
            raise KeyError(media_id)
        title = media.get("title") or Path(media["path"]).name

        def job(handle: JobHandle) -> dict:
            progress = self._progress_for(handle, LIBRARY_RUN, "analysis.")
            try:
                if media["kind"] in {"music", "sfx"}:
                    result = analyse_music(media, self.library, settings=self.settings, progress=progress)
                else:
                    result = analyse_media(media, self.library, settings=self.settings, options=options, progress=progress, cancelled=lambda: handle.cancelled)
            except Exception as exc:
                self.library.update_media(media_id, analysis_status="failed", error=str(exc))
                self._say(LIBRARY_RUN, "analysis", f"Analysis of {title} failed: {exc}", status=EventStatus.FAILED, error=str(exc))
                raise
            self._say(LIBRARY_RUN, "analysis", f"Analysed {title}", status=EventStatus.VERIFIED)
            if media["kind"] == "reference":
                fresh = self.library.get_media(media_id)
                self.brain.ingest_reference(fresh)
                if not fresh.get("profile_id"):
                    profile = self.brain.assign(media_id, self.brain.suggest_profile(fresh))
                else:
                    profile = self.library.get_profile(fresh["profile_id"])
                self.brain.rebuild_profile(profile["id"])
                self._say(LIBRARY_RUN, "style", f"Style Brain updated for profile '{profile['name']}'", status=EventStatus.VERIFIED)
            return {"media_id": media_id, "shots": len(result.get("shots", [])) if isinstance(result, dict) else 0}

        return self.jobs.submit(LIBRARY_RUN, f"analyse_{media['kind']}", f"Analyse {title}", job, lane="heavy")

    def rebuild_style(self, profile_id: str) -> JobHandle:
        def job(handle: JobHandle) -> dict:
            data = self.brain.rebuild_profile(profile_id)
            self._say(LIBRARY_RUN, "style", f"Rebuilt profile ({data['reference_count']} reels, {data['readiness']['level']})", status=EventStatus.VERIFIED)
            return data

        return self.jobs.submit(LIBRARY_RUN, "style", "Rebuild style profile", job, lane="light")

    # ----------------------------------------------------------- projects
    def create_project(self, name: str, spec: EditSpec, *, footage: list[str], references: list[str] | None = None, profile_id: str | None = None) -> dict:
        spec = spec.model_copy(update={"footage_ids": footage, "profile_id": profile_id or spec.profile_id})
        return self.library.create_project(name, spec.model_dump(mode="json"), footage=footage, references=references or [], music_id=spec.music_id, profile_id=profile_id)

    def update_spec(self, project_id: str, spec: EditSpec) -> None:
        self.library.update_project(project_id, spec=spec.model_dump(mode="json"), footage=spec.footage_ids, music_id=spec.music_id, profile_id=spec.profile_id)

    def _new_run(self, project_id: str, spec: EditSpec, kind: str = "edit") -> RunState:
        manifest = RunManifest(
            project_id=project_id,
            autonomy=spec.autonomy,
            run_mode=spec.run_mode,
            watch_delay_s=self.settings.edit.watch_delay_s,
            code_version=__version__,
            schema_versions={"edg": "2.0.0", "edit_spec": spec.schema_version},
            model_versions={"director": self.settings.models.director_model, "whisper": self.settings.models.whisper_model, "embedding": self.settings.models.embedding_model, "segmentation": self.settings.models.segmentation_model},
            solver_versions={"cut": "cut-v3", "grade": "grade-v3", "music": "music-v2", "text": "text-v2", "planner": "planner-v3"},
            prompt_version="plan-v3",
            seed=7,
            artifact_root=str(self.settings.runs_dir),
            kind=kind,
        )
        manifest = self.artifacts.create_run(manifest)
        self.events.create_run(manifest)
        run_dir = Path(manifest.artifact_root)
        (run_dir / "edit_spec.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
        state = RunState(run_id=manifest.run_id, project_id=project_id, run_dir=run_dir, control=RunControl(spec.run_mode, self.settings.edit.watch_delay_s))
        with self._lock:
            self.runs[manifest.run_id] = state
        return state

    # --------------------------------------------------------------- plan
    def plan(self, project_id: str, *, use_vlm: bool = True, music_choice: str | None = None) -> tuple[RunState, JobHandle]:
        from vanta.learning.preferences import apply_preferences
        from vanta.reasoning.planner import plan_edit

        project = self.library.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        spec = EditSpec.model_validate(project["spec_json"])
        rules = [r["json"] | {"approval_state": r["approval_state"], "kind": r["kind"]} for r in self.library.list_rules(profile_id=spec.profile_id)]
        spec, notes = apply_preferences(spec, rules)
        project = {**project, "spec_json": spec.model_dump(mode="json")}
        state = self._new_run(project_id, spec)
        self.events.transition_run(state.run_id, RunStatus.RUNNING)
        for note in notes:
            self._say(state.run_id, "preferences", note, status=EventStatus.INFO)

        def job(handle: JobHandle) -> Any:
            progress = self._progress_for(handle, state.run_id)
            revision = len(self.library.list_edgs(project_id)) + 1
            parent = self.library.latest_edg(project_id)
            try:
                result = plan_edit(project, self.library, run_dir=state.run_dir, settings=self.settings, use_vlm=use_vlm, music_choice=music_choice, progress=progress, cancelled=lambda: handle.cancelled, revision=revision, parent_id=parent["id"] if parent else None)
            except Exception as exc:
                self.events.transition_run(state.run_id, RunStatus.FAILED)
                self._say(state.run_id, "plan", f"Planning failed: {exc}", status=EventStatus.FAILED, error=str(exc))
                raise
            state.plan = result
            state.edg = result.edg
            state.warnings = result.warnings
            self._persist_plan(state, result)
            for warning in result.warnings:
                self._say(state.run_id, "plan", warning, status=EventStatus.WARNING)
            for decision in result.decisions:
                self.library.add_decision({**decision, "run_id": state.run_id, "project_id": project_id, "detail": decision.get("detail")})
            self._say(state.run_id, "plan", f"Plan ready: {len(result.edg.shots)} shots, {len(result.edg.texts)} titles, {len(result.edg.transitions)} transitions. Review the steer board.", status=EventStatus.VERIFIED, progress=1.0, action="plan_ready")
            self.events.transition_run(state.run_id, RunStatus.PAUSED)
            return result

        return state, self.jobs.submit(state.run_id, "plan", "Plan the edit", job, lane="heavy", retries=0)

    def _persist_plan(self, state: RunState, result: Any) -> None:
        run_dir = state.run_dir
        (run_dir / "edg.json").write_text(result.edg.model_dump_json(indent=2), encoding="utf-8")
        (run_dir / "steer.json").write_text(json.dumps(result.steer, indent=2, default=str), encoding="utf-8")
        (run_dir / "style_snapshot.json").write_text(json.dumps(result.package.to_dict(), indent=1, default=str), encoding="utf-8")
        (run_dir / "planner_output.json").write_text(json.dumps({"assembly": result.assembly.data, "assembly_source": result.assembly.source, "grade_intents": result.grade_intents.data, "timings": result.timings, "warnings": result.warnings}, indent=2, default=str), encoding="utf-8")
        if result.graph:
            (run_dir / "footage_index.json").write_text(json.dumps({sid: {k: v for k, v in shot.items() if k in {"id", "media_id", "start_s", "end_s", "description", "thumb", "quality", "usable_ranges"}} for sid, shot in result.graph.shots.items()}, indent=1, default=str), encoding="utf-8")
        solver_dir = run_dir / "solver_outputs"
        solver_dir.mkdir(exist_ok=True)
        (solver_dir / "cut.json").write_text(json.dumps([{"slot": p.slot.__dict__, "shot": p.candidate.shot_id, "in": p.source_in_frame, "out": p.source_out_frame, "confidence": p.confidence, "terms": p.terms} for p in result.placements], indent=1, default=str), encoding="utf-8")
        (solver_dir / "grades.json").write_text(json.dumps({sid: {"params": s.params.to_dict(), "target": s.target, "predicted": s.predicted, "loss": s.loss} for sid, s in result.grades.items()}, indent=1, default=str), encoding="utf-8")
        self.library.save_edg(state.project_id, result.edg.revision, result.edg.model_dump(mode="json"), edg_id=result.edg.id, parent_id=result.edg.parent_id, note=f"planned in {state.run_id}")
        self.library.update_project(state.project_id, status="planned", data={"last_run": state.run_id})

    # ------------------------------------------------------------- mattes
    def generate_mattes(self, run_id: str) -> JobHandle:
        from vanta.analysis.segmentation import MattePrompt, auto_prompt, segment_subject

        state = self.runs[run_id]

        def job(handle: JobHandle) -> list[dict]:
            edg = state.edg
            assert edg is not None
            out = []
            graph = state.plan.graph if state.plan else None
            for i, matte in enumerate(edg.mattes):
                if handle.cancelled:
                    raise JobCancelled("cancelled")
                if matte.method != "pending":
                    continue
                shot = edg.shot(matte.shot_id)
                src = edg.source(shot.source_media_id)
                self._say(run_id, "masks", f"Segmenting the subject in {shot.id} (SAM 2.1, temporal smoothing, feathering)", status=EventStatus.RUNNING, progress=i / max(1, len(edg.mattes)))
                key_rgb = None
                if graph and shot.footage_shot_id in graph.shots and graph.shots[shot.footage_shot_id].get("thumb"):
                    import cv2

                    key_rgb = cv2.cvtColor(cv2.imread(graph.shots[shot.footage_shot_id]["thumb"]), cv2.COLOR_BGR2RGB)
                prompt = auto_prompt(key_rgb) if key_rgb is not None else MattePrompt()
                vlm_box = ((graph.shots.get(shot.footage_shot_id) or {}).get("vlm") or {}).get("subject_box") if graph else None
                if vlm_box and len(vlm_box) == 4:
                    prompt.box = tuple(float(v) for v in vlm_box)
                prompt.key_frame = (shot.source_out_frame - shot.source_in_frame) // 2
                result = segment_subject(src.path, shot.source_in_frame, shot.source_out_frame, Path(matte.directory), prompt)
                matte.method = result.method
                matte.confidence = result.confidence
                matte.review_required = result.review_required
                matte.frame_count = result.frame_count
                status = EventStatus.WARNING if result.review_required else EventStatus.VERIFIED
                self._say(run_id, "masks", f"Matte for {shot.id}: {result.method}, confidence {result.confidence:.2f}" + (" — needs your review" if result.review_required else ""), status=status, details=result.to_dict())
                out.append(result.to_dict())
            (state.run_dir / "edg.json").write_text(edg.model_dump_json(indent=2), encoding="utf-8")
            self.library.save_edg(state.project_id, edg.revision, edg.model_dump(mode="json"), edg_id=edg.id, parent_id=edg.parent_id, note="mattes generated")
            return out

        return self.jobs.submit(run_id, "masks", "Generate subject mattes", job, lane="heavy")

    # ------------------------------------------------------------ preview
    def render_preview(self, run_id: str, *, scale: float = 0.5) -> JobHandle:
        from vanta.render.preview import render_preview

        state = self.runs[run_id]

        def job(handle: JobHandle) -> str:
            assert state.edg is not None
            out = state.run_dir / "proxy" / f"preview_r{state.edg.revision}.mp4"
            self._say(run_id, "preview", "Rendering VANTA preview (approximate transitions; Resolve is the reference)", status=EventStatus.RUNNING)
            spec_data = state.edg.metadata.get("spec")
            lufs = EditSpec.model_validate(spec_data).loudness_target_lufs if spec_data else -14.0
            render_preview(
                state.edg,
                out,
                scale=scale,
                loudness_lufs=lufs,
                progress=lambda stage, f: self._job_progress(handle, f, f"preview {stage}"),
                cancelled=lambda: handle.cancelled,
            )
            state.preview = str(out)
            self._say(run_id, "preview", "Preview ready", status=EventStatus.VERIFIED, action="preview_ready", details={"path": str(out)})
            return str(out)

        return self.jobs.submit(run_id, "preview", "Render preview", job, lane="heavy")

    def _job_progress(self, handle: JobHandle, fraction: float, message: str) -> None:
        if handle.cancelled:
            raise JobCancelled("cancelled")
        handle.progress = fraction
        handle.message = message
        self._job_update(handle)

    # ------------------------------------------------------------ approval
    async def _approval(self, state: RunState, step: Any) -> bool:
        state.pending_approval = {"step_id": step.id, "description": step.description, "confidence": step.confidence, "because": step.because}
        state.approval_event.clear()
        self.emit(VantaEvent(run_id=state.run_id, stage=step.stage, action="approval_required", status=EventStatus.PLANNED, message=f"Approval needed: {step.description}", confidence=step.confidence, step_id=step.id, because=step.because))
        while not state.approval_event.is_set():
            if state.control.stopped:
                return False
            await asyncio.sleep(0.2)
        state.pending_approval = None
        return state.approval_answer

    def answer_approval(self, run_id: str, approved: bool) -> None:
        state = self.runs[run_id]
        state.approval_answer = approved
        state.approval_event.set()

    # ------------------------------------------------------------ execute
    def execute(self, run_id: str, *, mode: RunMode | None = None, autonomy: AutonomyMode | None = None, plan_approved: bool = True, render: bool = True) -> JobHandle:
        from vanta.resolve.adapter import default_adapter
        from vanta.resolve.edit_executor import EditExecutor
        from vanta.resolve.ledger import load_ledger

        state = self.runs[run_id]
        if mode:
            state.control.set_mode(mode)

        def job(handle: JobHandle) -> Any:
            assert state.edg is not None
            adapter = default_adapter(allow_writes=True)
            spec = EditSpec.model_validate(state.edg.metadata.get("spec"))
            executor = EditExecutor(
                adapter,
                load_ledger(),
                run_id=run_id,
                run_dir=state.run_dir,
                emit=self.emit,
                control=state.control,
                autonomy=autonomy or spec.autonomy,
                approval=lambda step: self._approval(state, step),
                plan_approved=plan_approved,
            )
            state.executor = executor
            try:
                self.events.transition_run(run_id, RunStatus.RUNNING)
            except ValueError:
                pass
            try:
                report = asyncio.run(executor.execute(state.edg, include_render=render, progress=lambda stage, f: self._job_progress(handle, f, stage)))
            except Exception as exc:
                self._safe_transition(run_id, RunStatus.FAILED)
                self._say(run_id, "execute", f"Resolve execution failed: {exc}", status=EventStatus.FAILED, error=str(exc), action="execution_failed")
                raise
            finally:
                adapter.close()
            state.report = report
            summary = f"Resolve timeline {report.timeline_name}: {report.verified} verified, {report.skipped} skipped, {len(report.fallbacks)} fallbacks"
            if report.failed:
                failed = [f for f in report.fallbacks if ": FAILED" in f]
                self._say(run_id, "execute", f"{summary}; {report.failed} layer(s) FAILED and were left out: " + "; ".join(failed)[:600], status=EventStatus.WARNING, action="execution_done", details=report.to_dict())
            else:
                self._say(run_id, "execute", summary, status=EventStatus.VERIFIED, action="execution_done", details=report.to_dict())
            self._safe_transition(run_id, RunStatus.PAUSED)
            return report

        return self.jobs.submit(run_id, "execute", "Build the edit in DaVinci Resolve", job, lane="heavy", retries=0)

    def _safe_transition(self, run_id: str, status: RunStatus) -> None:
        try:
            self.events.transition_run(run_id, status)
        except (ValueError, KeyError):
            pass

    # --------------------------------------------------- QC / critic / repair
    def qc_and_repair(self, run_id: str, *, use_vlm: bool = True, max_iterations: int = 3) -> JobHandle:
        from vanta.qc.levels import run_qc
        from vanta.reasoning.critic import critique, next_best_footage, plan_repairs
        from vanta.reasoning.llm import get_provider
        from vanta.reasoning.patches import PatchContext, apply_patches
        from vanta.render.preview import render_preview

        state = self.runs[run_id]

        def job(handle: JobHandle) -> list[dict]:
            assert state.edg is not None
            spec = EditSpec.model_validate(state.edg.metadata.get("spec"))
            provider = get_provider(self.settings) if use_vlm else None
            history = []
            render_kind = "resolve_proxy" if state.report and state.report.render_verified else "preview"
            render = Path(state.report.render_path) if render_kind == "resolve_proxy" else (Path(state.preview) if state.preview else None)
            for iteration in range(max_iterations + 1):
                if render is None or not render.is_file():
                    out = state.run_dir / "proxy" / f"preview_r{state.edg.revision}.mp4"
                    render_preview(state.edg, out, scale=0.5, cancelled=lambda: handle.cancelled)
                    render, render_kind = out, "preview"
                    state.preview = str(out)
                self._say(run_id, "qc", f"QC iteration {iteration}: L0-L4 on the {render_kind.replace('_', ' ')}", status=EventStatus.RUNNING, progress=iteration / (max_iterations + 1))
                from vanta.solvers.text import scan_fonts

                qc = run_qc(state.edg, render, run_id=run_id, iteration=iteration, expected_duration_s=spec.duration_s, platform=spec.platform, target_lufs=spec.loudness_target_lufs, fonts={f["name"] for f in scan_fonts()}, render_kind=render_kind)
                if provider is not None:
                    outcome = critique(state.edg, render, qc, provider)
                    qc.findings.extend(outcome.findings)
                    qc.metrics["critic"] = {"source": outcome.source, "report": outcome.report, "error": outcome.error}
                payload = qc.model_dump(mode="json")
                self.library.add_qc(run_id, iteration, payload)
                (state.run_dir / "qc" / f"qc_{iteration}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
                history.append(payload)
                state.qc = history
                for finding in qc.findings:
                    self._say(run_id, f"qc.L{finding.level}", finding.description, status=EventStatus.FAILED if finding.blocking else EventStatus.WARNING, details=finding.model_dump(mode="json"))
                if not qc.findings or iteration == max_iterations:
                    break
                plan = plan_repairs(state.edg, qc.findings, graph=state.plan.graph if state.plan else None, frames_for_text=lambda layer: self._frames_for_text(state, layer), platform=spec.platform)
                for patch in plan.patches:
                    if patch.changes.get("footage_shot_id") == "__next_best__" and state.plan and state.plan.graph:
                        replacement = next_best_footage(state.plan.graph, state.edg, patch.target_id)
                        patch.changes["footage_shot_id"] = replacement
                plan.patches = [p for p in plan.patches if p.changes.get("footage_shot_id", "x")]
                if not plan.patches:
                    self._say(run_id, "repair", f"{len(plan.unresolved)} issue(s) need you; nothing safe to auto-repair", status=EventStatus.WARNING)
                    break
                new_edg, report = apply_patches(state.edg, plan.patches, PatchContext(lut_dir=state.run_dir / "luts", graph=state.plan.graph if state.plan else None))
                for patch in plan.patches:
                    self.library.save_patch(state.project_id, patch.model_dump(mode="json"), edg_id=new_edg.id, status="applied")
                self._say(run_id, "repair", f"Applied {len(plan.patches)} scoped patch(es): " + "; ".join(p.reason for p in plan.patches)[:400], status=EventStatus.VERIFIED, details={"changed": report})
                state.edg = new_edg
                self.library.save_edg(state.project_id, new_edg.revision, new_edg.model_dump(mode="json"), edg_id=new_edg.id, parent_id=new_edg.parent_id, note=f"repair iteration {iteration + 1}")
                (state.run_dir / f"edg_r{new_edg.revision}.json").write_text(new_edg.model_dump_json(indent=2), encoding="utf-8")
                render = None  # re-render after patches
            final = history[-1]
            status = "final_candidate" if not final["blocking"] else "needs_review"
            self._say(run_id, "qc", f"QC complete: {status.replace('_', ' ')} ({len(final['findings'])} finding(s))", status=EventStatus.VERIFIED if status == "final_candidate" else EventStatus.WARNING, action=status, progress=1.0)
            return history

        return self.jobs.submit(run_id, "qc", "QC, critique and repair", job, lane="heavy", retries=0)

    def _frames_for_text(self, state: RunState, layer: Any) -> list:
        if not state.preview or not Path(state.preview).is_file():
            return []
        from vanta.render.preview import contact_frames

        edg = state.edg
        times = [(layer.start_frame + (layer.end_frame - layer.start_frame) * f) / edg.fps for f in (0.2, 0.5, 0.8)]
        return contact_frames(Path(state.preview), times)

    # ---------------------------------------------------------- chat
    def chat(self, run_id: str, text: str, *, use_vlm: bool = True) -> Any:
        from vanta.reasoning.llm import get_provider
        from vanta.reasoning.nl import interpret

        state = self.runs[run_id]
        assert state.edg is not None
        project = self.library.get_project(state.project_id) or {}
        references = list(project.get("references_json") or []) or [r["id"] for r in self.library.list_media("reference")]
        return interpret(text, state.edg, get_provider(self.settings) if use_vlm else None, references=references)

    def apply_chat(self, run_id: str, result: Any) -> dict:
        """Apply a confirmed NL result: EDG patches now; spec changes on the next plan."""
        from vanta.reasoning.patches import PatchContext, apply_patches

        state = self.runs[run_id]
        out: dict[str, Any] = {}
        if result.patches:
            new_edg, report = apply_patches(state.edg, result.patches, PatchContext(lut_dir=state.run_dir / "luts", graph=state.plan.graph if state.plan else None))
            state.edg = new_edg
            for patch in result.patches:
                self.library.save_patch(state.project_id, patch.model_dump(mode="json"), edg_id=new_edg.id, status="applied")
            self.library.save_edg(state.project_id, new_edg.revision, new_edg.model_dump(mode="json"), edg_id=new_edg.id, parent_id=new_edg.parent_id, note=result.understood)
            (state.run_dir / f"edg_r{new_edg.revision}.json").write_text(new_edg.model_dump_json(indent=2), encoding="utf-8")
            self._say(run_id, "patch", f"Patched: {result.understood}", status=EventStatus.VERIFIED, details={"changed": report})
            out["changed"] = report
        if result.spec_changes:
            project = self.library.get_project(state.project_id)
            spec = EditSpec.model_validate(project["spec_json"])
            data = spec.model_dump()
            for key, value in result.spec_changes.items():
                if key == "sliders":
                    for name, v in value.items():
                        current = data["sliders"][name]
                        data["sliders"][name] = max(0.0, min(1.0, current + float(v))) if isinstance(v, str) and v[:1] in "+-" else float(v)
                elif key == "never_add":
                    data["never"] = sorted(set(data["never"]) | set(value))
                elif key == "reference_weights":
                    data["reference_weights"] = {**data["reference_weights"], **value}
                elif key in data:
                    data[key] = value
            self.update_spec(state.project_id, EditSpec.model_validate(data))
            self._say(run_id, "spec", f"Brief updated: {result.understood} — re-plan to apply", status=EventStatus.INFO)
            out["spec"] = result.spec_changes
        return out

    def text_edit(self, project_id: str, media_id: str, dropped: set[int], *, target_duration_s: float | None = None) -> RunState:
        """Build a text-based edit run from transcript choices (fast, synchronous)."""
        from vanta.reasoning.text_planner import plan_text_edit

        project = self.library.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        spec = EditSpec.model_validate(project["spec_json"])
        state = self._new_run(project_id, spec, kind="text_edit")
        revision = len(self.library.list_edgs(project_id)) + 1
        edg = plan_text_edit(project, self.library, media_id, dropped, run_dir=state.run_dir, settings=self.settings, target_duration_s=target_duration_s, revision=revision)
        state.edg = edg
        (state.run_dir / "edg.json").write_text(edg.model_dump_json(indent=2), encoding="utf-8")
        self.library.save_edg(project_id, edg.revision, edg.model_dump(mode="json"), edg_id=edg.id, parent_id=None, note="text-based edit")
        self.library.update_project(project_id, data={"last_run": state.run_id})
        self.events.transition_run(state.run_id, RunStatus.RUNNING)
        self._say(state.run_id, "plan", f"Text-based edit: {len(edg.shots)} kept ranges, {edg.duration_frames / edg.fps:.1f}s, {len(edg.captions)} caption chunks", status=EventStatus.VERIFIED, action="plan_ready", progress=1.0)
        self.events.transition_run(state.run_id, RunStatus.PAUSED)
        return state

    def replan_range(self, run_id: str, frame_range: list[int], pacing_delta: float) -> JobHandle:
        """Re-cut one range only ("cut faster during the drop"); everything else is kept."""
        from vanta.reasoning.replan import recut_range

        state = self.runs[run_id]

        def job(handle: JobHandle) -> str:
            if state.plan is None:
                raise RuntimeError("re-plan the edit first: range re-cuts need this run's planning context")
            spec = EditSpec.model_validate(state.edg.metadata.get("spec"))
            new = recut_range(state.edg, state.plan, int(frame_range[0]), int(frame_range[1]), pacing=min(1.0, max(0.0, spec.sliders.pacing + pacing_delta)), beat_sync=spec.sliders.beat_sync, variety=spec.sliders.shot_variety)
            state.edg = new
            self.library.save_edg(state.project_id, new.revision, new.model_dump(mode="json"), edg_id=new.id, parent_id=new.parent_id, note=f"range re-cut {frame_range}")
            (state.run_dir / f"edg_r{new.revision}.json").write_text(new.model_dump_json(indent=2), encoding="utf-8")
            self._say(run_id, "patch", f"Re-cut frames {frame_range[0]}-{frame_range[1]} ({'faster' if pacing_delta > 0 else 'slower'}); other shots untouched", status=EventStatus.VERIFIED)
            return new.id

        return self.jobs.submit(run_id, "patch", "Re-cut a range", job, lane="heavy", retries=0)

    def undo_dimension(self, run_id: str, dimension: str) -> str:
        """Return to the EDG revision before the most recent change (of ``dimension`` when recorded)."""
        state = self.runs[run_id]
        edg = state.edg
        if edg is None or not edg.parent_id:
            raise RuntimeError("nothing to undo in this run")
        patches = {p["id"]: p for p in self.library.list_patches(state.project_id)}
        target = edg
        while target.parent_id:
            mine = [patches.get(pid) for pid in target.patch_ids[-3:]]
            if dimension == "last" or any(p and p["json"].get("dimension") in {dimension, {"mask": "masking"}.get(dimension)} for p in mine):
                break
            parent = self.library.get_edg(target.parent_id)
            if not parent:
                break
            target = EditDecisionGraph.model_validate(parent["json"])
        parent = self.library.get_edg(target.parent_id) if target.parent_id else None
        if not parent:
            raise RuntimeError("no earlier revision to return to")
        restored = EditDecisionGraph.model_validate(parent["json"])
        state.edg = restored
        self._say(run_id, "undo", f"Restored revision {restored.revision} (undo {dimension})", status=EventStatus.ROLLED_BACK)
        return restored.id

    def final_render(self, run_id: str, *, codec: str = "H264") -> JobHandle:
        """Full-quality Resolve render — only after QC passes and music rights are confirmed."""
        from vanta.analysis.media import probe
        from vanta.resolve.adapter import default_adapter

        state = self.runs[run_id]
        if not state.report or not state.report.timeline_id:
            raise RuntimeError("build the edit in Resolve first")
        if state.edg and state.edg.music and not state.edg.music.licence_confirmed and not state.edg.music.guide_only:
            raise RuntimeError("confirm the music licence in Library › Music before the final render")
        history = state.qc or [r["json"] for r in self.library.list_qc(run_id)]
        if history and history[-1].get("blocking"):
            raise RuntimeError("QC still has blocking findings; resolve them before the final render")

        def job(handle: JobHandle) -> str:
            adapter = default_adapter(allow_writes=True)
            edg = state.edg
            target = state.run_dir / "final"
            name = edg.timeline_name.replace("_ASSEMBLY_", "_FINAL_CANDIDATE_")
            try:
                started = adapter.call("render_start", timeline_id=state.report.timeline_id, target_dir=str(target), custom_name=name, width=edg.width, height=edg.height, quality="Best", format="mp4", codec=codec)
                job_id = started["job_id"]
                while True:
                    time.sleep(2)
                    if handle.cancelled:
                        adapter.call("render_stop")
                        raise JobCancelled("cancelled")
                    status = adapter.call("render_status", job_id=job_id).get("status") or {}
                    pct = int(status.get("CompletionPercentage") or 0)
                    self._job_progress(handle, pct / 100, f"final render {pct}%")
                    if status.get("JobStatus") == "Complete":
                        break
                    if status.get("JobStatus") in {"Failed", "Cancelled"}:
                        raise RuntimeError(f"final render {status.get('JobStatus')}: {status.get('Error')}")
            finally:
                adapter.close()
            files = sorted(target.glob(f"{name}*"), key=lambda p: p.stat().st_mtime)
            if not files:
                raise RuntimeError("render finished but the file is missing")
            info = probe(files[-1])
            expected = edg.duration_frames / edg.fps
            if abs(info.duration_s - expected) > 2 / edg.fps + 0.05 or info.display_size != (edg.width, edg.height):
                raise RuntimeError(f"final render verification failed: {info.display_size} {info.duration_s:.2f}s")
            self._say(run_id, "render", f"Final candidate rendered and verified: {files[-1].name}", status=EventStatus.VERIFIED, action="final_render", details={"path": str(files[-1])})
            self.library.update_project(state.project_id, status="final_candidate")
            return str(files[-1])

        return self.jobs.submit(run_id, "render", "Final render", job, lane="heavy", retries=0)

    # ---------------------------------------------------------- learning
    def feedback(self, run_id: str, kind: str, *, target: str | None = None, dimension: str | None = None, note: str = "") -> None:
        from vanta.learning.corrections import from_feedback
        from vanta.learning.preferences import mine_preferences

        state = self.runs.get(run_id)
        correction = from_feedback(kind, target=target, dimension_hint=dimension, note=note)
        self.library.add_feedback(kind=kind, target=target, project_id=state.project_id if state else None, run_id=run_id, note=note)
        self.library.add_correction({**correction.to_dict(), "project_id": state.project_id if state else None, "run_id": run_id})
        if target:
            for decision in self.library.list_decisions(run_id=run_id):
                if decision["json"].get("target") == target and decision["accepted"] is None:
                    self.library.resolve_decision(decision["id"], kind == "thumbs_up")
        proposed = mine_preferences(self.library, profile_id=(EditSpec.model_validate(state.edg.metadata["spec"]).profile_id if state and state.edg else None))
        self._say(run_id, "learning", f"Feedback recorded ({kind})" + (f"; {len(proposed)} preference rule(s) in the review queue" if proposed else ""), status=EventStatus.INFO)

    def capture_corrections(self, run_id: str) -> JobHandle:
        """Read the user's finished timeline in Resolve and learn from the differences."""
        from vanta.learning.corrections import correction_rate, diff_timeline
        from vanta.learning.preferences import mine_preferences
        from vanta.resolve.adapter import default_adapter

        state = self.runs[run_id]

        def job(handle: JobHandle) -> dict:
            if not state.report or not state.report.timeline_id:
                raise RuntimeError("this run has no Resolve timeline to compare")
            adapter = default_adapter(allow_writes=True)
            try:
                final = adapter.call("timeline_snapshot", timeline_id=state.report.timeline_id)
                exported = {}
                for grade in state.edg.grades:
                    item_id = state.report.item_ids.get(grade.shot_id)
                    if not item_id:
                        continue
                    target = state.run_dir / "verify" / f"{grade.shot_id}_final.cube"
                    try:
                        adapter.call("export_lut", timeline_id=state.report.timeline_id, item_id=item_id, export_path=str(target), size=33)
                        exported[grade.shot_id] = str(target)
                    except Exception:
                        continue
            finally:
                adapter.close()
            import cv2

            frames = {}
            for grade in state.edg.grades:
                thumb = state.run_dir / "thumbs" / f"{grade.shot_id}_before.jpg"
                if thumb.is_file():
                    frames[grade.shot_id] = [cv2.cvtColor(cv2.imread(str(thumb)), cv2.COLOR_BGR2RGB)]
            corrections = diff_timeline(state.edg, state.report.item_ids, final, exported_luts=exported, reference_frames=frames)
            for c in corrections:
                self.library.add_correction({**c.to_dict(), "project_id": state.project_id, "run_id": run_id})
                self._say(run_id, "learning", f"Learned: {c.description}", status=EventStatus.INFO, details=c.to_dict())
            accepted_targets = {c.target for c in corrections}
            for decision in self.library.list_decisions(run_id=run_id):
                if decision["accepted"] is None:
                    self.library.resolve_decision(decision["id"], decision["json"].get("target") not in accepted_targets)
            proposed = mine_preferences(self.library)
            rate = correction_rate(state.edg, corrections)
            (state.run_dir / "corrections.json").write_text(json.dumps({"corrections": [c.to_dict() for c in corrections], "rate": rate}, indent=2, default=str), encoding="utf-8")
            self._say(run_id, "learning", f"{len(corrections)} correction(s) captured; {len(proposed)} candidate preference rule(s) for review", status=EventStatus.VERIFIED, details={"correction_rate": rate})
            return {"corrections": len(corrections), "rate": rate, "proposed": [p.id for p in proposed]}

        return self.jobs.submit(run_id, "learning", "Learn from your final timeline", job, lane="heavy", retries=0)

    # ---------------------------------------------------------- Resolve
    def reanalyse_outdated(self, kinds: tuple[str, ...] = ("reference", "footage")) -> int:
        """Queue re-analysis for every picture analysis made by an older pipeline version."""
        from vanta.analysis.pipeline import is_outdated

        count = 0
        for kind in kinds:
            for media in self.library.list_media(kind):
                if is_outdated(media):
                    self.library.update_media(media["id"], analysis_status="pending", analysis_path=None)
                    self.analyse(media["id"])
                    count += 1
        return count

    def preflight(self, project_id: str) -> dict:
        """Run-time estimate and readiness checks before hours are spent (see workers.preflight)."""
        from vanta.reasoning.llm import get_provider
        from vanta.resolve.ledger import OPERATION_GATES, load_ledger
        from vanta.workers.preflight import run_preflight

        project = self.library.get_project(project_id)
        if project is None:
            raise ValueError(f"unknown project {project_id}")
        spec = EditSpec.model_validate(project["spec_json"])
        footage = spec.footage_ids or list(project.get("footage_json") or [])
        ledger = load_ledger()
        gates = {op: ledger.allows(op)[0] for op in OPERATION_GATES}
        try:
            model = get_provider(self.settings).available()
        except Exception as exc:  # a broken model host must not break the brief page
            model = (False, str(exc))
        result = run_preflight(self.library, spec, footage, profile_id=spec.profile_id or project.get("profile_id"), settings=self.settings, model_ready=model, gates_ok=gates)
        return result.to_dict()

    def resolve_status(self) -> dict:
        from vanta.resolve.adapter import ResolveScriptingAdapter

        adapter = ResolveScriptingAdapter(self.settings.resolve_root, persistent=False, timeout_s=8)
        return adapter.capabilities()

    def capture_drx_template(self) -> dict:
        """Capture the user's 8-node grade (TECH..POLISH) from the clip under Resolve's playhead."""
        import shutil

        from vanta.resolve.adapter import ResolveScriptingAdapter
        from vanta.resolve.ledger import load_ledger
        from vanta.solvers.grade import NODE_LABELS

        folder = self.settings.data_dir / "templates" / "grade"
        adapter = ResolveScriptingAdapter(self.settings.resolve_root, allow_writes=True, output_root=self.settings.data_dir, timeout_s=45)
        try:
            result = adapter.call("capture_drx_template", export_dir=str(folder / "capture"), prefix="VANTA_8NODE")
        finally:
            adapter.close()
        labels = [str(x or "").strip().upper() for x in result.get("labels", [])]
        if labels[: len(NODE_LABELS)] != list(NODE_LABELS) or not result.get("drx_path"):
            raise ValueError(f"the clip's nodes are {labels or 'unlabelled'}; build 8 serial nodes labelled {', '.join(NODE_LABELS)} and capture again")
        target = folder / "vanta_8node.drx"
        shutil.copyfile(result["drx_path"], target)
        ledger = load_ledger()
        ledger.calibration["drx_template"] = {"path": str(target), "labels": labels[: len(NODE_LABELS)], "captured_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        ledger.save()
        return {"path": str(target), "labels": labels}

    def gate_check(self, include: set[str] | None = None) -> JobHandle:
        from vanta.resolve.gate_runner import run_gates

        def job(handle: JobHandle) -> Any:
            def say(gate: str, message: str) -> None:
                handle.message = f"{gate}: {message}"
                self._job_update(handle)
                self._say(LIBRARY_RUN, f"gates.{gate}", message, status=EventStatus.RUNNING)

            run = run_gates(progress=say, include=include)
            for result in run.results:
                status = {"PASS": EventStatus.VERIFIED, "PARTIAL": EventStatus.WARNING, "FAIL": EventStatus.FAILED}.get(result.status, EventStatus.INFO)
                self._say(LIBRARY_RUN, f"gates.{result.gate}", f"{result.gate} {result.status}: {result.summary}", status=status, details=result.evidence)
            return run

        return self.jobs.submit(LIBRARY_RUN, "gates", "Resolve compatibility check", job, lane="heavy", retries=0)

    # ---------------------------------------------------------- controls
    def control(self, run_id: str, action: str) -> dict:
        state = self.runs[run_id]
        control = state.control
        if action == "pause":
            control.pause()
        elif action == "resume":
            control.resume()
        elif action == "step":
            control.next_step()
        elif action == "skip":
            control.skip()
        elif action == "stop":
            control.stop()
            for handle in self.jobs.active():
                if handle.job.run_id == run_id:
                    handle.cancel()
        elif action == "undo":
            if state.executor is None:
                raise RuntimeError("nothing has been executed in Resolve for this run")
            return state.executor.undo_last()
        else:
            raise ValueError(f"unknown control {action}")
        self._say(run_id, "control", f"{action.title()} requested", status=EventStatus.INFO, details=control.snapshot())
        return control.snapshot()

    def load_run(self, run_id: str) -> RunState:
        """Rehydrate a past run (EDG + artifacts) for review, patches or execution."""
        if run_id in self.runs:
            return self.runs[run_id]
        row = self.events.get_run(run_id)
        if not row:
            raise KeyError(run_id)
        manifest = RunManifest.model_validate_json(row["manifest_json"])
        run_dir = Path(manifest.artifact_root)
        state = RunState(run_id=run_id, project_id=manifest.project_id, run_dir=run_dir, control=RunControl(manifest.run_mode, manifest.watch_delay_s))
        revisions = sorted(run_dir.glob("edg_r*.json"), key=lambda p: int(p.stem.split("_r")[-1]))
        edg_path = revisions[-1] if revisions else run_dir / "edg.json"
        if edg_path.is_file():
            state.edg = EditDecisionGraph.model_validate_json(edg_path.read_text(encoding="utf-8"))
        previews = sorted((run_dir / "proxy").glob("preview_r*.mp4"))
        state.preview = str(previews[-1]) if previews else None
        report = run_dir / "execution_report.json"
        if report.is_file():
            from vanta.resolve.edit_executor import ExecutionReport

            state.report = ExecutionReport(**json.loads(report.read_text(encoding="utf-8")))
        self.runs[run_id] = state
        return state

    def steer(self, run_id: str) -> dict:
        state = self.runs[run_id]
        path = state.run_dir / "steer.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def shutdown(self) -> None:
        for state in self.runs.values():
            state.control.stop()
        self.jobs.shutdown()


_service: VantaService | None = None


def get_service() -> VantaService:
    global _service
    if _service is None:
        _service = VantaService()
    return _service


def media_analysis(media_id: str, service: VantaService | None = None) -> dict | None:
    service = service or get_service()
    media = service.library.get_media(media_id)
    return load_analysis(media, service.settings) if media else None


__all__ = ["VantaService", "RunState", "get_service", "media_analysis", "LIBRARY_RUN"]
