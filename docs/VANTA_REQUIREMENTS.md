# VANTA requirements and acceptance ledger

Status vocabulary: `DONE`, `IN PROGRESS`, `BLOCKED`, `NOT STARTED`. Gate evidence is recorded separately in `capabilities.yaml`; installation is not a passed integration test.

## Phase 0 — feasibility and capability gates

- [x] G1–G16 are enumerated and machine-readable; missing/untested Resolve behavior is not called a pass.
- [x] Detect OS, Python, RAM, GPU/CUDA (when available), free disk, Resolve executable/version, scripting module, MCP package, FFmpeg/Ollama, Python packages, fonts and bundled LUT filenames.
- [x] Prefer the NVIDIA driver VRAM query over Windows adapter metadata; report free space across fixed disks.
- [x] Detect Resolve's embedded Python runtime/version and inspect MCP package tool names without launching the Resolve UI.
- [x] Write `capabilities.yaml`, JSON report, and plugin registry without launching Resolve or downloading models.
- [ ] G1 timeline/import/trim/track/marker/idempotence integration.
- [ ] G2 FCPXML cuts/dissolves.
- [ ] G3 and G3b Fusion text/OCR/placement and subject occlusion.
- [ ] G4 exported LUT/DRX/CDL colour verification.
- [ ] G5 and G6 subject isolation/matte import/alignment.
- [ ] G8 word-level text edit and A/V sync.
- [ ] G9 three-reel time/peak-VRAM benchmarks.
- [ ] G10 reject untested Resolve versions.
- [ ] G11 20-step visible follow-along test.
- [ ] G12 transition API exact-duration test.
- [ ] G13 constant speed and speed ramp test.
- [ ] G14 external camera solve import/slip test.
- [ ] G15 complete compatible registry scan (filename inventory is partial evidence only).
- [ ] G16 failure injection, safe rollback, and user-visible alerts.

## Phase 1 — real Resolve vertical slice

- [x] Versioned core contracts, including corrected event evidence fields, source-path binding, non-overlap, and exact timing policy.
- [x] Deterministic EDG-to-FCPXML hard-cut prototype with rational frame/time conversion, music lane, and source/output safety checks; Resolve import remains G2-blocked.
- [x] SQLite WAL storage for runs, events, jobs, migration ledger, and guarded job/run lifecycle transitions.
- [x] Single Resolve Adapter boundary, closed broker protocol, and fail-closed policy validation.
- [x] Fixed plan validator, manual confidence bands, checkpoint primitive, snapshot ownership requirement, exact read-back predicates, and VANTA-only rollback sequence (test-double evidence only).
- [ ] Live adapter write/read/verify methods, verified Resolve snapshots/rollback, and idempotence on the real Resolve API.
- [ ] Gate-verified live WATCH / STEP / TURBO, ASSISTED / AUTO / MANUAL, pause/stop/undo controls.
- [x] Mission Control gate view and persistent event feed.
- [x] Pinned repeatable Python environment installation and clean setup/probe run.
- [x] WATCH / STEP / TURBO checkpoint primitive; Mission Control refuses to record fake control signals before an executor attaches.
- [x] Run-scoped artifact directory and atomic manifest creation.
- [x] Resolve embedded-Python subprocess broker for fixed read-only status/state actions; arbitrary scripts and mutation actions are rejected.
- [x] Opt-in G1 five-clip direct-assembly/marker/idempotence/rollback harness; it generates evidence but does not set G1 to PASS.
- [ ] Mission Control stage/plan/EDG/artifact/error detail.
- [ ] Five clips + one song → beat-cut timeline → markers → proxy render → verification → rollback.

## Later phases

- [ ] Perception, calibration, evidence-linked Style Capsules.
- [ ] Profile-isolated Style Brain, conditional/negative rules, user approval queue.
- [ ] Deterministic grade solver and export-based verification.
- [ ] Vetted Fusion templates, OCR-checked text placement, masks/tracking.
- [ ] Licensed music indexing, ranked reasons, beat-grid solver.
- [ ] Planner/critic, QC levels, scoped patches and at most three repair iterations.
- [ ] Correction capture, candidate rules, reviewed learning, held-out evaluation.
- [ ] Install/setup docs, local creative-workstation UI, reproducible run bundles, release gates.
