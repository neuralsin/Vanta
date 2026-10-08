"""Compile an EDG JSON file to an FCPXML interchange artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vanta.compile.fcpxml import compile_edg_to_fcpxml
from vanta.core.schemas import EditDecisionGraph


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("edg", type=Path, help="Input Edit Decision Graph JSON")
    parser.add_argument(
        "output", type=Path, help="New .fcpxml output path; existing files are never replaced"
    )
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    args = parser.parse_args()
    graph = EditDecisionGraph.model_validate_json(args.edg.read_text(encoding="utf-8"))
    result = compile_edg_to_fcpxml(graph, args.output, width=args.width, height=args.height)
    print(json.dumps(result.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
