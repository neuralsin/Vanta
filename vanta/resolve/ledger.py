"""Live capability ledger (manual 7): gate results recorded by the gate runner.

The executor refuses any operation class whose gate has not passed on the
*currently running* Resolve version (G10 drift guard) and routes it to the
manual's fallback instead.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vanta.config import get_settings

# operation class -> gate that must pass, and the fallback when it has not
OPERATION_GATES: dict[str, tuple[str, str]] = {
    "structure": ("G1", "blocked: run the Resolve compatibility check first"),
    "fcpxml": ("G2", "direct AppendToTimeline"),
    "fusion_text": ("G3", "pre-rendered overlay clip"),
    "behind_subject": ("G3b", "pre-rendered overlay with matte compositing"),
    "grade_lut": ("G4", "skip grade, flag for manual grading"),
    "grade_drx": ("G4b", "LUT-only grading (single export-verified LUT)"),
    "mattes": ("G6", "burn mattes into pre-composited overlay"),
    "magic_mask": ("G6b", "no Resolve Magic Mask handle (SAM 2.1 mattes only)"),
    "live_follow": ("G11", "STEP mode + breadcrumbs"),
    "transitions": ("G12", "hard cut (FCPXML dissolve or Fusion halves)"),
    "speed": ("G13", "pre-rendered speed change"),
    "render": ("G1", "VANTA preview render for QC; final render manually"),
    "failure_recovery": ("G16", "STEP mode with manual recovery"),
}


@dataclass
class Ledger:
    resolve_version: str | None = None
    product: str | None = None
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    updated_at: str | None = None
    path: Path | None = None

    def status(self, gate: str) -> str:
        return (self.gates.get(gate) or {}).get("status", "NOT_RUN")

    def passed(self, gate: str, *, current_version: str | None = None) -> bool:
        if current_version and self.resolve_version and current_version != self.resolve_version:
            return False  # G10: gates were measured on another Resolve version
        return self.status(gate) == "PASS"

    def allows(self, operation: str, *, current_version: str | None = None) -> tuple[bool, str]:
        gate, fallback = OPERATION_GATES[operation]
        if self.passed(gate, current_version=current_version):
            return True, f"{gate} passed"
        return False, f"{gate} {self.status(gate)} -> fallback: {fallback}"

    def record(self, gate: str, status: str, evidence: dict[str, Any], *, version: str | None = None) -> None:
        if version:
            if self.resolve_version and version != self.resolve_version:
                self.gates = {}  # new version: previous evidence no longer applies
            self.resolve_version = version
        self.gates[gate] = {"status": status, "evidence": evidence, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        self.save()

    def save(self) -> None:
        path = self.path or default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload = {"resolve_version": self.resolve_version, "product": self.product, "gates": self.gates, "calibration": self.calibration, "updated_at": self.updated_at}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def default_path() -> Path:
    return get_settings().data_dir / "capabilities_live.json"


def load_ledger(path: Path | None = None) -> Ledger:
    path = path or default_path()
    if not path.is_file():
        return Ledger(path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return Ledger(resolve_version=data.get("resolve_version"), product=data.get("product"), gates=data.get("gates", {}), calibration=data.get("calibration", {}), updated_at=data.get("updated_at"), path=path)
