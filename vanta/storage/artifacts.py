"""Run-scoped, reproducible artifact directories with atomic manifest writes."""

from __future__ import annotations

import json
import os
from pathlib import Path

from vanta.core.schemas import RunManifest


class ArtifactStore:
    def __init__(self, data_dir: str | Path):
        self.base = Path(data_dir).resolve() / "runs"

    def create_run(self, manifest: RunManifest) -> RunManifest:
        self.base.mkdir(parents=True, exist_ok=True)
        run_root = (self.base / manifest.run_id).resolve()
        if not run_root.is_relative_to(self.base.resolve()):
            raise ValueError("run artifact path escaped the configured data directory")
        run_root.mkdir(parents=True, exist_ok=False)
        for relative in (
            "events",
            "solver_outputs",
            "masks",
            "luts",
            "cdl",
            "drx",
            "fusion",
            "proxy",
            "qc",
            "thumbs",
        ):
            (run_root / relative).mkdir()
        saved = manifest.model_copy(update={"artifact_root": str(run_root)})
        temp = run_root / "manifest.json.tmp"
        final = run_root / "manifest.json"
        payload = json.dumps(saved.model_dump(mode="json"), indent=2, ensure_ascii=False)
        with temp.open("x", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, final)
        return saved
