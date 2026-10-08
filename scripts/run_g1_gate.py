"""Run the opt-in G1 direct-assembly experiment in a disposable Resolve project."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from vanta.resolve.adapter import ResolveScriptingAdapter
from vanta.resolve.gates import G1Fixture, run_g1


def _write_exclusive(path: Path, payload: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path, help="Five-clip G1 fixture JSON")
    parser.add_argument(
        "--artifact-root",
        required=True,
        type=Path,
        help="New empty output folder for gate evidence",
    )
    parser.add_argument(
        "--resolve-root",
        type=Path,
        default=Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve"),
    )
    parser.add_argument(
        "--execute", action="store_true", help="Allow writes to the already-open disposable project"
    )
    parser.add_argument(
        "--confirm-disposable-project",
        action="store_true",
        help="Confirm the selected project is disposable and named with the VANTA_GATE_ prefix",
    )
    args = parser.parse_args()
    fixture = G1Fixture.model_validate_json(args.fixture.read_text(encoding="utf-8"))
    absent = [
        clip.source_path
        for clip in fixture.clips
        if not Path(clip.source_path).expanduser().is_file()
    ]
    if absent:
        parser.error("fixture sources are absent: " + ", ".join(absent))
    if not args.execute:
        print(
            "G1 preflight passed. No Resolve call was made. Re-run with --execute and --confirm-disposable-project only after opening the named disposable project and importing the five clips."
        )
        return 0
    if not args.confirm_disposable_project:
        parser.error("--execute requires --confirm-disposable-project")
    artifact_root = args.artifact_root.expanduser().resolve()
    if artifact_root.exists():
        parser.error("artifact-root must be new so evidence cannot be overwritten")
    artifact_root.mkdir(parents=True)
    adapter = ResolveScriptingAdapter(args.resolve_root, allow_disposable_gate_mutations=True)
    outcome = run_g1(adapter, fixture, artifact_root=artifact_root)
    _write_exclusive(artifact_root / "g1_result.json", outcome.model_dump(mode="json"))
    print(json.dumps(outcome.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0 if outcome.status == "PASS_CANDIDATE" else 1


if __name__ == "__main__":
    sys.exit(main())
