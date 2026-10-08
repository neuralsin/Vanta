# VANTA continuation context

Updated: 2026-10-09 (Asia/Calcutta)

This file is a compact recovery snapshot for continuing the active VANTA build after a context reset. It records user intent, repository facts, decisions, verified results, blockers, and the next concrete actions. It is not a substitute for the authoritative manual or the detailed chronological `progress.md`.

## User objective and instructions

Build VANTA end to end according to the project-folder `Resolve Autopilot — Final Build Manual v2.docx`, without skipping roadmap/acceptance requirements. The user wants very high edit quality and a strong AI brain, is willing to wait longer, says the machine has 12 GB RAM, 6 GB VRAM and 190 GB SSD space, and explicitly permits AirLLM if useful. They also asked that all work be documented continuously in `progress.md`, and now requested this `context.md` recovery file be refreshed periodically.

Keep the architecture from the manual: Perception → Style Capsules/Brain → Footage Graph → Planner → EDG → deterministic solvers → validator/policy/confidence gates → compilers → one Resolve Adapter → read-back verification → proxy/QC/critic → scoped patches → reviewed learning. Do not turn this into a chatbot that emits arbitrary Resolve commands. Do not claim VANTA is built until the real supervised edit loop works in Resolve.

## Workspace and source of truth

- Workspace: `D:\Vanta`
- Original authoritative manual, untouched: `D:\Vanta\Resolve Autopilot — Final Build Manual v2.docx`
- The initial folder had that DOCX only and no `.git`, source code, tests, UI, or database.
- `docs/VANTA_SPEC.md` is the concise implementation map.
- `docs/VANTA_REQUIREMENTS.md` is the checkbox ledger.
- `docs/VANTA_DECISIONS.md` explains implementation decisions.
- `VANTA_IMPLEMENTATION_STATUS.md` is the milestone/status summary.
- `progress.md` is the chronological, detailed live handoff log. Update it after each meaningful work slice.
- Keep this file updated every few implementation slices and whenever a major milestone, blocker, environment fact, or next action changes.

## Current phase and completion boundary

**Phase 1 is in progress.** Phase 0 capability discovery and gate recording are implemented. Current gate summary: **0 PASS, 2 PARTIAL, 14 BLOCKED**. This means the capabilities are recorded, not that all gates were accepted. Resolve is not running and no source media/song are in this workspace, so real G1/G2/live editing acceptance is not complete.

Implemented foundations:

