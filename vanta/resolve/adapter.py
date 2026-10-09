"""The single Resolve boundary (manual 16.1). No operation is reported successful without read-back.

``ResolveScriptingAdapter`` keeps one long-lived broker process running inside
Resolve's own Python (version-matched scripting module) and speaks the closed
protocol over JSON lines with per-request timeouts. If the broker dies or
Resolve restarts, the next request starts a fresh broker. Writes are only
possible when the adapter is constructed with ``allow_writes=True`` — the
executor does that only for operations whose capability gate permits them.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ResolveUnavailable(RuntimeError):
    """Raised when a live Resolve operation is requested without a verified bridge."""


class VerificationFailed(RuntimeError):
    """Raised when a Resolve write cannot be verified from observed state."""


@dataclass(frozen=True)
class VerifiedResult:
    operation: str
    write_result: Any
    observed: Any
    verified: bool


class ResolveAdapter(ABC):
    """High-level adapter. Implementations keep every vendor call inside this boundary."""

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Return live, gate-backed capabilities."""

    @abstractmethod
    def read_state(self, target: dict[str, Any]) -> dict[str, Any]:
        """Read project/timeline/media state for verification."""

    @abstractmethod
    def open_page(self, page: str) -> dict[str, Any]:
        """Switch the Resolve page (follow-along)."""

    @abstractmethod
    def create_timeline(self, name: str, *, snapshot_id: str, fps: str | None = None, width: int | None = None, height: int | None = None) -> dict[str, Any]:
        """Create a new timeline only; source timelines are never overwritten."""

    @abstractmethod
    def append_clip(self, timeline_id: str, source_path: str, source_in: int, source_out: int, timeline_start: int, track: int, media_type: int | None = None) -> dict[str, Any]:
        """Append one path-resolved media-pool item with exact source/record bounds."""

    @abstractmethod
    def add_marker(self, timeline_id: str, frame: int, color: str, name: str, note: str, custom_data: str) -> dict[str, Any]:
        """Create a breadcrumb marker for an operation."""

    @abstractmethod
    def render_proxy(self, timeline_id: str, output_path: str) -> dict[str, Any]:
        """Queue a proxy render; caller must poll and verify its result."""

    @abstractmethod
    def snapshot(self, label: str, *, export_path: str) -> dict[str, Any]:
        """Export backup evidence and record immutable baseline timeline identity."""

    @abstractmethod
    def rollback(self, snapshot_id: str) -> dict[str, Any]:
        """Remove exactly the VANTA-created timelines of a snapshot and restore selection."""

    # Extended operations: concrete adapters override; fakes may leave them unavailable.
    def call(self, action: str, **params: Any) -> dict[str, Any]:
        raise ResolveUnavailable(f"{type(self).__name__} does not implement {action}")


class UnavailableResolveAdapter(ResolveAdapter):
    """Honest default until a supported live bridge is connected."""

    def __init__(self, reason: str = "No verified live Resolve adapter is configured"):
        self.reason = reason

    def _unavailable(self) -> None:
        raise ResolveUnavailable(self.reason)

    def capabilities(self) -> dict[str, Any]:
        return {"connected": False, "verified": False, "reason": self.reason}

    def read_state(self, target: dict[str, Any]) -> dict[str, Any]:
        self._unavailable()

    def open_page(self, page: str) -> dict[str, Any]:
        self._unavailable()

    def create_timeline(self, name: str, *, snapshot_id: str, fps: str | None = None, width: int | None = None, height: int | None = None) -> dict[str, Any]:
        self._unavailable()

    def append_clip(self, timeline_id: str, source_path: str, source_in: int, source_out: int, timeline_start: int, track: int, media_type: int | None = None) -> dict[str, Any]:
        self._unavailable()

    def add_marker(self, timeline_id: str, frame: int, color: str, name: str, note: str, custom_data: str) -> dict[str, Any]:
        self._unavailable()

    def render_proxy(self, timeline_id: str, output_path: str) -> dict[str, Any]:
        self._unavailable()

    def snapshot(self, label: str, *, export_path: str) -> dict[str, Any]:
        self._unavailable()

    def rollback(self, snapshot_id: str) -> dict[str, Any]:
        self._unavailable()


