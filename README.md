# VANTA

VANTA is a local-first, supervised AI co-editor for **DaVinci Resolve Studio**. It learns an editing style from reference reels, reads your footage (one long camera roll or many clips), and builds a 30–45 s edit as a real, editable Resolve timeline. It cuts on the beat, grades to match the references (with camera-log decoding), adds titles and effects with a stated purpose, frames for 9:16, and adds music and SFX. It then checks its own render and repairs what fails.



## Status

Code-complete for the manual's pipeline. Automated tests, the synthetic end-to-end run and ruff all pass. **Live Resolve gates, model benchmarks and the perception calibration set have not been run yet**; they are deferred by request and listed with pass criteria in [`docs/DEFERRED_VERIFICATION.md`](docs/DEFERRED_VERIFICATION.md). Until a gate passes live, the matching Resolve operation uses its documented fallback.

## The app

A native Windows desktop application (PySide6), not a browser UI.

| Page | What it does |
| --- | --- |
| Home | Projects, recent runs, system readiness |
| Library | Reference reels (paste Instagram links, downloaded with built-in yt-dlp), footage, music and SFX. Per-clip camera colour profile (S-Log3, V-Log, C-Log3, LogC3, F-Log, D-Log, Apple Log, HLG, PQ). Flags and re-analyses analyses made by older VANTA versions |
| Style Brain | Learned style capsules, rule review queue, reference inspector |
| Create | Brief, sliders, **preflight** (run-time estimate and quality risks), then the **steer board**: which reels and techniques VANTA draws on, planned sequence, solved looks and music. Toggle any of them, re-plan, preview, build |
| Mission Control | Live build in Resolve (WATCH / STEP / TURBO), Pause / Step / Skip / Stop / Undo, before/after, flight recorder |
| Review | QC L0–L5, critic, chat to patch, final render, learn from your timeline edits |
| Transcript | Word-level text editing of dialogue |
| Learning / System | Preferences learned from corrections; Resolve gates, models, settings |

Launch it with `VANTA.exe` (Start Menu or Desktop shortcut), or run `.\.venv\Scripts\python.exe -m vanta.app`.

## How an edit is made

1. **Perception.** Shots and transitions, long takes split into 3–6 s *moments*, DIS optical-flow motion, matrix-correct colour statistics, faces, composition, OCR/typography, audio (BPM, beats, drops), Whisper words, and embeddings. Every footage moment is logged by the VLM in batches of 16.
2. **Style.** Observed and inferred capsules feed the Style Brain: rules induced from at least 8 shots across at least 3 reels with leave-one-reel-out checks, plus your approval queue.
3. **Plan.** The director (Qwen3-VL 8B with thinking) gets a *feature manual*: every title, transition, effect and its use/avoid conditions. Its proposals are policy-checked. Then the solvers run:
   - beat-grid slots;
   - a second-order Viterbi cut that never reuses a moment and spreads takes;
   - a relational grade solver (camera log decode → TECH CDL → LOOK, baked as a 33-point LUT, 65-point for log/HDR);
   - text placement;
   - effects with purposes;
   - speed ramps;
   - subject-follow reframing;
   - SFX.
4. **Build.** One closed, declarative protocol drives a persistent broker inside Resolve's own Python. Every write is read back and verified. Grades are verified by `ExportLUT` (< 1 ΔE). Fusion layers on a clip are merged into one active comp. A decorative layer that fails is reported and skipped, while structural failures roll back to a `.drp` snapshot.
5. **QC → repair.** Checks cover:
   - schema, timeline, frames, OCR and safe zones;
   - loudness and beat sync;
   - grading craft: skin line, shot-to-shot exposure and white balance, channel clipping, style distance.

   The critic turns findings into scoped patches, up to 3 rounds.
6. **Learn.** Your timeline corrections become reviewed preferences.

## Hardware budget

Designed for the guaranteed budget of an RTX 4050 (6 GB VRAM), 12 GB RAM and a 190 GB SSD:
- one heavy model resident at a time (`keep_alive 0`, scheduler leases);
- 16-bit pre-renders are streamed with two frames in memory;
- a long-footage edit is a roughly 2–7 h job, which the preflight card estimates before you start.

## Setup

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1      # venv, locked packages, CUDA PyTorch, ffmpeg, Ollama model store, shortcuts
.\scripts\build_app.ps1    # rebuilds VANTA.exe (PyInstaller launcher)
```

Models live under `data/models` (Ollama: `qwen3-vl:8b` director; Hugging Face: Whisper large-v3-turbo, DINOv2-small, SigLIP-base, CLAP, SAM 2.1). In Resolve, set *Preferences ▸ System ▸ General ▸ External scripting* to **Local**, and keep VANTA projects on **DaVinci YRGB** colour science. VANTA's LUTs include the camera decode, so a colour-managed project would decode twice; VANTA refuses to grade such projects and tells you why.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check vanta tests
```

## Documentation index

- [`ARCHITECTURE.md`](ARCHITECTURE.md): modules and data flow
- [`VANTA_IMPLEMENTATION_STATUS.md`](VANTA_IMPLEMENTATION_STATUS.md): what is done and what remains
- [`docs/DEFERRED_VERIFICATION.md`](docs/DEFERRED_VERIFICATION.md): live checks to run, with pass criteria
- [`docs/RESOLVE_GATE_RUNBOOK.md`](docs/RESOLVE_GATE_RUNBOOK.md), [`docs/RESOLVE_API_NOTES.md`](docs/RESOLVE_API_NOTES.md)
- [`docs/VANTA_SPEC.md`](docs/VANTA_SPEC.md), [`docs/VANTA_REQUIREMENTS.md`](docs/VANTA_REQUIREMENTS.md), [`docs/VANTA_DECISIONS.md`](docs/VANTA_DECISIONS.md)
- [`progress.md`](progress.md): chronological log; [`context.md`](context.md): recovery snapshot
