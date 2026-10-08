"""Narrow Resolve boundary. No operation is reported successful without read-back."""

from __future__ import annotations

import json
import os
import subprocess
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
    """High-level adapter. Implementations must keep every vendor call inside this boundary."""

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Return live, gate-backed capabilities."""

    @abstractmethod
    def read_state(self, target: dict[str, Any]) -> dict[str, Any]:
        """Read project/timeline/media state for verification."""

    @abstractmethod
    def open_page(self, page: str) -> dict[str, Any]:
        """Switch the Resolve page when the live-follow gate has passed."""

    @abstractmethod
    def create_timeline(self, name: str, *, snapshot_id: str) -> dict[str, Any]:
        """Create a new timeline only; source timelines are never overwritten."""

    @abstractmethod
    def append_clip(
        self,
        timeline_id: str,
        source_path: str,
        source_in: int,
        source_out: int,
        timeline_start: int,
        track: int,
    ) -> dict[str, Any]:
        """Append one path-resolved media-pool item with exact source/record bounds."""

    @abstractmethod
    def add_marker(
        self,
        timeline_id: str,
        frame: int,
        color: str,
        name: str,
        note: str,
        custom_data: str,
    ) -> dict[str, Any]:
        """Create a breadcrumb marker for an operation."""

    @abstractmethod
    def render_proxy(self, timeline_id: str, output_path: str) -> dict[str, Any]:
        """Queue a proxy render; caller must poll and verify its result."""

    @abstractmethod
    def snapshot(self, label: str, *, export_path: str) -> dict[str, Any]:
        """Export backup evidence and record immutable baseline timeline identity."""

    @abstractmethod
    def rollback(self, snapshot_id: str) -> dict[str, Any]:
        """Restore the snapshot and return state that the caller must verify."""


class UnavailableResolveAdapter(ResolveAdapter):
    """Honest default until a supported live bridge passes its integration gates."""

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

    def create_timeline(self, name: str, *, snapshot_id: str) -> dict[str, Any]:
        self._unavailable()

    def append_clip(
        self,
        timeline_id: str,
        source_path: str,
        source_in: int,
        source_out: int,
        timeline_start: int,
        track: int,
    ) -> dict[str, Any]:
        self._unavailable()

    def add_marker(
        self,
        timeline_id: str,
        frame: int,
        color: str,
        name: str,
        note: str,
        custom_data: str,
    ) -> dict[str, Any]:
        self._unavailable()

    def render_proxy(self, timeline_id: str, output_path: str) -> dict[str, Any]:
        self._unavailable()

    def snapshot(self, label: str, *, export_path: str) -> dict[str, Any]:
        self._unavailable()

    def rollback(self, snapshot_id: str) -> dict[str, Any]:
        self._unavailable()


class ResolveScriptingAdapter(UnavailableResolveAdapter):
    """Closed-protocol adapter backed by Resolve's version-matched Python runtime.

    The default is discovery only. The mutating protocol exists so a dedicated
    disposable-project gate harness can test it, but this adapter never reports
    gate verification merely because methods are implemented.
    """

    def __init__(
        self,
        resolve_root: str | Path,
        *,
        timeout_s: float = 12.0,
        allow_disposable_gate_mutations: bool = False,
    ):
        super().__init__("Resolve scripting connection unavailable")
        self.resolve_root = Path(resolve_root)
        self.python = self.resolve_root / "ResolvePython" / "ResolvePython.exe"
        self.module_path = self.resolve_root / "ResolvePython" / "lib" / "modules"
        self.library = self.resolve_root / "fusionscript.dll"
        self.worker = Path(__file__).with_name("broker_worker.py")
        self.timeout_s = timeout_s
        self.allow_disposable_gate_mutations = allow_disposable_gate_mutations
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._created_timeline_ids: dict[str, list[str]] = {}
        self._rolled_back: set[str] = set()

    def _request(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        mutation_actions = {
            "snapshot",
            "create_timeline",
            "append_clip",
            "add_marker",
            "rollback_vanta_timelines",
        }
        if action not in {"status", "read_state", *mutation_actions}:
            raise ResolveUnavailable(
                f"Action is not in the VANTA Resolve adapter protocol: {action}"
            )
        if action in mutation_actions and not self.allow_disposable_gate_mutations:
            raise ResolveUnavailable(
                "Resolve writes are disabled until a disposable live gate harness explicitly enables them"
            )
        if (
            not all(path.is_file() for path in (self.python, self.library, self.worker))
            or not self.module_path.is_dir()
        ):
            raise ResolveUnavailable(
                "Resolve's embedded Python runtime or scripting module is missing"
            )
        env = os.environ.copy()
        env["RESOLVE_SCRIPT_LIB"] = str(self.library)
        env["VANTA_RESOLVE_MODULE_PATH"] = str(self.module_path)
        env["VANTA_BROKER_ROOT"] = str(Path(__file__).resolve().parents[2])
        if self.allow_disposable_gate_mutations:
            env["VANTA_RESOLVE_GATE_MUTATIONS"] = "1"
        request = json.dumps({"action": action, "params": params or {}}, ensure_ascii=False) + "\n"
        try:
            result = subprocess.run(
                [str(self.python), "-u", str(self.worker)],
                input=request,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_s,
                check=False,
                env=env,
                cwd=str(self.resolve_root),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise ResolveUnavailable(
                f"Resolve scripting request timed out after {self.timeout_s:g}s"
            ) from exc
        except OSError as exc:
            raise ResolveUnavailable(f"Could not start Resolve's embedded Python: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip()[-1000:] or f"exit code {result.returncode}"
            raise ResolveUnavailable(f"Resolve scripting broker failed: {detail}")
        try:
            response = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            raise ResolveUnavailable("Resolve scripting broker returned invalid JSON") from exc
        if not response.get("ok"):
            raise ResolveUnavailable(response.get("error", "Resolve scripting operation failed"))
        return response.get("result", {})

    def capabilities(self) -> dict[str, Any]:
        try:
            return {"verified": False, **self._request("status")}
        except ResolveUnavailable as exc:
            return {"connected": False, "verified": False, "reason": str(exc)}

    def read_state(self, target: dict[str, Any]) -> dict[str, Any]:
        if target.get("scope") == "snapshot":
            snapshot_id = target.get("snapshot_id")
            if snapshot_id in self._rolled_back:
                return {"restored_snapshot_id": snapshot_id}
            return {"restored_snapshot_id": None}
        state = self._request("read_state", target)
        if not state.get("connected"):
            raise ResolveUnavailable(state.get("reason", "Resolve is not connected"))
        return state

    def snapshot(self, label: str, *, export_path: str) -> dict[str, Any]:
        result = self._request("snapshot", {"label": label, "export_path": export_path})
        snapshot_id = result.get("snapshot_id")
        if (
            not isinstance(snapshot_id, str)
            or snapshot_id != label
            or result.get("verified") is not True
        ):
            raise VerificationFailed("Resolve snapshot response did not contain verified identity")
        self._snapshots[snapshot_id] = result
        self._created_timeline_ids[snapshot_id] = []
        return result

    def create_timeline(self, name: str, *, snapshot_id: str) -> dict[str, Any]:
        if snapshot_id not in self._snapshots:
            raise ResolveUnavailable(
                "create_timeline requires a snapshot created by this adapter instance"
            )
        result = self._request("create_timeline", {"name": name, "snapshot_id": snapshot_id})
        timeline_id = result.get("timeline_id")
        if (
            not isinstance(timeline_id, str)
            or not timeline_id
            or result.get("name") != name
            or result.get("verified") is not True
        ):
            raise VerificationFailed("Resolve did not verify a newly created VANTA timeline")
        self._created_timeline_ids[snapshot_id].append(timeline_id)
        return result

    def append_clip(
        self,
        timeline_id: str,
        source_path: str,
        source_in: int,
        source_out: int,
        timeline_start: int,
        track: int,
    ) -> dict[str, Any]:
        return self._request(
            "append_clip",
            {
                "timeline_id": timeline_id,
                "source_path": source_path,
                "source_in_frame": source_in,
                "source_out_frame": source_out,
                "timeline_start_frame": timeline_start,
                "track_index": track,
            },
        )

    def add_marker(
        self,
        timeline_id: str,
        frame: int,
        color: str,
        name: str,
        note: str,
        custom_data: str,
    ) -> dict[str, Any]:
        return self._request(
            "add_marker",
            {
                "timeline_id": timeline_id,
                "frame": frame,
                "color": color,
                "name": name,
                "note": note,
                "custom_data": custom_data,
            },
        )

    def rollback(self, snapshot_id: str) -> dict[str, Any]:
        snapshot = self._snapshots.get(snapshot_id)
        created = self._created_timeline_ids.get(snapshot_id)
        if not snapshot or not created:
            raise ResolveUnavailable(
                "rollback requires exact VANTA-created timeline IDs from this adapter instance"
            )
        baseline_id = snapshot.get("baseline_current_timeline_id")
        if not isinstance(baseline_id, str) or not baseline_id:
            raise VerificationFailed("snapshot did not retain baseline timeline identity")
        result = self._request(
            "rollback_vanta_timelines",
            {
                "snapshot_id": snapshot_id,
                "created_timeline_ids": created,
                "baseline_current_timeline_id": baseline_id,
            },
        )
        if result.get("verified") is not True or result.get("restored_snapshot_id") != snapshot_id:
            raise VerificationFailed(
                "Resolve rollback response did not verify exact VANTA timeline deletion"
            )
        self._rolled_back.add(snapshot_id)
        return result
