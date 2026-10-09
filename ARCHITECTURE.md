# VANTA System Architecture

```text
Reference Reels ──> Ingest (yt-dlp) ──> Perception ──> Style Capsules ──> Style Brain ────────┐
                                                                                               │
Raw Footage ────> Ingest ──> Long-Take Moments (3-6s) ──> Multimodal Footage Graph ───────────┤
                                                                                               │
User Brief + Sliders ────> Preflight (Cost & Risk Assessment) ─────────────────────────────────┘
                                       │
                Creative Director (Qwen3-VL 8B / Heuristic Fallback)
                     with Feature Manual Gating & Effects Plan
                                       │
                               Intent Proposals
                                       │
                      Deterministic Engineering Solvers
    ┌──────────────────────────────────┼──────────────────────────────────┐
    │                                  │                                  │
Viterbi Cut Solver             8-Node DRX Grade Solver             Music Beat Solver
(Take-Diversity & ASD)        (Camera Log Decode + 33/65p LUT)    (Drop Snap <= 40ms)
    │                                  │                                  │
Text Layout Solver             Subject-Follow Reframe             Fusion Comp Chainer
(Saliency & Face Margins)     (9:16 Dead-Zone Smoothing)         (1 Active Comp / Clip)
    └──────────────────────────────────┼──────────────────────────────────┘
                                       │
                        Edit Decision Graph (EDG v2)
                    (Versioned JSON Source of Truth)
                                       │
        Fail-Closed Policy Validation & Three-Tier Confidence Gate
        (Reject < 0.50 │ Require Approval 0.50-0.70 │ Auto >= 0.90)
                                       │
                            Interactive Steer Board
                                       │
                       DaVinci Resolve Adapter Layer
                (Subprocess Broker in Python 3.14 Runtime)
                                       │
                     Closed 40-Action Declarative Protocol
                 (Breadcrumbs · Read-Back Verify · Undo Revert)
                                       │
           Resolve Execution Loop (WATCH / STEP / TURBO Modes)
           Isolated Decorative Failures │ Structural Rollback to .DRP
                                       │
          Internal 16-Bit RGB Preview Render (Rec.709 Tagged)
                                       │
          Automated 6-Tier Quality Control (Levels L0 through L5)
                                       │
             Critic Feedback Loop ──> Scoped Patch Algebra (<= 3 Rounds)
                                       │
          Timeline Difference Detection ──> Reviewed Preference Memory
```

---

## 1. Subsystem Directory Structure & Responsibilities

