# VANTA live build progress

Last updated: 2026-10-09 (Asia/Calcutta)

## Manual-conformance reset — 2026-10-09

The user identified that the first implementation draft was substantially flawed relative to the manual and requested a careful correction before continuing. The repository is now being treated as a prototype under audit, not as a completed Phase 1 foundation.

- Added `docs/MANUAL_AUDIT.md`, mapping the existing implementation to the authoritative manual and identifying unsafe or incomplete claims.
- Most important corrections queued: resolve the EDG source-ID versus Resolve media-ID confusion; replace generic rollback with exact VANTA-timeline ownership; remove unverified proxy rendering from executable operations; make timing and confidence policy explicit; distinguish discovery/protocol/live-gate capability states.
- No Resolve mutation, model download, or new gate claim was made during the audit.
- The prior status prose that called broad Phase 1 pieces “implemented” is provisional. Only the narrowly stated unit-test results and Phase 0 discovery evidence may be relied on until the corrective work is verified.

### Corrected execution semantics — 2026-10-09

- Added `vanta/core/timing.py`. Common broadcast frame rates are represented as rationals. The EDG now rejects same-track overlaps and any source-to-timeline mapping that needs hidden frame rounding; a future retime solver must make that content decision explicitly.
- Added `vanta/core/confidence.py`. The manual's starting 0.50 / 0.70 / 0.90 bands are now one policy: below 0.50 blocks, 0.50–0.70 requires approval, ASSISTED high-impact work requires approval, and AUTO may proceed without approval only at 0.90 or above. Every executor plan event records the decision.
- Rebuilt `vanta/core/executor.py` around logical timeline references rather than guessed Resolve IDs. It resolves an EDG source to the adapter by canonical path, validates a fixed read-back predicate for each operation, generates unique VANTA breadcrumb custom-data IDs, and permits only create timeline / append clip / marker. `render_proxy` is no longer executable until the full render, poll, watchdog and QC contract exists.
- Reworked the adapter method contract so a snapshot has a `.drp` recovery export and captures baseline timeline identity. Rollback is designed to delete only exact timeline IDs created by the same adapter instance, after checking VANTA ownership; it does not overwrite or import over a user project.
- Added `vanta/resolve/protocol.py` and replaced the broker with a closed request protocol. It rejects arbitrary script/code fields before connecting to Resolve. Broker mutations are disabled by default and are exposed only for a future disposable-project gate harness. The standard Mission Control adapter remains non-verified and cannot execute a plan.
- Corrected Mission Control's misleading controls. It now persists reviewable run manifests only; it responds `409` rather than claiming to pause/step/stop a nonexistent live executor.

### Verification after the corrective slice

```text
.\.venv\Scripts\python.exe -m pytest -q
=> 29 passed in 0.72s
.\.venv\Scripts\ruff.exe check scripts vanta tests
=> All checks passed!
.\.venv\Scripts\python.exe -m compileall -q scripts vanta tests
=> passed
```

The tests are contract and failure-boundary tests only. No DaVinci Resolve action ran and no gate status changed.

### Gate evidence preparation — 2026-10-09

- Added an explicit `RunStatus` transition graph. Runs now follow queued → running → paused/terminal semantics; Mission Control leaves a newly created run queued and rejects live controls until a real executor attaches.
- Added `vanta/resolve/gates.py`, `scripts/run_g1_gate.py`, `configs/g1_fixture.example.json`, and `docs/RESOLVE_GATE_RUNBOOK.md`. The runner is an explicit, opt-in disposable-project experiment for G1. It requires exactly five already-imported source clips, a named baseline timeline, local scripting, `--execute`, and `--confirm-disposable-project`. It exports a `.drp`, checks source trims/track/record frames and markers by read-back, tests duplicate creation does not change the timeline list, and verifies VANTA-only rollback.
- The runner does not launch Resolve, create a project, import source media, update `capabilities.yaml`, or claim a pass. Its ordinary preflight makes no Resolve call. The execution mode has not been run because Resolve is not open and no controlled fixture project/media exists.
- Added fake-protocol coverage of the five-clip runner and rollback, exact frame/timing constraints, confidence bands, run state transitions, and broker schemas.

