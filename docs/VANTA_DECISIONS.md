# VANTA implementation decisions

## 2026-10-09 — initial repository audit

- **Start from the manual, not a pre-existing implementation.** `D:\Vanta` initially contains only the authoritative v2 DOCX; there is no source code, test suite, or Git history.
- **Use a measured gate ledger.** Resolve can be installed while untested. `capabilities.yaml` therefore records installed facts separately from `PASS` integration gates. Unknown behavior remains blocked and the manual's fallback is recorded.
- **Keep Phase 0 probes side-effect-light.** The probe reads installation/hardware metadata and inventories paths; it does not launch Resolve, install plugins, or fetch large models.
- **Keep AirLLM optional.** Layer streaming can reduce resident VRAM, but it does not make model inference free of disk/RAM/time costs. Evaluate it against task quality and timed benchmarks before choosing it for the creative director. The current upstream README documents multi-GB model storage/host-memory tradeoffs; a September 2026 Windows issue reports 28.6 s/token for one 3B setup, so performance must be benchmarked on this machine.
- **Use local deterministic functionality before heavyweight AI.** The machine's measured RAM and Windows-reported GPU memory are below the manual's recommended tier. Do not weaken timeline/verification quality to force concurrent inference.
- **Prefer the driver's VRAM report over WMI.** Windows reported 4 GiB for this laptop GPU while `nvidia-smi` reported 6141 MiB total. The probe now captures both and uses NVIDIA's driver query for model scheduling.
- **Keep the official MCP behind VANTA's own boundary.** Resolve 21.1.1's bundled MCP package contains 14 tools, including script execution and direct LUT/DCTL write/delete operations. VANTA will not expose that full toolset to the planner; use fixed adapter methods and validated arguments only.
- **Use Resolve's embedded Python runtime for the bridge.** The installation contains `ResolvePython.exe` 3.14.4 and its `DaVinciResolveScript` module loads successfully. This lets the app's current Python service use a subprocess broker rather than loading a vendor C extension into the wrong Python ABI.
- **Probe the Resolve bridge read-only.** The embedded runtime can import the scripting API, but `scriptapp('Resolve')` currently reports that Resolve is not running or Local scripting is disabled. Mission Control may show that state; no project mutation is attempted.
- **Limit the first interchange compiler to a verifiable assembly.** EDG compiles to FCPXML with a single hard-cut video spine and one rights-confirmed music lane. Overlaps, speed changes, extra video tracks, grades, and Fusion are refused until their respective compilers/gates exist. Well-formed XML tests do not count as a Resolve import pass.
- **Use the standard Python 3.12.10 VANTA environment.** The prior Python 3.11-only draft is obsolete. VANTA runs in its isolated Python 3.12.10 `.venv`; Resolve retains its separate embedded Python 3.14.4 bridge.

## 2026-10-09 — manual-conformance corrective pass

- **Treat EDG IDs as portable identities.** A `SourceMedia.id` is never a Resolve media-pool ID. The adapter resolves exactly one imported item by canonical source path and rejects missing or ambiguous matches.
- **Reject silent timing changes.** Phase 1 does not round mixed-rate frame boundaries. A retime solver must make and record any conversion decision after G13 evidence exists.
- **Make rollback ownership provable.** A `.drp` export is recovery evidence, not permission to overwrite a project. Rollback may delete only exact timeline IDs created by the same adapter instance after verifying the `VANTA_` ownership prefix.
- **Keep normal UI controls honest.** A queued run is reviewable state, not a live executor. Mission Control returns a clear conflict until an execution worker is actually attached.
- **Gate harnesses generate evidence, not a pass.** The opt-in G1 runner requires an open disposable `VANTA_GATE_*` project, five pre-imported clips, explicit execution flags, read-back, and rollback. A human reviews its evidence before changing the gate ledger.
