# VANTA architecture

```text
Reference reels ─> Perception ─> Style Capsules ─> Style Brain ───────┐
Footage (long takes -> moments) ─> Footage Knowledge Graph ───────────┤
Brief + sliders + preflight ──────────────────────────────────────────┘
                                   │
          Director (Qwen3-VL, thinking, feature manual)  ──>  intent only
                                   │
   deterministic solvers: slots · Viterbi cut (variety) · grade (log decode
   -> CDL -> look LUT) · text · effects · ramps · subject-follow · SFX
                                   │
                      EDG v2 (versioned source of truth)
                                   │
               schema -> policy -> confidence gates -> steer board
                                   │
   pre-renders (16-bit ramps / follow)   Fusion comps (chained per clip)   LUTs
                                   │
           single Resolve adapter -> persistent broker (closed protocol)
                                   │
        read-back verification · ExportLUT ΔE · isolated layer failures
                                   │
          proxy render -> QC L0-L5 -> critic -> scoped patches (<= 3)
                                   │
                corrections -> reviewed preference memory
```

## Packages

| Package | Responsibility |
| --- | --- |
| `vanta/analysis` | probe and **matrix-correct proxies** (BT.601 re-encode so OpenCV reads true RGB), signals (DIS flow), shots and moments (`split_long_takes`), motion, colour, faces/composition/quality, OCR, audio, Whisper, embeddings, SAM 2.1 mattes, effect detectors, pipeline (versioned cache, `is_outdated`) |
| `vanta/core` | EDG v2 schemas, colour maths (`correct_decoded`, ΔE2000), `camera_log` (log/HDR decode + filmic tonemap), policy, confidence |
| `vanta/style` | capsules, Style Brain, rule induction, retrieval |
| `vanta/reasoning` | LLM provider (Ollama, schema JSON, thinking with retry), prompts, **`features.py`** (director's feature manual), footage graph (unique labels, take-diverse showcase), director, planner, effects plan, critic, patches, NL, replan |
| `vanta/solvers` | cut (slots, Viterbi + global variety repair), music, grade (+ LUT size by profile), grade maths, LUT I/O, text, text-edit, **`reframe.py`** (subject track, operator smoothing, follow keys) |
| `vanta/compile` | Fusion template registry and renderer, **`chain_comps`** (one comp per clip), FCPXML |
| `vanta/resolve` | closed protocol (VANTA_-prefixed names, output-root confinement), broker worker, adapter, gate ledger (G10 drift guard), gate runner, edit executor (comp chains, colour-science guard, layer isolation, undo) |
| `vanta/render` | preview (16-bit RGB chain, Fusion effect stand-ins, keyed follow crop, Rec.709-tagged output), pre-render (16-bit ramps + follow, source colour tags), overlay track |
| `vanta/qc` | L0 schema, L1 timeline, L2 frames, L3 audio, L4 style and grading craft (skin line, continuity, channel clipping) |
| `vanta/workers` | job runner, `VantaService` (every stage as a job), **`preflight.py`** |
| `vanta/app` | PySide6 desktop app, pages, widgets, HUD server |
| `vanta/learning` | timeline-diff corrections, preference mining |

## Invariants

- Resolve is only driven through the closed protocol, and writes are read back and verified. VANTA never overwrites a user timeline; it creates new `VANTA_*` timelines after a `.drp` snapshot.
- Colour:
  - every frame VANTA measures is decoded with the file's true Y'CbCr matrix and range;
  - every video VANTA writes is explicitly Rec.709-tagged, or carries the source's tags for pre-renders.
- Camera log/HDR is decoded once, inside the shot LUT, ahead of the look. Projects must use DaVinci YRGB.
- Fusion: one active VANTA comp per clip, layered as picture effects → titles → transition halves → letterbox.
- Mattes are solved on source frames, so shots with mattes are never speed-ramped or follow-reframed.
- The model proposes intent. Solvers produce numbers, and policy, confidence and QC decide what ships.
