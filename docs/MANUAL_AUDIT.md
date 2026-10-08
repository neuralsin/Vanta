# VANTA manual-conformance audit

Audited: 2026-10-09. Authority: `Resolve Autopilot — Final Build Manual v2.docx` in the repository root.

This audit replaces the earlier optimistic interpretation of the prototype. A passing unit test proves only the stated contract; it never proves Resolve behaviour, editorial quality, or a manual acceptance gate.

## Findings requiring correction before feature expansion

| Area | Earlier state | Manual requirement | Audit result | Correction direction |
| --- | --- | --- | --- | --- |
| EDG identity | `SourceMedia.id` was treated as a Resolve media ID by the executor. | EDG is a portable source-of-truth; Resolve is an execution target. | **Incorrect abstraction.** A source identifier cannot be assumed to be a Resolve media-pool ID. | Bind a source to one media-pool item by canonical source path only, reject absent or ambiguous matches, and retain the source ID in the EDG. |
| Timeline safety | The executor had a generic `rollback(snapshot_id)` interface but no ownership proof. | Never modify the original project/timeline; work on new timeline versions; snapshot before major work. | **Unsafe design.** A generic restore could mutate unrelated work, and an exported `.drp` is backup evidence rather than automatic restoration. | Snapshot baseline timeline IDs/current selection and export a `.drp`; rollback may delete only exact timeline IDs created during this VANTA run and restore the baseline selection. |
| Gate semantics | Capabilities stated that adapter methods existed while gates were blocked. | Every write requires version/gate evidence and read-back verification. | **Overclaimed scaffolding.** Method presence is not a live capability. | Split installed, protocol-tested, and live-gate-passed capability states. Keep mutators unavailable until live acceptance evidence exists. |
| EDG timing | Timeline placement was weakly validated; the compiler silently used different time domains. | Deterministic solvers own timing and record exact decisions. | **Insufficient invariant.** Mixed source/timeline rate conversion and gaps need an explicit policy. | Validate timeline placement, source bounds, per-shot timeline duration, overlap policy, and exact rate conversion before compile/execute. |
| Executor | In-memory fake verified a few calls but did not model media resolution, ownership, idempotence, breadcrumbs, or rollback safety. | Plan → validate → policy → adapter → read-back; every action emits event and breadcrumb. | **Incomplete.** | Model input/output invariants for each fixed adapter operation; add protocol-faithful fakes, idempotence tests, ownership tests, and failure-injection tests. |
| Confidence/autonomy | Thresholds and approval handling were partial; `AUTO` was not tied to the manual's 0.90 / 0.70 / 0.50 bands. | Confidence gates are calibrated starting values, ASSISTED is default, MANUAL never writes without approval. | **Incomplete.** | Centralise confidence classification and policy; record the gate decision in events. |
| Live visibility | The UI showed a basic feed but did not expose a real plan checklist, skipped steps, decision evidence, undo eligibility, run artifacts, thumbnails, or stage state. | Mission Control is a first-class live subsystem with Plan, log, confidence, GPU/VRAM, Pause/Step/Stop/Undo and before/after evidence. | **Prototype only.** | Build from structured run/step records after executor semantics are correct. Do not present controls as operational before live adapter gates pass. |
| Project structure | No explicit stage naming/ownership policy was enforced. | `AI_EDIT / ASSEMBLY_V1 / COLOR_V1 / FUSION_V1 / FINAL_CANDIDATE / APPROVED`, versioned, traceable. | **Missing.** | Validate VANTA-owned stage names and immutable baseline binding; reject arbitrary timeline names. |
| Render/QC | `render_proxy` existed in the interface despite no verified Resolve render settings, poll, watchdog, media probe, or QC pipeline. | Queue/poll low-res proxy, validate L0–L5, then final render only after approval. | **Incorrectly premature.** | Remove proxy from executable mutator allowlist until render configuration and verification are fully specified and tested. |
| Model stack/scheduler | Only a hardware probe exists. | Specialist stack, single GPU lease, explicit job state, measured G9 performance, no concurrent Resolve AI and model work. | **Not implemented.** | Implement the deterministic scheduler and measurement harness before selecting or downloading a heavy model; AI provider work comes after the safe vertical slice. |
| Perception/style/learning | Schemas exist, but no calibrators, evidence generation, profile isolation, retrieval, rule induction, correction capture, held-out evaluation, or review queue exists. | These are separate phases after a working base editor. | **Schemas only.** | Keep these phases explicitly unimplemented; build them after Phase 1 is accepted, not as empty endpoints. |

## Corrections applied after this audit

- The EDG now rejects overlapping primary-track plans and nonintegral source-to-timeline frame conversion instead of silently changing timing.
- The executor uses logical timeline references and EDG source paths; it no longer treats a source ID as a Resolve media ID, and it no longer declares proxy rendering executable.
- The closed Resolve broker protocol validates all field names and values before a Resolve connection. Mutations are disabled by default.
- Snapshot/rollback semantics now require baseline identity plus exact IDs for VANTA-created timelines. The implementation still requires disposable-project live tests before it can be enabled.
- Mission Control does not report inactive executor controls as accepted actions.

## What remains sound enough to retain

- The product is local-first, uses a single Resolve boundary, and rejects raw model code.
- The read-only capability probe provides useful installation and hardware evidence when labelled as discovery only.
- SQLite WAL, run artifacts, Pydantic contracts, and a local web surface are suitable foundations but need stronger semantics.
- The FCPXML compiler is a conservative interchange prototype. It cannot be treated as Resolve-verified until G2 is exercised.

## Corrected build order

1. Correct contracts, policy, adapter protocol, snapshot ownership, executor semantics, event records, and their tests.
2. Run live G1, G11, G16 first in a disposable project with controlled media; then G2/G12/G13 as their compilers become available.
3. Complete the five-clip-plus-licensed-song watched assembly, proxy, verification, and rollback slice.
4. Add measurement/scheduler infrastructure and only then evaluate a local model provider against a fixed quality/latency suite.
5. Implement perception, calibration, Style Brain, grade, text/masks, music, critic/repair, learning, and complete UI screens in the manual's order.

## Non-negotiable interpretation rules

- A code path, fake, installed executable, or successful API return is never a manual gate pass without read-back evidence.
- A Resolve project export is a recovery artifact. It does not authorize importing/restoring over a user's project automatically.
- Any destructive Resolve operation must have exact VANTA-run ownership proof and must be covered by failure-injection tests before it is enabled.
- The AI may propose schemas and parameters; deterministic code validates, computes, applies, and verifies them.
