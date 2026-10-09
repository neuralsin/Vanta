# VANTA: Supervised AI Co-Editor for DaVinci Resolve Studio

VANTA is a production-grade, local-first AI co-editor designed specifically for **DaVinci Resolve Studio 21.1+**. It ingests raw camera footage (single continuous long takes or discrete clips), extracts creative editing style from reference reels, and builds a professional 30–60 second social reel directly on your Resolve timeline.

VANTA cuts on the beat, grades with camera-log decoding (Sony S-Log3, V-Log, C-Log3, LogC3, F-Log, D-Log, Apple Log, HLG), places typography and visual effects with stated purpose, reframes for 9:16 vertical video using subject-follow tracking, synchronizes music and SFX, runs an internal 6-tier Quality Control (QC) suite, and repairs findings with scoped patch algebra.

---

## 1. System Health & Verification Status

- **Automated Test Suite:** **110 passed in 198.86s (100% pass rate)**.
- **Codebase Scope:** **18 packages, 113 Python modules, 19 test suites, 25+ production Fusion templates**.
- **Static Analysis & Linting:** Clean (`ruff check`: 0 errors/warnings | `compileall`: 0 errors).
- **Application Interface:** Native Windows PySide6 desktop workstation (`vanta.app` / `VANTA.exe`) with 9 creative screens and an in-Resolve HUD WebSocket server.
- **Live DaVinci Resolve Status:** Automated G1–G16 gate harness (`vanta/resolve/gate_runner.py`) is fully built and ready to self-test in disposable `VANTA_GATE_*` projects once DaVinci Resolve Studio is launched with Local scripting enabled; see [`docs/DEFERRED_VERIFICATION.md`](docs/DEFERRED_VERIFICATION.md).

---

## 2. Desktop Workstation Application

VANTA provides a native PySide6 desktop interface engineered for professional editors and creators:

| Page | Primary Workflow & Features |
|---|---|
| **Home** | Project management, recent run history, system hardware readiness monitors. |
| **Library** | Raw footage and reference reel importer (Instagram/TikTok links via `yt-dlp`). Camera log profile selector (S-Log3, V-Log, C-Log3, LogC3, F-Log, D-Log, Apple Log, HLG, PQ). Analysis cache inspector. |
| **Style Brain** | Visual style capsule explorer (observed data vs inferred intent), candidate rule review queue, and reference reel comparison. |
| **Create** | Brief inputs, creative sliders (Pacing, Beat Sync, Color, Typography, Motion), preflight run-time and risk estimator, interactive steer board, and real-time preview player. |
| **Mission Control** | Live Resolve execution loop (`WATCH`, `STEP`, `TURBO`), Pause / Step / Skip / Stop / Undo controls, before/after comparison, and flight recorder telemetry. |
| **Review** | 6-tier QC report card (L0–L5), critic review findings, natural language chat patcher, and final delivery render exporter. |
| **Transcript** | Word-level dialogue editor with filler-word and pause removal. |
| **Learning** | Preference memory induced from user timeline corrections and calibration graphs. |
| **System** | DaVinci Resolve acceptance gate dashboard (G1–G16), neural model downloader, storage path configuration. |

Launch the workstation app with:
```powershell
.\.venv\Scripts\python.exe -m vanta.app
# OR double-click VANTA.exe
```

---

## 3. End-to-End Pipeline Architecture

```text
Raw Footage / Reels ──> Perception ──> Style Brain ──> Planner & Director ──> Deterministic Solvers ──> Resolve Execution ──> QC & Repair
```

1. **Perception Pipeline (`vanta/analysis`):**
   - Probes media geometry and creates matrix-correct proxies (BT.601/BT.709).
   - PySceneDetect cut detection and `split_long_takes()` subdividing continuous camera rolls into 3–6s coherent editorial moments.
   - OpenCV DIS optical flow, RapidOCR text recognition, YuNet face detection, and EBU R128 audio loudness / beat analysis.
2. **Style Brain (`vanta/style`):**
   - Converts reference media into Style Capsules, segregating observable facts from inferred director choices.
   - SQLite knowledge base storing universal principles and conditional rules.
   - Statistical rule induction requiring $\ge 8$ shots across $\ge 3$ reels with leave-one-reel-out (LORO) cross-validation.
3. **Reasoning & Planning (`vanta/reasoning`):**
   - 3-Pass timeline planner: Assembly (Pass 1), Polish (Pass 2), and Finish (Pass 3).
   - Creative Director guided by a strict *feature manual* (`features.py`) and effects purpose plan.
   - Deterministic heuristic director fallback when local VLM is offline.