### Latest verification

```text
.\.venv\Scripts\python.exe -m pytest -q
=> 31 passed in 0.87s
.\.venv\Scripts\ruff.exe check scripts vanta tests
=> All checks passed!
.\.venv\Scripts\python.exe -m compileall -q scripts vanta tests
=> passed
.\.venv\Scripts\python.exe scripts\phase0_probe.py
=> 0 PASS, 2 PARTIAL, 14 BLOCKED
.\.venv\Scripts\python.exe scripts\run_g1_gate.py --help
=> passed; command exposes explicit no-write and disposable-project execution flags
```

No acceptance gate changed in this slice.

This file is the detailed handoff record. Update it after each implementation slice with exact changed files, commands, outcomes, gate status, blockers, and a reproducible next action. The authoritative source remains `Resolve Autopilot — Final Build Manual v2.docx`.

## Current state at a glance

- **Workspace:** `D:\Vanta`; it initially contained only the manual DOCX. There was no existing source tree, Git repository, UI, DB, or tests.
- **Current work:** Phase 0 probe and first Phase 1 foundations complete; Phase 1 is in progress.
- **Do not assume a Resolve integration passed:** Resolve is installed but was not running. The Python 3.14 bridge provides only read-only status/state; every mutation remains disabled rather than simulated.
- **Current hardware evidence:** Windows 11 build 26200; Resolve 21.1.1.10; Python 3.11.9; 11.8 GB visible RAM and about 0.76 GB free at probe time; RTX 4050 Laptop GPU. WMI reported 4 GB, but `nvidia-smi` verified **6141 MiB total / 4445 MiB free**, matching the requested ~6 GB VRAM. Active PyTorch 2.10.0+cpu has no CUDA. Fixed volumes C: and D: had 19.4 + 94.2 = **113.6 GB free**, and the workspace drive had 94.2 GB. The stated 190 GB free is not currently measurable on these fixed volumes.
- **No Resolve UI was launched and no model downloads occurred.** The read-only `ResolveMCP.exe --dump-tools` call and embedded Python scripting check were executed. AirLLM, Ollama and FFmpeg are unavailable in the initial runtime; `nvidia-smi` is available.

## Completed work

### 1. Manual and repository audit

- Read the attached execution protocol and master prompt, then inspected the authoritative DOCX once and extracted its section structure, G1–G16 gate matrix, data contracts, architecture, roadmap, safety boundaries, model roles, and failure handling into concise project docs.
- Confirmed root initially had the DOCX only and `git status` failed because this was not a Git repository.
- Removed the temporary text extraction after deriving the project docs; the original DOCX remains untouched.

### 2. Phase 0 capability detection

Files:

- `scripts/phase0_probe.py`
- `capabilities.yaml`
- `configs/capabilities.json`
- `plugin_registry.json`

Probe behavior:

