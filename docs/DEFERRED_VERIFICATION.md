# Deferred verification checklist (run at the end of the build)

Per the user's instruction, local benchmarks, model inference trials and live
Resolve runs are deferred until the full product is built. Each item lists what
to run and the pass criterion. Tick items only with recorded evidence.

## Models / GPU (G9)

- [ ] **Director VLM speed + quality** — `qwen3-vl:8b` (Ollama, pulled, 6.1 GB) on RTX 4050 6 GB: one image + schema call; record seconds, tokens/s, peak VRAM/RAM. Fall back to `qwen3-vl:4b` if > ~3 min per planning pass. Compare thinking vs non-thinking for critique.
- [ ] **AirLLM experiment** — only if a larger director is wanted; measure s/token on this machine before adopting.
- [ ] **Whisper large-v3-turbo** — word timestamps on a 60 s dialogue clip, int8_float16 on CUDA; time + WER sanity.
- [ ] **DINOv2 / SigLIP / CLAP** — embedding throughput per 100 frames / 10 tracks; check VRAM release after the lease (scheduler unload).
- [ ] **SAM 2.1 video masks** — 10 s handheld subject clip: chunked propagation (CHUNK=48) peak VRAM < 5 GB, temporal IoU, edge halo < 1 px (G6).
- [ ] **SAM 3 (gated)** — only after HF access approval; text-prompt masks vs SAM 2.1.
- [ ] **YuNet** face detector loads; Haar fallback parity.
- [ ] **Stage timings** on 3 sample reels vs manual budgets (reference 1–3 min/30 s reel, footage 2–5 min/raw minute, planning 1–3 min, grade solve ~5 s/shot, proxy+QC 1–3 min).

## Perception calibration (Phase 2 acceptance)

- [ ] Run `make_calibration_set` (10 known looks, reel-style re-encode) on the user's own footage; estimators recover look direction/magnitude.
- [ ] Shot detection agreement with PySceneDetect on 5 real reels.
- [ ] OCR + typography on 5 real reels with captions.

## Live DaVinci Resolve Studio 21.1.1 gates (disposable `VANTA_GATE_*` project)

- [ ] G1 build (import, timeline, source-frame trims, trackIndex, markers, idempotent rerun) — calibrate inclusive/exclusive `GetSourceEndFrame`.
- [ ] G2 FCPXML import with cuts + cross-dissolve.
- [ ] G3 / G3b Fusion text template + OCR round trip; text behind subject.
- [ ] G4 DRX/CDL/LUT + `ExportLUT` < 1 ΔE.
- [ ] G5 / G6 external mattes via `AddClipMattesToMediaPool`.
- [ ] G8 word-level text edit, A/V sync.
- [ ] G10 version guard; G11 live-follow (18/20 visible < 1 s); G12 `AddTransition`; G13 `SetSpeed`; G14 camera solve import; G15 registry scan; G16 failure injection.
- [ ] In-Resolve HUD script (Workspace ▸ Scripts) polls the app and Stop works.
- [ ] Proxy render job → poll → ffprobe verification → QC.

## End-to-end acceptance ladder

- [ ] Level 1: 5 reference reels + 10 clips + 1 licensed song → capsules, footage graph, 20–30 s editable timeline, grade, captions, proxy.
- [ ] Level 2–6: compositing, spatial/rhythm, self-correction, learning (edit 01 → corrections → edit 02 closer), visibility.
- [ ] Packaged app (`VANTA.exe`) launches on a clean user profile.