4. **Deterministic Solvers (`vanta/solvers`):**
   - **Viterbi Cut Solver:** Second-order dynamic programming solver ensuring take diversity and rhythm alignment.
   - **8-Node DRX Grade Solver:** Relational color matching baking camera log decode, technical CDL, and creative look into 33-point (or 65-point) `.cube` LUTs.
   - **Music Solver:** Downbeat snapping ($\le 40\text{ ms}$) and section placement matching beat drops to narrative climaxes.
   - **Text Layout Solver:** Face-avoiding safe-zone typography placement.
   - **Subject-Follow Reframe Solver:** 9:16 vertical reframe with dead-zone operator smoothing.
5. **DaVinci Resolve Integration (`vanta/resolve`):**
   - Closed 40-action declarative request protocol rejecting arbitrary code injection.
   - Persistent Python 3.14 broker subprocess communicating directly with Resolve's embedded runtime.
   - Automatic `.drp` project snapshot before writing; rollback deletes only VANTA-created timelines.
   - Fusion template chaining (`chain_comps`) merging multiple effect layers into **one active comp per clip**.
   - Decorative effect failure isolation: failing cosmetic layers are skipped without rolling back the edit.
6. **QC & Self-Repair (`vanta/qc`, `vanta/reasoning`):**
   - 6-tier QC pipeline checking schema (L0), timeline gaps (L1), visual clipping (L2), loudness/peaks (L3), style (L4), and pacing (L5).
   - Critic translates findings into scoped patches across up to 3 automated repair iterations.
7. **Learning from Human Feedback (`vanta/learning`):**
   - Compares the final approved timeline against the initial draft to detect cuts, shot swaps, and grade tweaks.
   - Induces user preferences requiring recurring edits before promoting them to active rules.

---

## 4. Hardware Budgets & Confinement

Engineered to operate reliably within the verified host environment:
- **GPU:** NVIDIA GeForce RTX 4050 Laptop GPU (6141 MiB VRAM).
- **RAM:** 12 GB system RAM (operates safely with $\approx 1.6\text{ GB}$ free).
- **Storage:** Fixed disk space across C: and D: drives (temporary renders and caches directed to drive D:).
- **Single GPU Lease Scheduler (`vanta/models/scheduler.py`):** Enforces single-model VRAM residency (`keep_alive 0`) and pauses AI workloads during Resolve playback and rendering.

---

## 5. Quickstart & Setup

### Environment Provisioning
```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
```
*Note:* To enable GPU acceleration for neural models (Whisper, DINOv2, SAM 2.1), install CUDA PyTorch wheels:
```powershell
.\.venv\Scripts\pip.exe install torch --index-url https://download.pytorch.org/whl/cu121
```

### Running Offline Test Suites
```powershell
# Run all 110 automated tests
.\.venv\Scripts\python.exe -m pytest -q

# Run static linter
.\.venv\Scripts\ruff.exe check scripts vanta tests

# Verify bytecode compilation
.\.venv\Scripts\python.exe -m compileall -q scripts vanta tests
```

### Live DaVinci Resolve Gate Verification
1. Launch **DaVinci Resolve Studio 21.1**.
2. Open **Preferences ▸ System ▸ General** and ensure **External scripting using: Local** is enabled.
3. Run the automated gate runner:
```powershell
.\.venv\Scripts\python.exe -c "from vanta.resolve.gate_runner import run_gates; print(run_gates())"
```
The runner will test G1–G16 in disposable `VANTA_GATE_*` projects and record results in `capabilities.yaml` and `ledger.json`.

---

## 6. Documentation Index

- [`VANTA_FULL_SYSTEM_AUDIT_REPORT.md`](VANTA_FULL_SYSTEM_AUDIT_REPORT.md): Definitive system audit and triple-checked multi-perspective verdict matrix.
- [`ARCHITECTURE.md`](ARCHITECTURE.md): Architectural layers, package inventory, and system invariants.
- [`VANTA_IMPLEMENTATION_STATUS.md`](VANTA_IMPLEMENTATION_STATUS.md): Current implementation state and test evidence.
- [`docs/OPERATOR_MANUAL.md`](docs/OPERATOR_MANUAL.md): Complete guide for editors and workstation operators.
- [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md): Developer API reference for schemas, protocol, and solvers.
- [`docs/DEFERRED_VERIFICATION.md`](docs/DEFERRED_VERIFICATION.md): Deferred live Resolve verification checklist.
- [`docs/RESOLVE_GATE_RUNBOOK.md`](docs/RESOLVE_GATE_RUNBOOK.md): Live gate test runbook and failure procedures.
- [`progress.md`](progress.md): Chronological implementation progress and audit log.