- Queries Windows OS/RAM/GPU metadata, Python version, local disk capacity, Resolve installation/version/scripting module/MCP bundle/process state, command availability, importable Python packages, and CUDA availability when PyTorch imports.
- Inventories Windows font filenames and bundled Resolve LUT filenames. It flags that file presence alone does not verify compatibility/licensing.
- Writes YAML/JSON capability reports and registry. It does not launch Resolve, mutate a project, install packages, or download models.
- Initial measured gate summary: **0 PASS, 2 PARTIAL, 14 BLOCKED** (G10 version evidence and G15 limited registry inventory are partial; live integration behavior is not tested).
- Follow-up found `nvidia-smi` at `C:\WINDOWS\system32\nvidia-smi.EXE`; query reports driver 610.47, RTX 4050 6141 MiB total / 4445 MiB free, current utilization 9% at 44 C. WMI under-reported. Probe updated to use driver-reported VRAM and total fixed-disk free space.
- Follow-up disk query: C: 19.4 GB free, D: 94.2 GB free; 113.6 GB across detected fixed volumes. The free-space check is independent from user estimate and stays explicit.
- G7 is absent from the manual's numbered gate list; G8 follows G6. No gate has been invented to fill that numbering gap.
- `ResolvePython.exe -c "import sys; print(sys.version)"` reports Python 3.14.4. Importing the installed `DaVinciResolveScript` module succeeds. Calling `scriptapp('Resolve')` reports Resolve is not running or Local scripting is disabled.
- `ResolveMCP.exe --dump-tools` reports 14 bundled tools. The MCP manifest labels the bundle Proprietary and includes arbitrary script execution plus direct LUT/DCTL mutation/deletion. VANTA deliberately keeps its own fixed-method boundary.

Re-run with:

```powershell
python scripts/phase0_probe.py
```

### 3. Phase 1 contract/store/visibility foundations

Files:

- `vanta/core/schemas.py` defines versioned Pydantic contracts: EditSpec, EDG sources/shots/music, Style Capsule/Rule, Patch, Event, Job, RunManifest, QC finding/result.
- `vanta/core/policy.py` provides fail-closed gate checks for EDG executability and scoped patch validation.
- `vanta/storage/event_store.py` creates a SQLite WAL database with runs, ordered events, jobs, migration ledger, and guarded state transitions.
- `vanta/resolve/broker_worker.py` is a fixed, read-only Python 3.14 subprocess broker; it accepts only `status` and `read_state`, with no `eval`/`exec` or model-authored code.
- `vanta/resolve/adapter.py` defines the single high-level boundary. `ResolveScriptingAdapter` performs bounded read-only status/state calls; all mutation methods still fail closed.
- `vanta/storage/artifacts.py` creates run-contained artifact directories and atomically writes manifests.
- `vanta/live/run_control.py` supplies cooperative WATCH/STEP checkpoints and pause/resume/next/stop signals.
- `vanta/live/app.py` provides local FastAPI endpoints for health, capabilities, live read-only Resolve status, runs/artifacts, events, EDG policy validation, controls, and WebSocket event updates.
- `ui/mission_control.html` displays measured hardware, gate evidence, fallback details, read-only Resolve status, persisted event history, and run controls.
- `vanta/compile/fcpxml.py` compiles a single hard-cut video track plus one rights-confirmed music lane. It uses exact rational timebases for common fractional rates and refuses overlaps, extra video lanes, missing media, unconfirmed music, and output overwrite.
- `scripts/compile_edg.py` compiles an EDG JSON file to a new `.fcpxml`; it does not import into Resolve.
- `vanta/core/executor.py` implements a fixed action allowlist, EDG/action binding, timeline/output policy, confidence/approval gates, WATCH/STEP checkpoints, snapshot verification, write → read-back compare, rollback, and structured events. The live adapter has no mutation methods yet.
- Executor tests use an in-memory adapter to verify sequencing only; these do not count as Resolve integration evidence.
- Added Windows environment setup, README, changelog, and `.gitignore`.

### 4. Reproducibility and docs

- Added `docs/VANTA_SPEC.md`, `docs/VANTA_REQUIREMENTS.md`, `docs/VANTA_DECISIONS.md`, `VANTA_IMPLEMENTATION_STATUS.md`, `ARCHITECTURE.md`, `README.md`, `CHANGELOG.md`, `.env.example`, `pyproject.toml`, `.gitignore`, and `scripts/setup.ps1`.
- Manual-derived architecture and non-destructive policy are recorded; state remains explicit that the product is not yet a working editor.

## Commands executed and results