class _Broker:
    """Long-lived ResolvePython subprocess speaking JSON lines."""

    def __init__(self, args: list[str], env: dict[str, str], cwd: str):
        self.process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=cwd,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.stderr_tail: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump(self) -> None:
        assert self.process.stdout
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _pump_err(self) -> None:
        assert self.process.stderr
        for line in self.process.stderr:
            self.stderr_tail = (self.stderr_tail + [line.rstrip()])[-20:]

    def alive(self) -> bool:
        return self.process.poll() is None

    def request(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        assert self.process.stdin
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        while True:
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty as exc:
                self.close()
                raise ResolveUnavailable(f"Resolve did not answer {payload['action']} within {timeout:g}s (modal dialog or stalled call?)") from exc
            if line is None:
                raise ResolveUnavailable("Resolve broker exited: " + " | ".join(self.stderr_tail[-4:]))
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue  # vendor module chatter on stdout
            if response.get("id") == payload["id"]:
                return response

    def close(self) -> None:
        try:
            self.process.kill()
        except Exception:
            pass


class ResolveScriptingAdapter(UnavailableResolveAdapter):
    """Closed-protocol adapter backed by Resolve's version-matched Python runtime."""

    SLOW_ACTIONS = {"snapshot": 180.0, "import_media": 300.0, "append_clips": 120.0, "add_clip_mattes": 300.0, "render_start": 60.0, "export_lut": 60.0, "import_fusion_comp": 60.0, "create_project": 120.0, "load_project": 120.0, "export_still": 60.0, "timeline_snapshot": 60.0}

    def __init__(
        self,
        resolve_root: str | Path,
        *,
        timeout_s: float = 20.0,
        allow_disposable_gate_mutations: bool = False,
        allow_writes: bool = False,
        output_root: str | Path | None = None,
        persistent: bool = True,
    ):
        super().__init__("Resolve scripting connection unavailable")
        self.resolve_root = Path(resolve_root)
        self.python = self.resolve_root / "ResolvePython" / "ResolvePython.exe"
        self.module_path = self.resolve_root / "ResolvePython" / "lib" / "modules"
        self.library = self.resolve_root / "fusionscript.dll"
        self.worker = Path(__file__).with_name("broker_worker.py")
        self.timeout_s = timeout_s
        self.allow_writes = allow_writes or allow_disposable_gate_mutations
        self.allow_disposable_gate_mutations = self.allow_writes
        self.output_root = str(Path(output_root).resolve()) if output_root else None
        self.persistent = persistent
        self._broker: _Broker | None = None
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._created_timeline_ids: dict[str, list[str]] = {}
        self._rolled_back: set[str] = set()

    # ------------------------------------------------------------ transport
    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["RESOLVE_SCRIPT_LIB"] = str(self.library)
        env["VANTA_RESOLVE_MODULE_PATH"] = str(self.module_path)
        env["VANTA_BROKER_ROOT"] = str(Path(__file__).resolve().parents[2])
        env["PYTHONIOENCODING"] = "utf-8"
        if self.allow_writes:
            env["VANTA_RESOLVE_WRITES"] = "1"
        else:
            env.pop("VANTA_RESOLVE_WRITES", None)
            env.pop("VANTA_RESOLVE_GATE_MUTATIONS", None)
        if self.output_root:
            env["VANTA_OUTPUT_ROOT"] = self.output_root
        return env

    def installed(self) -> bool:
        return all(path.is_file() for path in (self.python, self.library, self.worker)) and self.module_path.is_dir()

    def _request(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        from vanta.resolve.protocol import (
            ALL_ACTIONS,
            MUTATION_ACTIONS,
            ProtocolError,
            validate_request,
        )

        if action not in ALL_ACTIONS:
            raise ResolveUnavailable(f"Action is not in the VANTA Resolve adapter protocol: {action}")
        if action in MUTATION_ACTIONS and not self.allow_writes:
            raise ResolveUnavailable("Resolve writes are disabled for this adapter (no gate/approval granted)")
        try:
            validate_request({"action": action, "params": params or {}}, allow_mutations=self.allow_writes, output_root=self.output_root)
        except ProtocolError as exc:
            raise ResolveUnavailable(f"protocol rejected {action}: {exc}") from exc
        if not self.installed():
            raise ResolveUnavailable("Resolve's embedded Python runtime or scripting module is missing")
        timeout = self.SLOW_ACTIONS.get(action, self.timeout_s)
        payload = {"id": next(self._ids), "action": action, "params": params or {}}
        if self.persistent:
            with self._lock:
                if self._broker is None or not self._broker.alive():
                    self._broker = _Broker([str(self.python), "-u", str(self.worker)], self._env(), str(self.resolve_root))
                response = self._broker.request(payload, timeout)
        else:
            response = self._oneshot(payload, timeout)
        if not response.get("ok"):
            raise ResolveUnavailable(response.get("error", "Resolve scripting operation failed"))
        return response.get("result", {})

    def _oneshot(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [str(self.python), "-u", str(self.worker)],
                input=json.dumps(payload, ensure_ascii=False) + "\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                check=False,
                env=self._env(),
                cwd=str(self.resolve_root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise ResolveUnavailable(f"Resolve scripting request timed out after {timeout:g}s") from exc
        except OSError as exc:
            raise ResolveUnavailable(f"Could not start Resolve's embedded Python: {exc}") from exc
        if result.returncode != 0:
            raise ResolveUnavailable(f"Resolve scripting broker failed: {result.stderr.strip()[-600:] or result.returncode}")
        for line in reversed(result.stdout.strip().splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        raise ResolveUnavailable("Resolve scripting broker returned invalid JSON")

    def close(self) -> None:
        with self._lock:
            if self._broker:
                self._broker.close()
                self._broker = None

    def call(self, action: str, **params: Any) -> dict[str, Any]:
        result = self._request(action, params)
        if isinstance(result, dict) and result.get("connected") is False:
            raise ResolveUnavailable(result.get("reason", "Resolve is not connected"))
        return result

    # --------------------------------------------------------------- reads
    def capabilities(self) -> dict[str, Any]:
        try:
            return {"verified": False, **self._request("status")}
        except ResolveUnavailable as exc:
            return {"connected": False, "verified": False, "reason": str(exc)}

    def status(self) -> dict[str, Any]:
        return self.capabilities()

    def read_state(self, target: dict[str, Any]) -> dict[str, Any]:
        if target.get("scope") == "snapshot":
            snapshot_id = target.get("snapshot_id")
            return {"restored_snapshot_id": snapshot_id if snapshot_id in self._rolled_back else None}
        state = self._request("read_state", target)
        if state.get("connected") is False:
            raise ResolveUnavailable(state.get("reason", "Resolve is not connected"))
        return state

    def open_page(self, page: str) -> dict[str, Any]:
        return self.call("open_page", page=page)

    # -------------------------------------------------------------- writes
    def snapshot(self, label: str, *, export_path: str) -> dict[str, Any]:
        result = self.call("snapshot", label=label, export_path=export_path)
        snapshot_id = result.get("snapshot_id")
        if not isinstance(snapshot_id, str) or snapshot_id != label or result.get("verified") is not True:
            raise VerificationFailed("Resolve snapshot response did not contain verified identity")
        self._snapshots[snapshot_id] = result
        self._created_timeline_ids[snapshot_id] = []
        return result

    def create_timeline(self, name: str, *, snapshot_id: str, fps: str | None = None, width: int | None = None, height: int | None = None) -> dict[str, Any]:
        if snapshot_id not in self._snapshots:
            raise ResolveUnavailable("create_timeline requires a snapshot created by this adapter instance")
        params: dict[str, Any] = {"name": name, "snapshot_id": snapshot_id}
        if fps:
            params["fps"] = fps
        if width and height:
            params.update(width=width, height=height)
        result = self.call("create_timeline", **params)
        timeline_id = result.get("timeline_id")
        if not isinstance(timeline_id, str) or not timeline_id or result.get("name") != name or result.get("verified") is not True:
            raise VerificationFailed("Resolve did not verify a newly created VANTA timeline")
        self._created_timeline_ids[snapshot_id].append(timeline_id)
        return result

    def append_clip(self, timeline_id: str, source_path: str, source_in: int, source_out: int, timeline_start: int, track: int, media_type: int | None = None) -> dict[str, Any]:
        params = {"timeline_id": timeline_id, "source_path": source_path, "source_in_frame": source_in, "source_out_frame": source_out, "timeline_start_frame": timeline_start, "track_index": track}
        if media_type:
            params["media_type"] = media_type
        return self.call("append_clip", **params)

    def add_marker(self, timeline_id: str, frame: int, color: str, name: str, note: str, custom_data: str) -> dict[str, Any]:
        return self.call("add_marker", timeline_id=timeline_id, frame=frame, color=color, name=name, note=note, custom_data=custom_data)

    def render_proxy(self, timeline_id: str, output_path: str) -> dict[str, Any]:
        target = Path(output_path)
        return self.call("render_start", timeline_id=timeline_id, target_dir=str(target.parent), custom_name=target.stem, width=540, height=960, quality="Medium")

    def rollback(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = self._snapshots.get(snapshot_id)
        created = self._created_timeline_ids.get(snapshot_id)
        if not snapshot or not created:
            raise ResolveUnavailable("rollback requires exact VANTA-created timeline IDs from this adapter instance")
        baseline_id = snapshot.get("baseline_current_timeline_id")
        if not isinstance(baseline_id, str) or not baseline_id:
            raise VerificationFailed("snapshot did not retain baseline timeline identity")
        result = self.call("rollback_vanta_timelines", snapshot_id=snapshot_id, created_timeline_ids=created, baseline_current_timeline_id=baseline_id)
        if result.get("verified") is not True or result.get("restored_snapshot_id") != snapshot_id:
            raise VerificationFailed("Resolve rollback response did not verify exact VANTA timeline deletion")
        self._rolled_back.add(snapshot_id)
        return result

    def register_disposable_session(self, label: str, project_name: str) -> None:
        """Gate runs happen in a project VANTA just created, so no .drp baseline exists."""
        if not project_name.startswith("VANTA_GATE_"):
            raise ResolveUnavailable("disposable sessions are only allowed in VANTA_GATE_ projects")
        self._snapshots[label] = {"snapshot_id": label, "verified": True, "baseline_current_timeline_id": None, "disposable_project": project_name}
        self._created_timeline_ids[label] = []

    def created_timelines(self, snapshot_id: str) -> list[str]:
        return list(self._created_timeline_ids.get(snapshot_id, []))


def default_adapter(*, allow_writes: bool = False) -> ResolveScriptingAdapter:
    from vanta.config import get_settings

    settings = get_settings()
    return ResolveScriptingAdapter(settings.resolve_root, allow_writes=allow_writes, output_root=settings.data_dir)
