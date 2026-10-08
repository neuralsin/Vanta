# Resolve capability-gate runbook

This runbook is deliberately separate from the normal application. It produces evidence for a manual gate; it never changes `capabilities.yaml` on its own.

## G1: direct timeline assembly

The current G1 runner tests the manual's direct route with exactly five distinct, already-imported video clips:

1. It requires an already-open project named `VANTA_GATE_*`, a pre-existing baseline timeline, Local scripting, and five source files that are already in that project's media pool.
2. It exports a new `.drp` recovery artifact, creates one uniquely named VANTA timeline, appends each clip with explicit source-in/source-out/record-frame/track values, and writes a VANTA marker after every verified clip.
3. It reads every clip and marker back by immutable IDs/path/custom data.
4. It retries the same timeline creation and requires the broker to reject it without changing the timeline list.
5. It deletes only the exact timeline it created and restores the baseline timeline selection. If rollback does not verify, the result is `FAIL`.

The runner has no launch, import-media, or project-creation command. Those omissions are intentional: they force the operator to verify that the selected project is disposable first.

Copy `configs/g1_fixture.example.json`, replace every placeholder with controlled local media, and first run the no-write preflight:

```powershell
.\.venv\Scripts\python.exe scripts\run_g1_gate.py --fixture D:\path\to\g1_fixture.json --artifact-root D:\Vanta\data\gate-runs\g1-001
```

When Resolve Studio is open, Local scripting is enabled, the specified disposable project is selected, and all five clips are imported, run the same command with both explicit mutation acknowledgements:

```powershell
.\.venv\Scripts\python.exe scripts\run_g1_gate.py --fixture D:\path\to\g1_fixture.json --artifact-root D:\Vanta\data\gate-runs\g1-001 --execute --confirm-disposable-project
```

The output is a candidate result at `g1_result.json`. Review the timeline/read-back data and the rollback result before changing G1 from `BLOCKED` to `PASS`. The G1 run does not test FCPXML import (G2), live repaint timing (G11), or the full failure matrix (G16).