1. Read-only capability probe: `scripts/phase0_probe.py`; outputs `capabilities.yaml`, `configs/capabilities.json`, and `plugin_registry.json`.
2. Pydantic contracts in `vanta/core/schemas.py`: EditSpec, EDG/source/shot/music, Style Capsule/Rule, Patch, Event, Job, RunManifest, QCResult.
3. Fail-closed EDG/patch policies in `vanta/core/policy.py`.
4. SQLite WAL run/event/job persistence and state transitions in `vanta/storage/event_store.py`.
5. Contained run artifact directories and atomic manifest in `vanta/storage/artifacts.py`.
6. Cooperative WATCH/STEP/TURBO checkpoint plus pause/resume/next/stop primitives in `vanta/live/run_control.py`.
7. One high-level Resolve boundary in `vanta/resolve/adapter.py`. `UnavailableResolveAdapter` never returns fake success. `ResolveScriptingAdapter` allows only read-only status/state calls via a separate process.
8. `vanta/resolve/broker_worker.py` uses a strict allowlist (`status`, `read_state`); no model text, `eval`, or arbitrary script execution.
9. `vanta/core/executor.py` includes fixed action allowlist, EDG/action binding, confidence/approval gates, run checkpoints, verified snapshot requirement, write → read-back comparison, rollback, and events. It has only test-double sequencing evidence; actual Resolve mutation methods do not exist yet.
10. Deterministic `vanta/compile/fcpxml.py` compiles a conservative single hard-cut video spine + rights-confirmed music lane to frame-addressed FCPXML. Rejects overlaps, extra video lanes, missing media, unconfirmed music rights, and overwrite. `scripts/compile_edg.py` is the CLI. XML shape is unit-tested; Resolve import is unverified.
11. FastAPI Mission Control v0 in `vanta/live/app.py` and `ui/mission_control.html`: capabilities/gates, read-only Resolve connection, event history, run artifact creation and run controls, plus a WebSocket event endpoint.
12. Setup/docs: `pyproject.toml`, `requirements.lock`, `scripts/setup.ps1`, `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `.env.example`, `.gitignore`.

Current test suite: **21 passed** under Python 3.12.10. Ruff passes and `compileall` passes. See `progress.md` for exact commands and chronology.

## Machine evidence (verify live before models)

Measured on 2026-10-09:

- Windows 11 Home Single Language, build 26200.
- DaVinci Resolve files: **21.1.1.10**.
- Resolve embedded runtime: **Python 3.14.4**; installed `DaVinciResolveScript.py` imports successfully.
- A standard **Python 3.12.10** was installed side-by-side for VANTA. Active environment is `D:\Vanta\.venv`.
- RTX 4050 Laptop: `nvidia-smi` reports **6141 MiB total / 4445 MiB free**, driver 610.47. Windows WMI under-reports this as 4 GiB; trust `nvidia-smi`.
- Main VANTA venv has no PyTorch/CUDA package yet. Earlier global Python 3.11 had CPU-only `torch 2.10.0+cpu`; capability report is now refreshed using Python 3.12, so it may show torch absent rather than CPU-only.
- System RAM: 11.8 GB total; only about 0.76 GB was free during initial discovery. Later report should be checked for current free RAM.
- Fixed disks C: and D: showed about 19.3 GB + 94 GB = **113.2 GB free** at latest probe, below user's stated 190 GB. Do not download large weights until actual target/cache storage is confirmed.
- Resolve is installed but **not running**. `scriptapp('Resolve')` reports “Resolve is not running or external scripting is not enabled for Local access.” Studio entitlement is unconfirmed.
- Resolve's bundled MCP manifest has **14 tools**, including `run_script`, `run_script_unsafe`, and LUT/DCTL writes/deletes; bundle license says Proprietary. VANTA does not expose this toolset to a model. `ResolveMCP.exe --dump-tools` was used to inspect only definitions.
- FFmpeg, Ollama, AirLLM were not installed/found in the initial app runtime. Do not auto-download models.

## AI/hardware decisions

- The user allows AirLLM; it remains optional. Current upstream AirLLM README describes layer-wise streaming and very large checkpoints, but streaming does not remove disk/RAM/latency costs. A September 2026 upstream Windows/NVIDIA issue reports 28.6 s/token on one 3B setup; benchmark locally before selecting it for an interactive creative director.
- No final model/provider has been selected. Need CUDA-enabled runtime, model cache/storage budget, and fixed quality/latency evaluation before choosing. Serialize heavy model jobs and Resolve AI/GPU work; never compromise edit gates to fit concurrent workloads.
- User asked for high quality. Be clear if the measured free disk/RAM limits prevent the chosen model from fitting or its latency is unusable; do not falsely claim “no compromise”. Prefer the manual's modular specialist architecture and retain a provider abstraction.
- AirLLM references already logged with URLs in `progress.md` and `docs/VANTA_DECISIONS.md`.

## Exact next work

**Priority correction (2026-10-09):** The user rejected the previous implementation as substantially flawed versus the manual. `docs/MANUAL_AUDIT.md` now lists the concrete gaps. Treat the repository as an audited prototype: correct contracts, policy, adapter protocol, exact timeline ownership, rollback semantics, timing, confidence gates, and tests before extending UI or models. Do not treat existing Phase 1 checkmarks as a live vertical slice.

**Corrective work completed in this context:** `vanta/core/timing.py` now rejects implicit mixed-rate rounding and same-track overlap; `vanta/core/confidence.py` implements 0.50/0.70/0.90 gates; `vanta/core/executor.py` uses logical timeline refs and source paths, exact predicates, VANTA markers, and no premature proxy action; `vanta/resolve/protocol.py`/`broker_worker.py` are a closed data protocol with mutations disabled by default; Mission Control no longer pretends inactive controls work. Runs have guarded lifecycle transitions. `vanta/resolve/gates.py` and `scripts/run_g1_gate.py` provide an explicit, opt-in five-clip G1 disposable-project evidence runner with rollback. Tests now show 31 passing, Ruff and compileall pass. None of that is a Resolve gate pass.

1. Add guarded run lifecycle transitions and a real executor attachment boundary; Mission Control must never show a control as effective until it is attached to a verified live run.
2. Build the disposable-project G1/G11/G16 harness around the closed broker protocol, controlled fixtures, a screen-recording option, and explicit preconditions. It must remain opt-in and never start Resolve or mutate the user's current project automatically.
3. When the user has opened Resolve Studio, enabled Local scripting, and selected/created a disposable project with controlled media, run G1 then G16 from that harness. Record actual API semantics (especially source out-frame and record frame) and only then change capability gate evidence.
4. Add FCPXML import G2 and only afterward design transition/speed, proxy render/poll/media-probe/QC steps behind their separate gates.
5. Complete the five clips + confirmed licensed song vertical slice after live gates pass. Then implement model scheduler/measurement before selecting a local AI provider, followed by the manual's perception → style → grade → Fusion → music → critic → learning sequence.

## Do not forget

- Update this `context.md` and `progress.md` together at major milestones and after environment/model decisions.
- Update `docs/VANTA_REQUIREMENTS.md`, `VANTA_IMPLEMENTATION_STATUS.md`, `capabilities.yaml`, and `CHANGELOG.md` together as applicable.
- Tests using an in-memory Resolve fake validate ordering only; never count them as G1–G16 passes.
- Do not start Resolve UI or perform a project mutation without a disposable project and verified rollback path.
- Do not remove or overwrite the manual or source media. There is no existing source tree to preserve apart from the manual.
