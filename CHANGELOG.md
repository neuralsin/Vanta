# Changelog

## 0.1.0 — 2026-10-09

- Added manual-derived architecture, requirements, decisions, and implementation status documentation.
- Added read-only Phase 0 capability probe and gate report generation.
- Added Pydantic v2 contracts for EditSpec, EDG, Style Capsule/Rule, Patch, Event, Job, RunManifest, and QCResult.
- Added SQLite WAL run/event/job persistence and guarded job-state transitions.
- Added conservative EDG/patch policy checks and a single Resolve adapter boundary with an honest unavailable default.
- Added Mission Control v0 for gate evidence and event history.
- Added initial contract, event-store, and policy tests.
- Added WATCH/STEP/TURBO run checkpoints and cooperative pause/resume/next/stop controls.
- Added a fixed read-only Resolve Python 3.14 broker and Mission Control connection status.
- Added deterministic EDG-to-FCPXML assembly compilation and plan execution/rollback sequencing contracts.
- Added exact Python dependency lock installation and refreshed Phase 0 evidence under the supported Python 3.12.10 environment.
- Added run/executor and compiler coverage; current suite has 21 passing tests, with Ruff and compileall clean. Live Resolve remains unverified.
- Audited the prototype against the authoritative manual and corrected unsafe EDG-to-Resolve identity, implicit frame rounding, confidence policy, rollback ownership, protocol allowlisting, run lifecycle, and inactive UI-control claims.
- Added a closed embedded-Python Resolve protocol and an opt-in disposable-project G1 harness. The normal app leaves its mutation path disabled.
- Expanded deterministic contract coverage to 31 passing tests; live Resolve gates remain unverified.
