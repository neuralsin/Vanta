"""Conservative pre-execution policy checks for EDG actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from vanta.core.schemas import EditDecisionGraph, Patch


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


def validate_edg(
    graph: EditDecisionGraph,
    *,
    capabilities: dict,
    project_root: Path,
    structure_route: Literal["direct", "fcpxml"] = "direct",
) -> list[str]:
    """Return every policy violation; callers must not execute on a nonempty result."""
    errors: list[str] = []
    resolve = capabilities.get("resolve", {})
    gates = capabilities.get("gates", {})
    version = resolve.get("version")
    version_gate = gates.get("G10", {}).get("status")
    if not resolve.get("installed"):
        errors.append("DaVinci Resolve is not installed")
    if version_gate != "PASS":
        errors.append(f"Resolve version {version or 'unknown'} has not passed the version gate")
    root = project_root.resolve()
    for source in graph.sources:
        if not source.read_only:
            errors.append(f"source {source.id} is not marked read-only")
        source_path = Path(source.path).expanduser().resolve()
        if source_path == root:
            errors.append(f"source path for {source.id} cannot be the project root")
        if not source_path.is_file():
            errors.append(f"source media is absent or is not a file: {source.id}")
    if graph.music and not graph.music.licence_confirmed:
        errors.append("music rights are not confirmed")
    required_gate = "G1" if structure_route == "direct" else "G2"
    if gates.get(required_gate, {}).get("status") != "PASS":
        errors.append(
            f"{structure_route} structure route requires Resolve integration gate {required_gate}"
        )
    return errors


def validate_patch(patch: Patch, *, confidence_threshold: float = 0.70) -> PolicyDecision:
    if patch.operation not in {"replace", "adjust", "move", "remove", "add"}:
        return PolicyDecision(False, "unsupported patch operation")
    if patch.dimension not in {"cut", "grade", "text", "mask", "music", "metadata"}:
        return PolicyDecision(False, "patch dimension is not allowed")
    if patch.confidence < 0.50:
        return PolicyDecision(False, "confidence is below the minimum safe threshold")
    if patch.confidence < confidence_threshold and not patch.requires_approval:
        return PolicyDecision(False, "low-confidence patch must require approval")
    return PolicyDecision(
        True, "patch is structurally allowed; EDG validation and execution gates still apply"
    )
