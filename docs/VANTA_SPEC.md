# VANTA implementation specification

This is the implementation index distilled from **Resolve Autopilot — Final Build Manual v2**. The DOCX remains authoritative where a detail is not restated here.

## Product boundary

VANTA is a local-first, supervised co-editor for DaVinci Resolve Studio 21.1+. It produces editable timelines and artifacts. It does not promise perfect creative judgment or replace the editor's final approval. No model may directly mutate Resolve or execute arbitrary Python/Fusion code.

## System flow

References → perception → evidence-based Style Capsules → profile-isolated Style Brain → footage knowledge graph → planner → versioned Edit Decision Graph (EDG) → deterministic solvers → schema/policy/confidence gates → compilers → single Resolve Adapter → read-back verification → proxy render → QC/critic → scoped EDG patches → approved candidate. Corrections become reviewed preference evidence.

## Invariants

- EDG is the source of truth; Resolve is an execution target.
- Every write is planned, validated, policy checked, executed, read back, and verified.
- New timelines and snapshots protect source projects and media.
- All Resolve calls live behind one adapter; unverified version capabilities are refused.
- Fusion comes only from vetted, versioned templates with validated parameters.
- Decisions retain evidence, provenance, confidence, conditions, and approval state.
- Deterministic code handles timing, scoring, transforms, validation, LUT math, and alignment; models interpret and rank.
- A persistent event ledger and local Mission Control expose progress, artifacts, decisions, failures, and controls.
- Model providers are replaceable. A single GPU lease serializes heavy inference and Resolve AI work.

## Contracts

Versioned schemas: EditSpec, EDG, StyleCapsule, StyleRule, Patch, Event, Job, RunManifest, QCResult. SQLite is the structured source of truth; run folders hold reproducible, content-addressed artifacts; `kb/` is human-readable and versioned.

## Resolve policy

Target Studio 21.1 baseline; record exact Resolve version and gate ledger. Version-specific differences stay in the adapter. Use the documented MCP or scripting API only after capability gates pass. Prefer AppendToTimeline/FCPXML for structure, DRX templates plus generated LUT/CDL for color, and Fusion templates for graphics. Never claim completion on an API success alone.

## Build roadmap

0. Environment/capability detection and gates G1–G16.
1. EDG, Resolve adapter, persistent events/jobs, snapshots/rollback, Mission Control and a real five-clip-plus-song Resolve slice.
2. Perception and calibration.
3. Style Capsules and Style Brain with review/approval.
4. Export-verified grade engine.
5. Text/masks/Fusion template library.
6. Licensed music indexing and beat alignment.
7. Planner, structured critic, scoped repair.
8. User-facing local web UI.
9. Correction learning and hardening.

## Acceptance ladder

Foundation: five reference reels + ten clips + one licensed song → explainable style/footage analysis → 20–30 second editable Resolve timeline → verifiable grade, captions, proxy and rollback. Then compositing, spatial/rhythm tools, bounded self-repair, measurable preference learning, and visible controls.

## Hardware policy

Hardware is discovered, not inferred from a requested spec. Keep heavy jobs sequential, show model residency/VRAM, and do not auto-download models. On this machine, use low-memory deterministic analysis first; only enable a model after measured RAM/VRAM/time tests. AirLLM is an optional provider experiment, not a guarantee of high-quality fast vision-language reasoning.