| Package | Purpose & Primary Responsibilities |
|---|---|
| [`vanta/core`](file:///d:/Vanta/vanta/core) | **Contracts, Timing & Color Science:** Pydantic v2 schemas (`extra="forbid"`), rational broadcast timing (`fractions.Fraction`), transfer functions for 9 camera log profiles, CIE $\Delta E_{00}$ color transforms, confidence bands, fail-closed policy validation, and logical timeline sequencing. |
| [`vanta/storage`](file:///d:/Vanta/vanta/storage) | **Data Persistence:** SQLite WAL event store for runs, events, jobs, media items, style capsules, and rules. Thread-safe reentrant locks (`RLock`), busy timeouts, foreign keys, and atomic artifact writers. |
| [`vanta/ingest`](file:///d:/Vanta/vanta/ingest) | **Media Ingest & Reference Downloader:** Fast `yt-dlp` wrapper for reference reel ingestion with strict cookie handling (references purged post-analysis per Manual Section 21) and multi-format footage import. |
| [`vanta/analysis`](file:///d:/Vanta/vanta/analysis) | **Perception Pipeline:** FFmpeg/ffprobe wrappers, matrix-correct proxies (BT.601/BT.709), PySceneDetect cut detection, `split_long_takes()` into 3–6s moments, OpenCV DIS optical flow, RapidOCR with memory guard, YuNet face detection, and EBU R128 audio loudness / beat analysis. |
| [`vanta/style`](file:///d:/Vanta/vanta/style) | **Style Brain:** Constructs Style Capsules (separating `observed` facts from `inferred` director intent), SQLite profile manager, and statistical rule induction ($\ge 8$ shots across $\ge 3$ reels with leave-one-reel-out cross-validation). |
| [`vanta/reasoning`](file:///d:/Vanta/vanta/reasoning) | **Reasoning & Planning:** 3-Pass timeline planner (Assembly, Polish, Finish), VLM / heuristic creative director, director's feature manual (`features.py`), narrative effects planning, scoped patch algebra, and automated critic. |
| [`vanta/solvers`](file:///d:/Vanta/vanta/solvers) | **Deterministic Solvers:** Second-order Viterbi cut solver with variety repair, 8-node DRX / LUT grade solver, downbeat-aligned music sync ($\le 40\text{ ms}$), face-avoiding typography layout, dialogue editing, and subject-tracking 9:16 reframe. |
| [`vanta/compile`](file:///d:/Vanta/vanta/compile) | **Fusion Templates & XML:** Registry and compiler for 25+ vetted Fusion templates. Implements `chain_comps()` to merge multiple effect layers into 1 active comp per clip. Deterministic FCPXML 1.10 sequence compiler. |
| [`vanta/resolve`](file:///d:/Vanta/vanta/resolve) | **DaVinci Resolve Integration:** Closed 40-action declarative request protocol, isolated Python 3.14 subprocess broker, high-level adapter with read-back verification and `.drp` snapshots, edit executor (WATCH/STEP/TURBO), automated G1–G16 gate runner, and live capability ledger. |
| [`vanta/render`](file:///d:/Vanta/vanta/render) | **Internal Timeline Renderer:** Standalone 16-bit RGB timeline renderer with Rec.709 tagging, speed ramps, and -14 LUFS loudness normalization; 16-bit pre-renders; diagnostic safe-zone overlays. |
| [`vanta/qc`](file:///d:/Vanta/vanta/qc) | **Automated Quality Control:** 6-tier QC pipeline checking schema (L0), timeline gaps (L1), visual clipping/safe zones (L2), audio loudness/peaks (L3), style fidelity (L4), and editorial pacing (L5). |
| [`vanta/learning`](file:///d:/Vanta/vanta/learning) | **Learning & Preferences:** Timeline difference analyzer detecting user cuts, shot swaps, and grade tweaks; preference memory requiring recurrence before candidate promotion. |
| [`vanta/models`](file:///d:/Vanta/vanta/models) | **Model Scheduler:** Single GPU lease scheduler ensuring only one heavy AI model is resident in VRAM, unloading before Resolve render/playback to respect the 6.1 GB VRAM budget. |
| [`vanta/workers`](file:///d:/Vanta/vanta/workers) | **Service & Preflight:** Pre-flight run-time estimator, quality risk assessor, and asynchronous job worker service. |
| [`vanta/app`](file:///d:/Vanta/vanta/app) | **Desktop Creative Workstation:** Native PySide6 application with 9 production screens, dark theme, and an in-Resolve HUD WebSocket server. Packaged as `VANTA.exe`. |

---

## 2. Non-Negotiable System Invariants

1. **DaVinci Resolve Isolation & Verification:**
   - Resolve is driven **only** through the closed declarative protocol. No arbitrary scripts, `eval()`, or `exec()` are permitted.
   - Every write operation is followed immediately by a read-back check. If read-back state does not match expected state, the operation is flagged or rolled back.
   - VANTA never mutates or overwrites user timelines. It creates unique `VANTA_*` timelines and takes a `.drp` project snapshot before writing. Rollback deletes only timelines created by the current VANTA run.
2. **Color Science Integrity:**
   - Every frame measured by VANTA is decoded using its source file's true matrix (BT.601, BT.709, or BT.2020) and range.
   - All internal renders are explicitly tagged with Rec.709 color space and transfer function.
   - Camera log/HDR decoding occurs **once** within the shot LUT ahead of creative CDL adjustments. VANTA enforces DaVinci YRGB color science and rejects color-managed projects to prevent double-decoding.
3. **Fusion Composition Chaining:**
   - Exactly **one active VANTA `.comp`** is attached per clip. Multiple layers (visual effects, titles, transitions, letterbox) are chained sequentially between `MediaIn1` and `MediaOut1` via `chain_comps()`.
   - Decorative effect failures are isolated: if a title or bloom effect fails to bind, the effect is skipped and logged, but the underlying cut edit is preserved without rolling back.
4. **VRAM Leasing & Hardware Confinement:**
   - AI models strictly share a single GPU lease. Only one heavy neural model resides in VRAM at any moment (`keep_alive 0`), leaving VRAM free for DaVinci Resolve rendering and neural processing.
5. **AI Proposes Intent — Solvers Produce Math:**
   - Language models and VLMs propose high-level creative intent (ASD pacing, color temperature, shot selections).
   - Deterministic solvers calculate exact cut frames, 33/65-point 3D LUT matrices, beat snap timings, and bezier reframing curves.
   - Confidence policy and automated QC decide whether an edit is applied automatically, held for user approval, or rejected.