```text
python scripts/phase0_probe.py
=> wrote capabilities.yaml, configs/capabilities.json, plugin_registry.json
=> gate summary {"pass": 0, "partial": 2, "blocked": 14, "not_run": 0}

python -m pytest -q
=> 20 passed in 1.02s

python -m compileall -q scripts vanta tests
=> passed

FastAPI TestClient smoke:
GET /api/health => 200, reports resolve adapter unavailable-until-gated
GET /           => 200
GET /api/resolve/status => 200; reports disconnected because Resolve is not running or Local scripting is disabled

ruff check scripts vanta tests
=> All checks passed!

nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv,noheader
=> NVIDIA GeForce RTX 4050 Laptop GPU, 6141 MiB total, 4445 MiB free, driver 610.47
```

The probe was updated after WMI/driver disagreement; regenerate the report before using it as current evidence:

```powershell
python scripts/phase0_probe.py
```

Test files: `tests/test_schemas.py`, `tests/test_event_store.py`, `tests/test_policy.py`, `tests/test_run_control.py`, `tests/test_mission_control_api.py`, `tests/test_resolve_broker.py`, `tests/test_fcpxml.py`, `tests/test_executor.py`.

## Known limitations / blockers

1. G1–G16 are recorded, not passed. G1/2/3/3b/4/5/6/8/11/12/13/14/16 need a running Resolve Studio session, appropriate media/templates, and real write/read/verify/failure tests. G9 requires representative footage and GPU metrics. G15 is only a filename inventory.
2. Resolve Studio entitlement is not confirmed from the executable alone. The version gate stays partial until the live app/bridge is inspected and exercised.
3. Active Python is 3.11.9 while the manual's target is 3.12+. The probe and current contracts are compatible with 3.11, but final environment standardization remains open.
4. Current `torch` is CPU-only despite the 6 GB GPU. The current fixed volumes expose ~113.6 GB free, below the 190 GB estimate; confirm model storage and cache location before choosing a model.
5. No real footage/song was supplied in the workspace. Licensed music status cannot be presumed.
6. FCPXML is structurally tested but not imported into Resolve. No model planner, live Resolve mutation bridge, proxy render, critic, or correction learning is implemented yet.

## Exact continuation sequence

1. Re-run `python scripts/phase0_probe.py` if the machine or Resolve state changes.
2. Inspect exact Resolve 21.1.1.10 method signatures for import, append, marker, snapshot/export, render, poll, and read-back; add only fixed methods with paired verification.
3. Run G1/G2 in a disposable Resolve Studio project with test media, checking import, exact source-frame trim, clip order, marker read-back, and idempotent rerun.
4. Add real proxy polling/render verification and rollback failure injection, then connect them to Mission Control and artifact inspection.
5. Build the five-video/one-song path only with supplied media and a confirmed licensed track; preserve originals and write to new timelines/run folders.
6. Implement perception/model providers and scheduler after verifying CUDA and storage. Do not download models automatically; compare output quality and latency on fixed evaluation tasks.

## Current phase report

CURRENT PHASE: Phase 1 — contracts, compiler, executor infrastructure, and Mission Control (in progress)
COMPLETED: Phase 0 capability probe and reports; manual-derived project docs; core data contracts; SQLite event/job store; artifacts; run controls; fail-closed policy layer; fixed read-only Resolve broker; Mission Control v0; setup files
TESTS: 20 tests pass; Python compileall passes; Ruff passes; API health/UI/read-only Resolve status smoke passes; FCPXML structural checks pass
FAILURES: No code test failures. Resolve acceptance gates remain blocked/partial as listed above.
BLOCKERS: Live Resolve was not started; Studio license unconfirmed; no media/song; live mutation methods unimplemented; CPU-only PyTorch; available disk space below 190 GB
NEXT: Inspect exact Resolve scripting API details and implement the first version-gated import/verify method; run G1/G2 in a disposable project

## External research notes (2026-10-09)

