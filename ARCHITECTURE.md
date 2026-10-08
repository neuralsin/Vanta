# VANTA architecture

```text
References ─> Perception ─> Style Capsules ─> Style Brain ─┐
Footage ─────> Footage Knowledge Graph ────────────────────┤
Brief + controls ──────────────────────────────────────────┘
                              │
                              v
        Planner -> versioned EDG -> deterministic solvers
                              │
             schema -> policy -> confidence gates
                              │
       FCPXML / OTIO / LUT / CDL / DRX / Fusion templates
                              │
                    single Resolve Adapter
                              │
       read-back verification -> proxy -> QC / critic
                              │
                  scoped EDG patch -> review
                              │
      user corrections -> preference memory (reviewed)
```

All stages write structured events and versioned artifacts. SQLite is the system-of-record for runs, jobs, events, decisions and feedback. Resolve mutations use snapshots and new timelines. A fake adapter may test contracts but cannot satisfy a Resolve acceptance test.

See [docs/VANTA_SPEC.md](docs/VANTA_SPEC.md) for invariants and roadmap.