- Upstream AirLLM README claims large models can be streamed layer-wise with low VRAM, and documents model checkpoint splitting, disk use, Transformers/CUDA dependencies, and compression. It reports Qwen3.8-27B vision-language execution at 3.33 GB VRAM on an RTX 3090, but that benchmark is not this machine's performance: [upstream AirLLM README](https://github.com/lyogavin/airllm).
- An open upstream AirLLM issue reports a Windows 11 + NVIDIA case at 28.6 seconds/token for a 3B model. It is one report, not a general benchmark, but enough reason to measure real latency before adopting it for interactive planning: [AirLLM issue #364](https://github.com/lyogavin/airllm/issues/364).

## Continuation update — pinned environment and Resolve API inspection (2026-10-09)

### Environment reproducibility

- Updated `scripts/setup.ps1` to install exact package versions from `requirements.lock`, then install the local package editable with `--no-deps`. The script verifies `pip` exit codes and stops if the lockfile is missing.
- Re-ran `scripts/setup.ps1` successfully on Python 3.12.10. It reinstalled/verified the 29 locked packages, rebuilt editable metadata, and refreshed the Phase 0 reports.
- Latest probe at this update: 0 PASS, 2 PARTIAL, 14 BLOCKED; 11.8 GB total RAM, about 1.6 GB free RAM, 6141 MiB GPU VRAM, around 101.0 GB free on D: and 121.6 GB across detected fixed disks. The reported disk target (190 GB) is still unmet. Torch/CUDA, AirLLM, FFmpeg and Ollama remain unavailable in `.venv`/PATH.
- Updated `README.md` and `VANTA_IMPLEMENTATION_STATUS.md` to describe the Python 3.12.10 venv, latest disk/RAM evidence, lock-based setup, and the compiler/executor foundations.

### Resolve scripting API source inspection

- Used the installed proprietary Resolve MCP only for read-only tool inventory and API/doc retrieval. Its handshake identifies DaVinci Resolve Studio 21.1.1 and reports that Resolve is not running. No launch, script execution inside Resolve, project mutation, model fetch, or media operation occurred.
- Retrieved the locally installed version-matched `DaVinciResolveScript.pyi` stub and `README.md` into the OS temp directory using the MCP's `as_file` options. Did not copy proprietary vendor docs into the repository.
- Recorded exact relevant API names and safety constraints in `docs/RESOLVE_API_NOTES.md`: new timeline, append, marker/custom data, project export, timeline delete, and render job signatures. The stub does not confirm inclusive/exclusive source-out behavior, marker frame coordinate, save/reopen persistence, or runtime behavior.
- Decision: write adapter operations only for VANTA-owned new timelines. Project export is backup evidence; rollback must target exact VANTA-created timeline IDs and preserve baseline timelines. Do not call this tested or enable executor until live read-back and failure injection pass.

### Verification rerun

```text
.\scripts\setup.ps1
=> exit 0; pinned environment install and Phase 0 refresh succeeded
.\.venv\Scripts\python.exe -m pytest -q
=> 21 passed in 0.78s
.\.venv\Scripts\ruff.exe check scripts vanta tests
=> All checks passed!
.\.venv\Scripts\python.exe -m compileall -q scripts vanta tests
=> passed
```

### Current status and next action

- Phase 1 foundations, FCPXML compiler, execution sequencing and Mission Control v0 remain implemented. No real Resolve acceptance gate has changed status; gates remain 0 PASS / 2 PARTIAL / 14 BLOCKED.
- Next: build fixed broker/adapter operations with strict snapshot ownership, media/timeline ID resolution, read-back, and rollback semantics, then unit-test those operations against protocol-faithful fakes. Keep `capabilities().verified` false until live G1/G2/G16 evidence is collected in a disposable Resolve Studio project. Proxy render remains disabled pending a similarly tested render configuration/poll/verification flow.
- Resolve UI, Studio entitlement, media inputs and confirmed licensed music remain outstanding. Keep writing both `progress.md` and `context.md` after major slices.
