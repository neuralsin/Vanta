the build manual is already in the folder so build it end to end without skipping anything and following it 1:1, u may use airllm to fit, we have 190gb ssd space,12gb ram and 6gb vram, can work with longer waits but everything should be very high quality no compromise in edit quality or the ai brain
# VANTA BUILD AGENT — EXECUTION PROTOCOL

You are the lead engineer building **VANTA**, the local AI video-editing system described in the project’s existing build manual.

The repository already contains:

**`Resolve Autopilot — Final Build Manual v2.docx`**

Treat that file as the **authoritative specification**.

## CRITICAL: DO NOT WASTE CONTEXT

Do NOT paste, reproduce, or summarize the entire manual into your context.

Do NOT read the whole document repeatedly.

Instead:

1. Inspect the repository first.
2. Read the manual once only as much as needed to understand its structure and roadmap.
3. Create a compact internal/project specification extracted from it.
4. For each implementation task, retrieve/read only the relevant manual section.
5. Keep the extracted requirements in project files so they do not need to be regenerated repeatedly.

Create:

```text
docs/
  VANTA_SPEC.md
  VANTA_REQUIREMENTS.md
  VANTA_DECISIONS.md
```

`VANTA_SPEC.md` should be a concise implementation-oriented representation of the manual, not a copy of it.

`VANTA_REQUIREMENTS.md` should contain checkboxes for every major requirement and acceptance test.

`VANTA_DECISIONS.md` should contain implementation decisions made during development and why.

---

# OPERATING MODE

You are not here to design a hypothetical system.

You are here to **inspect → implement → run → test → verify → fix → continue**.

For every feature:

```text
READ RELEVANT REQUIREMENT
        ↓
INSPECT EXISTING CODE
        ↓
DESIGN MINIMAL IMPLEMENTATION
        ↓
IMPLEMENT
        ↓
RUN TESTS
        ↓
RUN REAL INTEGRATION TEST IF POSSIBLE
        ↓
VERIFY RESULT
        ↓
FIX FAILURES
        ↓
UPDATE REQUIREMENTS
        ↓
MOVE TO NEXT FEATURE
```

Never stop at “code written”.

A feature is not complete until it works or has a documented external blocker.

---

# IMPORTANT REPOSITORY RULE

Before making architectural changes:

```text
inspect the current repository
```

Determine:

- existing modules
- existing Resolve integration
- existing MCP integration
- existing AI/model code
- existing UI
- dependencies
- tests
- configuration
- scripts
- database
- environment files

Reuse working code.

Refactor instead of duplicating functionality.

Do not delete useful existing implementations without understanding them.

---

# BUILD STRATEGY

Follow the manual's roadmap and acceptance ladder.

Do NOT build all subsystems simultaneously.

Build a thin vertical slice first.

Priority:

```text
1. Environment / capability detection
2. Core schemas
3. EDG
4. Resolve Adapter
5. Event system
6. Executor + verification
7. Snapshots + rollback
8. Mission Control
9. First real Resolve vertical slice
10. Perception
11. Style Brain
12. Grade solver
13. Text / masks / Fusion
14. Music
15. Planner
16. Critic / repair
17. Learning
18. Hardening
```

At each stage, preserve a working system.

---

# CORE ARCHITECTURE

Maintain the architecture defined by the manual:

```text
References
   ↓
Perception
   ↓
Style Capsules
   ↓
Style Brain

New Footage
   ↓
Footage Knowledge Graph

Style + Footage + User Brief
   ↓
Planner
   ↓
Edit Decision Graph
   ↓
Solvers
   ↓
Validator
   ↓
Policy Engine
   ↓
Confidence Gate
   ↓
Compilers
   ↓
Resolve Adapter
   ↓
DaVinci Resolve
   ↓
Proxy Render
   ↓
Critic
   ↓
Patch
   ↓
QC
   ↓
Final Candidate

User Corrections
   ↓
Preference Memory
   ↓
Style Brain
```

Do not collapse this into a single LLM prompt.

---

# FIRST MILESTONE

Get this working before building advanced AI features:

```text
5 clips + 1 song
        ↓
Edit Spec
        ↓
EDG
        ↓
Resolve timeline
        ↓
markers / metadata
        ↓
proxy render
        ↓
verification
        ↓
rollback
```

Mission Control must show the process.

This milestone is the foundation of everything else.

---

# ARCHITECTURE RULES

These are non-negotiable:

### 1. EDG is the source of truth

The AI does not directly manipulate Resolve state as its primary representation.

It produces structured decisions.

EDG represents the intended project.

Resolve is the execution target.

### 2. Single Resolve boundary

All Resolve mutations go through the Resolve Adapter.

Do not scatter Resolve API calls throughout the codebase.

### 3. Plan before execution

```text
decision
→ schema validation
→ policy validation
→ confidence
→ execution
→ verification
```

### 4. Never trust success responses

Always:

```text
WRITE
→ READ BACK
→ VERIFY
```

### 5. Non-destructive by default

Never modify originals.

Use versioned timelines and snapshots.

### 6. Patch rather than rebuild

A request such as:

> “Make the grade less aggressive.”

must produce a scoped patch to the grading portion of the EDG.

Do not rebuild the entire edit.

### 7. Model abstraction

Do not hard-code VANTA to one AI model.

Create provider/model interfaces.

### 8. Deterministic math stays deterministic

Use code for:

- timing
- statistics
- scoring
- transforms
- validation
- LUT generation
- beat alignment
- measurements

Use AI for:

- interpretation
- semantic reasoning
- creative strategy
- ranking
- intent

---

# RESOLVE

Target the Resolve/Studio capabilities defined by the manual.

At startup detect the actual environment.

Generate/update:

```text
capabilities.yaml
```

Detect things such as:

- Resolve version
- Studio availability
- scripting
- MCP
- Fusion
- render support
- transcription
- available models
- GPU
- VRAM
- external dependencies

Never hallucinate capabilities.

When Resolve cannot perform something directly, use the fallback defined by the manual or implement a safe alternative.

---

# AI MODEL USAGE

Follow the model-role separation from the manual.

Do not use one giant model for everything.

Use specialist components for:

- visual reasoning
- embeddings
- segmentation
- transcription
- OCR
- motion
- audio semantics
- deterministic color analysis

The VLM should primarily act as the **creative director/reasoner**.

It should not be responsible for arbitrary low-level Resolve operations.

---

# STYLE LEARNING

The system must learn **principles and relationships**, not just copy pixels.

Maintain separation between:

```text
observed
inferred
```

Every learned rule should track:

- evidence
- confidence
- conditions
- purpose
- implementation hypothesis
- source
- approval state

Never silently turn one Reel into a permanent style rule.

Profiles must remain coherent rather than blindly averaging unrelated references.

---

# FUSION

Never execute arbitrary model-generated Fusion code.

Use the template system described in the manual.

The model selects:

```text
template + parameters
```

not arbitrary graph code.

---

# SAFETY

Never:

- overwrite originals
- destroy user timelines
- silently replace user work
- execute arbitrary AI-generated code
- invent unavailable fonts/plugins/effects
- claim an operation succeeded without verification
- silently learn permanent preferences
- bypass the policy engine

---

# OBSERVABILITY

Every meaningful operation emits an event.

Events should contain at minimum:

```text
run_id
stage
action
status
timestamp
confidence
artifact
error
```

Mission Control must be able to display these.

A user should be able to understand what VANTA is doing without reading logs in a terminal.

---

# TESTING STANDARD

Every subsystem gets tests.

At minimum:

```text
unit tests
integration tests
golden tests
failure tests
Resolve integration tests where available
```

Test failure conditions deliberately.

Examples:

- Resolve unavailable
- MCP unavailable
- missing media
- locked timeline
- render failure
- model timeout
- missing font
- invalid mask
- insufficient VRAM
- Resolve restart

Failure should become:

```text
FAILED
RETRYABLE
CANCELLED
```

not a silent crash.

---

# CONTEXT MANAGEMENT

To remain efficient:

### Never repeatedly read the entire manual.

Instead:

```text
manual
→ extract requirements
→ store concise project spec
→ implement
→ reference only relevant section when uncertain
```

When working on:

- Resolve → read Resolve/platform section
- Style Brain → read Style section
- grading → read color section
- Fusion → read Fusion section
- learning → read learning section

Do not reload unrelated sections.

Keep code and documentation concise.

Avoid generating huge comments that simply restate the manual.

---

# AUTONOMY

You are authorized to make normal implementation decisions without asking for approval.

Do not stop because something is imperfect.

When a requirement is blocked:

1. identify the blocker
2. implement the best safe fallback
3. record the limitation
4. continue building everything else

Only stop when an action genuinely requires an unavailable external dependency, credential, hardware device, or user decision.

---

# DEVELOPMENT LOOP

After each meaningful implementation:

```text
run formatter
run type checking
run unit tests
run integration tests
inspect failures
fix failures
update status
```

Do not accumulate dozens of untested modules.

Keep `main`/default branch buildable.

---

# STATUS TRACKING

Continuously maintain:

```text
VANTA_IMPLEMENTATION_STATUS.md
```

Use:

```text
DONE
IN PROGRESS
BLOCKED
NOT STARTED
```

Include:

- current phase
- current milestone
- completed requirements
- failing tests
- known limitations
- next implementation target

Update it after each major milestone.

---

# DEFINITION OF DONE

Do not call VANTA “built” because:

- the UI loads
- an API responds
- a mock timeline is generated
- an LLM produces JSON
- fake Resolve responses are returned

The actual system must eventually perform the real loop:

```text
reference analysis
→ footage analysis
→ style reasoning
→ planning
→ EDG
→ solver execution
→ Resolve modification
→ verification
→ proxy render
→ critique
→ repair
→ final candidate
```

with user corrections feeding the learning system.

---

# START IMMEDIATELY

Begin with:

### STEP 1
Inspect the repository.

### STEP 2
Locate and inspect the build manual.

### STEP 3
Create the concise requirement/spec files described above.

### STEP 4
Determine what has already been implemented.

### STEP 5
Start Phase 0.

### STEP 6
Continue into Phase 1 immediately once Phase 0 passes.

Do not merely tell me what you plan to build.

**Actually build it.**

At the end of each work cycle, report only:

```text
CURRENT PHASE:
COMPLETED:
TESTS:
FAILURES:
BLOCKERS:
NEXT:
```

Then continue executing.

# VANTA — Autonomous Local AI Video Editing Engine
## Master Build Prompt

You are the principal engineer building **VANTA**, a local-first AI creative editing engine that analyzes reference Reels, learns editing principles and user preferences, analyzes new footage, plans an edit, and drives **DaVinci Resolve Studio** to create a real, editable, non-destructive project.

The project folder already contains:

**`Resolve Autopilot — Final Build Manual v2.docx`**

That document is the **primary technical specification and source of truth** for this build.

---

# 1. READ THE MANUAL FIRST

Before modifying code:

1. Locate and read the complete build manual in the project directory.
2. Understand its:
   - decision log
   - platform truth table
   - architecture
   - twelve design principles
   - hardware/model strategy
   - repository structure
   - data contracts
   - Style Capsule
   - Style Brain
   - Edit Spec
   - Edit Decision Graph
   - planner
   - solvers
   - grading system
   - masking/tracking system
   - Fusion template system
   - music system
   - Resolve adapter
   - executor/verification/rollback
   - Mission Control
   - critic/QC loop
   - learning system
   - roadmap
   - acceptance tests
   - failure matrix
   - performance targets

Do **not** replace the architecture with a simpler chatbot-to-Resolve implementation.

Do **not** flatten the concept into “LLM generates an edit.”

VANTA must follow the architecture described in the manual.

The manual explicitly defines the target as a **supervised co-editor**, not an unsupervised autonomous replacement for a professional editor. Preserve that design boundary.

---

# 2. YOUR JOB

Build VANTA as a **real working application**, not a conceptual scaffold.

You are responsible for:

- repository architecture
- backend
- local AI orchestration
- perception pipeline
- Style Brain
- footage analysis
- planning
- Edit Decision Graph
- editing solvers
- grading pipeline
- masks/tracking
- Fusion templates
- music analysis
- Resolve integration
- Mission Control UI
- event system
- validation
- rollback
- QC
- learning from corrections
- tests
- documentation
- installation/setup scripts

Implement real functionality wherever technically possible.

Do not create fake implementations just to make tests pass.

When a capability depends on DaVinci Resolve being open or a specific Studio API being present, build the integration boundary and a real fallback/test mode rather than pretending the operation succeeded.

---

# 3. FIRST ACTION: AUDIT THE REPOSITORY

Before writing major code, inspect the existing repository.

Determine:

- current files
- existing architecture
- programming languages
- existing dependencies
- build system
- environment files
- installed scripts
- existing Resolve integration
- existing MCP integration
- any existing UI
- any existing AI/model code
- existing tests
- existing configuration

Do not delete useful existing work.

Reuse working components when appropriate.

If the repository already contains an implementation that conflicts with the manual, preserve useful code but refactor it toward the manual architecture.

Create:

`VANTA_IMPLEMENTATION_STATUS.md`

This file must contain:

- current repository state
- manual requirements
- implemented requirements
- partially implemented requirements
- blocked requirements
- technical assumptions
- next implementation stage

Update this file throughout development.

---

# 4. CORE DESIGN PRINCIPLE

VANTA is NOT:

`video → LLM → random Resolve commands`

VANTA IS:

`references`
→ `perception`
→ `style capsules`
→ `style brain`
→ `footage knowledge graph`
→ `planner`
→ `edit decision graph`
→ `solvers`
→ `validator`
→ `policy engine`
→ `confidence gate`
→ `compiler`
→ `Resolve adapter`
→ `DaVinci Resolve`
→ `proxy render`
→ `critic`
→ `patch`
→ `QC`
→ `final`

User corrections then feed back into:

`preference memory`
→ `style brain`

This architecture must remain intact.

---

# 5. ARCHITECTURE TO IMPLEMENT

Use the manual's architecture:

```text
USER
 ├─ brief
 ├─ controls
 ├─ natural language
 ├─ approvals
 └─ corrections
        │
        ▼
REFERENCE REELS
        │
        ▼
PERCEPTION
        │
        ▼
STYLE CAPSULES
        │
        ▼
STYLE BRAIN
        │
        ├───────────────┐
        ▼               │
NEW FOOTAGE             │
        │               │
        ▼               │
FOOTAGE KNOWLEDGE GRAPH │
        │               │
        └──────┬────────┘
               ▼
            PLANNER
         (VLM Director)
               │
               ▼
      EDIT DECISION GRAPH
          (SOURCE OF TRUTH)
               │
               ▼
           SOLVERS
 ┌────────┬────────┬────────┬────────┐
 Cut     Grade    Text     Mask    Music
               │
               ▼
           VALIDATOR
               │
               ▼
         POLICY ENGINE
               │
               ▼
        CONFIDENCE GATE
               │
               ▼
           COMPILERS
               │
               ▼
        RESOLVE ADAPTER
               │
               ▼
      DAVINCI RESOLVE
               │
               ▼
         PROXY RENDER
               │
               ▼
            CRITIC
               │
               ▼
             PATCH
               │
               ▼
              QC
               │
               ▼
        FINAL CANDIDATE
               │
               ▼
        USER CORRECTIONS
               │
               ▼
      PREFERENCE MEMORY
               │
               ▼
          STYLE BRAIN
```

---

# 6. TECHNOLOGY DEFAULTS

Where the manual is explicit, follow it.

Where it is silent, prefer this stack unless the repository already has a strong reason to use something else:

### Backend
- Python 3.12+
- FastAPI
- Pydantic
- asyncio
- SQLite
- SQLModel or SQLAlchemy where useful

### Frontend
- React
- TypeScript
- Vite
- modern component system
- WebSocket/SSE for live events

### Local inference
- Ollama for local VLM orchestration where appropriate
- Qwen3-VL for visual reasoning
- Whisper large-v3-turbo for speech
- DINOv2 for visual embeddings
- CLIP/SigLIP-class models for semantic matching
- SAM 3.1 for segmentation
- OpenCV for motion analysis
- CLAP for audio/music semantics

Do not hard-code model assumptions.

Create a model/provider abstraction so models can be swapped through configuration.

---

# 7. RESOLVE INTEGRATION

Target **DaVinci Resolve Studio 21.1+** according to the manual.

Build a single high-level Resolve Adapter boundary.

The AI must never directly spray low-level Resolve API calls everywhere.

Use a narrow facade such as:

```text
create_project_structure()
create_timeline()
append_clip()
insert_clip()
duplicate_timeline()
add_marker()
set_clip_properties()
apply_grade_template()
apply_lut()
apply_cdl()
import_fusion_comp()
insert_transition()
set_fade()
set_speed()
create_transcription()
create_render_job()
poll_render()
export_still()
export_lut()
read_state()
verify_state()
```

Keep the adapter implementation isolated.

If MCP is available, use the Resolve MCP path described by the manual.

If scripting is available, use the Studio scripting API.

Expose capabilities through:

`capabilities.yaml`

Detect actual capabilities at startup.

Do not assume every Resolve installation exposes every feature.

---

# 8. HARD RULE: PLAN → VALIDATE → EXECUTE

The model must NEVER directly modify Resolve.

Every operation must pass through:

```text
model decision
→ structured action
→ schema validation
→ policy validation
→ confidence gate
→ executor
→ read-back verification
```

Every write operation must emit an event.

Every significant operation must be verifiable.

Every failed operation must be visible.

Never report success merely because an API call returned without throwing.

---

# 9. EDIT DECISION GRAPH

Implement the **Edit Decision Graph (EDG)** as the authoritative representation of an edit.

The EDG must represent at least:

- source media
- shots
- selected clips
- source in/out
- timeline positions
- track assignments
- clip roles
- pacing
- beat relationships
- transitions
- speed
- framing
- music
- captions
- typography
- masks
- Fusion templates
- color intent
- grade parameters
- confidence
- rationale
- dependencies
- user locks
- prohibitions
- provenance
- version
- timestamp

The EDG must be serializable.

Use versioned schemas.

The UI, solver layer, validator and Resolve adapter must all be able to consume it.

---

# 10. EDIT SPEC

Implement the Edit Spec contract described in the manual.

It must support:

```yaml
goal:
platform:
duration_s:
aspect:
fps:

mode:
  autonomy:
  run:

sliders:
  pacing:
  beat_sync:
  color_intensity:
  skin_priority:
  masking:
  three_d:
  typography_density:
  music_priority:

references:

locks:

never:
```

Make Edit Spec versioned.

Use Pydantic + JSON Schema.

The UI should be able to generate and edit it.

Natural-language commands should compile into **scoped patches**, not entire project rewrites.

Example:

> “Make the grade 15% less aggressive.”

This should modify the grading dimension only.

It must not rebuild the edit.

---

# 11. REFERENCE REEL LEARNING

Implement reference analysis.

For each reference Reel extract as much of the following as possible:

### Visual
- shot boundaries
- shot duration
- camera movement
- subject
- framing
- camera angle
- motion intensity
- composition
- lighting
- color characteristics
- contrast
- saturation
- skin treatment
- background/subject separation
- typography
- text placement
- overlays
- transitions
- masking
- motion graphics
- Fusion-like effects
- spatial effects
- speed changes

### Audio
- BPM
- beat grid
- downbeats
- energy
- sections
- loudness
- spectral brightness
- key/chroma where feasible
- speech
- captions
- sound effects

### Editing
- cut density
- average shot duration
- beat relationship
- repeated motifs
- visual escalation
- opening hook
- payoff
- ending behavior

Do NOT simply memorize pixel colors.

The system must reason in terms of principles and relationships.

Example:

Do not learn:

> “motorcycle night footage = teal shadows + orange highlights”

Learn:

> “night footage with isolated subject and cyan environment tends toward cool environmental shadows, warmer subject/highlight separation, deeper blacks and controlled saturation.”

Store the observed evidence separately from inferred rules.

---

# 12. STYLE CAPSULES

Implement the manual's Style Capsule structure.

Each observation must distinguish:

```text
OBSERVED
vs
INFERRED
```

Include:

- observation
- evidence
- confidence
- conditions
- likely purpose
- implementation hypothesis
- fallback
- source references
- dimension

Do not convert uncertain observations into hard rules.

---

# 13. STYLE BRAIN

Implement a persistent Style Brain.

Authoritative storage:

- SQLite

Retrieval:

- vector index such as FAISS/LanceDB

Dimensions:

```text
rhythm
color
typography
composition
motion
masking
Fusion
audio
```

Support:

- profiles
- universal principles
- conditional rules
- negative preferences
- exceptions
- dimension-specific reference weights
- user preferences
- evidence counts
- confidence
- approvals
- rejected rules

Do not average unrelated styles into one giant profile.

Keep coherent profiles separate.

Allow borrowing across profiles only through explicitly defined dimension weights.

---

# 14. RULE INDUCTION

Do NOT silently invent permanent rules.

A rule candidate should require:

- evidence
- support
- confidence
- repeated observation
- effect size or measurable association where possible
- validation
- human approval

Implement a review queue for candidate rules.

Store rejected rules too.

The system must learn from:

- explicit user feedback
- repeated corrections
- final timeline differences
- accepted/rejected suggestions

But no new rule becomes authoritative without passing the approval process.

---

# 15. FOOTAGE KNOWLEDGE GRAPH

Analyze new footage before editing.

Index:

- shot boundaries
- timestamps
- visual embeddings
- descriptions
- subjects
- motion
- quality
- camera movement
- lighting
- exposure
- composition
- audio
- speech
- transcript
- OCR
- usable ranges
- continuity relationships

Every shot must have a stable ID.

The system should be able to answer:

> “Which shots are suitable for a hard cut on the beat after the reveal?”

without reprocessing the whole footage set.

---

# 16. PLANNER

The VLM acts as a **creative director**, not the numerical solver.

Use the planning flow:

```text
OBSERVE
CLASSIFY
COMPARE
CHOOSE
IMPLEMENT
VERIFY
```

Planner passes:

### Pass 1 — Assembly
- story
- hook
- shot selection
- pacing
- music
- captions
- framing

### Pass 2 — Polish
- color
- masks
- Fusion
- typography
- SFX
- micro-timing

### Pass 3 — Finish/QC
- consistency
- technical correctness
- style adherence
- readability
- audio
- clipping
- missing assets
- final quality

Do not ask the VLM to manually calculate everything.

Use deterministic solvers for numerical tasks.

---

# 17. CUT SOLVER

Implement a shot-selection/ranking system based on:

- semantic fit
- technical quality
- motion
- rhythm
- beat fit
- continuity
- repetition penalty
- story role
- subject visibility
- style compatibility
- user preferences

Beat logic must be intelligent.

A beat does NOT automatically equal a cut.

Support:

- soft beat → potential hold
- strong beat → cut candidate
- drop → major visual change
- build → increasing density

---

# 18. TEXT-BASED EDITING

Implement:

```text
Whisper
→ timestamps
→ semantic indexing
→ text query
→ source range selection
→ handles
→ EDG
→ Resolve
→ verify
→ captions
```

Support natural-language editing such as:

> “Remove the part where I repeat myself.”

> “Keep the explanation but cut the pauses.”

> “Only keep clips where I mention the bike.”

Use Resolve-native transcription as a cross-check when available.

---

# 19. COLOR ENGINE

This is important:

Do NOT attempt to make the ordinary Resolve API magically create arbitrary color-node graphs when the API does not support it.

Use the manual's approach:

```text
color management
→ DRX templates
→ generated LUT/CDL
→ optional advanced DRX authoring
→ verification through export/stills
```

Implement a reusable node template:

```text
TECH
BALANCE
TONE
LOOK
SEP
BG
SKIN
POLISH
```

The AI should output **grading intent**.

The deterministic solver turns intent into numeric parameters.

Implement:

- histogram analysis
- luminance statistics
- RGB statistics
- skin-tone analysis
- contrast measurement
- saturation measurement
- color-temperature estimation
- subject/background relationship
- LUT generation where applicable
- CDL generation
- verification

Do not claim a grade succeeded without verification.

---

# 20. MASKING + SUBJECT SEPARATION

Use:

- SAM 3.1
- OpenCV
- tracking
- Resolve Magic Mask as fallback where available

Pipeline:

```text
prompt
→ segmentation
→ tracking
→ temporal smoothing
→ hole filling
→ feather
→ flicker suppression
→ confidence score
→ Resolve
→ verify
```

Support:

- subject cutouts
- background separation
- text behind subject
- glows
- outlines
- isolated grading where technically possible

Low-confidence masks must be reviewable.

---

# 21. FUSION

Do NOT let the language model generate arbitrary Fusion code and execute it.

Build a vetted template registry.

Support templates including:

```text
text_basic
slide
typewriter
tracking_reveal
word_highlight
kinetic_words
masked_reveal
text_behind_subject
text3d_hover
text3d_tracking
tracked_callout
screen_replacement
subject_glow
subject_outline
speed_blur
cinematic_bloom
parallax
freeze_frame
PIP
```

The model chooses:

```text
template
+
parameters
```

It does not invent arbitrary graph code.

Implement token substitution into validated template comps.

---

# 22. TEXT PLACEMENT

Implement a placement scorer considering:

- face position
- subject position
- safe zones
- saliency
- background complexity
- contrast
- user preferences
- previous placement behavior

Run OCR-based verification after rendering.

Detect:

- clipping
- overlap
- unreadable text
- face obstruction
- unsafe platform placement

---

# 23. 3D HOVER TEXT

Use the complexity ladder:

```text
2D
→ 2.5D
→ Fusion 3D
→ external camera solve
→ depth-aware occlusion
```

Do not use 3D just because it looks cool.

Require a spatial reason.

Use the external-camera-solve route described by the manual where necessary.

---

# 24. MUSIC ENGINE

Index the local/licensed music library.

Extract:

- BPM
- beat grid
- downbeats
- sections
- energy
- RMS
- brightness
- key/chroma
- loudness
- duration
- CLAP embedding
- rights/licensing metadata

Music ranking should consider:

- semantic fit
- emotional fit
- tempo
- energy
- structure
- duration
- drop timing
- user preference
- freshness

Return ranked candidates with reasons.

Never assume an external reference Reel's music is licensed for reuse.

Treat reference music as an analysis signal unless the user explicitly provides the track.

---

# 25. EVENT SYSTEM

Every meaningful stage must emit structured events.

Example:

```json
{
  "run_id": "...",
  "timestamp": "...",
  "stage": "GRADE_SOLVER",
  "action": "GENERATE_LOOK",
  "status": "RUNNING",
  "confidence": 0.93,
  "artifact": "...",
  "message": "Solving warm-subject/cool-background separation"
}
```

Create a persistent event stream.

Store:

- run ID
- stage
- timing
- status
- input
- output
- artifact
- confidence
- error
- retry count

Job state:

```text
QUEUED
RUNNING
SUCCEEDED
FAILED
RETRYABLE
CANCELLED
```

---

# 26. MISSION CONTROL

Build a local web interface.

It must provide:

- live event feed
- current pipeline stage
- progress
- current plan
- selected shots
- thumbnails
- confidence
- model currently running
- GPU/VRAM usage if available
- logs
- warnings
- errors
- pause
- step
- stop
- undo/rollback
- run history
- artifact inspection
- EDG inspection
- style decisions
- QC findings

The user should be able to watch VANTA work.

---

# 27. EXECUTION MODES

Implement:

### Autonomy

```text
AUTO
ASSISTED
MANUAL
```

### Execution

```text
WATCH
STEP
TURBO
```

Default:

```text
ASSISTED + WATCH
```

WATCH should visibly expose execution progress.

STEP should stop at meaningful checkpoints.

TURBO can execute validated operations faster without UI pauses.

---

# 28. SAFETY + POLICY ENGINE

Hard rules:

VANTA must never:

- overwrite original media
- destroy source timelines
- modify locked tracks
- silently overwrite user edits
- execute arbitrary model-generated Python
- execute arbitrary model-generated Fusion code
- load unknown plugins automatically
- invent missing fonts
- invent unavailable effects
- claim success without verification
- remove source media
- silently replace user-approved choices

Major edits must occur on versioned timelines:

```text
ASSEMBLY_V1
COLOR_V1
FUSION_V1
FINAL_CANDIDATE
APPROVED
```

Snapshot before major mutations.

Support rollback.

---

# 29. CONFIDENCE GATES

Start with the manual's thresholds:

```text
> 0.90
execute + log

0.70–0.90
execute + note

0.50–0.70
preview/approval

< 0.50
ask/fallback
```

Treat these as configurable starting values, not permanent truth.

Make calibration possible.

Store actual user acceptance/rejection outcomes.

---

# 30. VERIFICATION

Adopt:

**WRITE → READ BACK → VERIFY**

Examples:

After creating a timeline:

- inspect actual timeline
- verify clip count
- verify source mapping
- verify order
- verify durations
- verify track placement

After color:

- verify via export/still/LUT path

After Fusion:

- verify comp exists
- verify template/version
- verify clip association

After captions:

- OCR or transcription verification

After render:

- verify render status
- verify file existence
- verify duration
- verify frame rate
- verify audio
- verify errors

Never trust “success” from an intermediate system.

---

# 31. CRITIC + REPAIR LOOP

Implement:

```text
EDIT
→ PROXY RENDER
→ QC
→ CRITIC
→ PATCH
→ RE-RENDER
→ QC
```

Maximum 3 repair iterations unless explicitly configured otherwise.

QC levels:

```text
L0 Schema
L1 Timeline
L2 Visual
L3 Audio
L4 Style
L5 Editorial/VLM
```

Critic output must be structured.

Example:

```json
{
  "issue": "caption overlaps face",
  "severity": "medium",
  "confidence": 0.96,
  "affected_range": [12.40, 14.10],
  "repair": {
    "type": "MOVE_TEXT",
    "constraint": "preserve safe zone"
  }
}
```

Repairs must become EDG patches.

Do not rebuild the entire edit to fix one issue.

---

# 32. USER CORRECTION LEARNING

VANTA must learn from the final timeline.

Record:

- cuts changed
- shots rejected
- music replaced
- grade changes
- text moves
- masks corrected
- caption moves
- Fusion effects removed
- Fusion effects added
- pacing changes
- color-intensity changes

Categorize every correction.

Example:

```text
AI:
shot 14 selected

USER:
replaces with shot 9
```

Store this as a preference signal.

Repeated corrections can create candidate rules.

Never silently update authoritative Style Brain rules.

---

# 33. GPU / MODEL SCHEDULER

Implement a resource-aware job scheduler.

Rules:

- one heavy local model resident at a time by default
- unload models after stages when appropriate
- `OLLAMA_NUM_PARALLEL=1` by default
- pause AI inference when Resolve is performing GPU-heavy AI operations
- do not overload VRAM
- expose GPU/VRAM usage
- persist job states
- retry recoverable failures
- cancellation must be safe

Provide configuration for:

- low VRAM
- medium VRAM
- high VRAM
- multi-GPU

---

# 34. CAPABILITY DISCOVERY

Implement startup detection for:

- Resolve version
- Studio vs non-Studio
- MCP availability
- scripting availability
- Fusion
- Magic Mask
- render APIs
- transcription APIs
- available models
- GPU
- VRAM
- RAM
- FFmpeg
- fonts
- plugins
- LUTs
- DCTLs
- Fusion templates

Store results in:

`capabilities.yaml`

Never assume a dependency is installed.

---

# 35. REGISTRY SYSTEM

Build registries for:

### Models
- model name
- provider
- quantization
- VRAM requirement
- capabilities

### Fonts
- font name
- file
- availability

### LUTs
- name
- path
- category
- metadata

### DCTLs
- name
- path
- compatibility

### Plugins
- name
- version
- availability

### Fusion templates
- template name
- version
- supported parameters

The planner can only select assets from the verified registry.

---

# 36. STORAGE

Use SQLite as authoritative structured storage.

Persist:

- projects
- runs
- EDGs
- style capsules
- style profiles
- rules
- preferences
- footage analysis
- shot metadata
- events
- jobs
- errors
- QC results
- user corrections
- model metadata
- artifacts
- version manifests

Use a vector index for retrieval.

Use human-readable versioned KB files under:

`kb/`

---

# 37. ARTIFACT SYSTEM

Every run should produce an artifact manifest.

Example:

```text
runs/
  2026-10-09_run_001/
    manifest.json
    edit_spec.json
    edg.json
    style_snapshot.json
    footage_index.json
    planner_output.json
    solver_outputs/
    masks/
    luts/
    cdl/
    drx/
    fusion/
    proxy/
    qc/
    events/
```

Everything must be reproducible.

Record:

- code version
- schema version
- model version
- solver version
- style KB version
- prompt version
- Resolve version
- seed where relevant

---

# 38. TESTING

Do not just test Python functions.

Implement:

### Unit tests
- schemas
- validators
- shot scoring
- beat alignment
- color math
- LUT generation
- confidence logic
- policy engine
- patch system

### Golden tests
Fixed inputs should verify:

- clip count
- ordering
- durations
- tracks
- selected templates
- generated artifacts
- successful render path

Do not use exact pixel comparison as the only criterion.

### Resolve integration tests
When Resolve is available:

- project creation
- timeline creation
- clip insertion
- markers
- transitions
- grading template
- Fusion comp
- render
- rollback

### Failure tests
Simulate:

- Resolve unavailable
- MCP unavailable
- Resolve restart
- render failure
- missing media
- locked track
- invalid fps
- missing font
- missing LUT
- missing plugin
- empty transcript
- no face
- SAM failure
- VLM timeout
- model unavailable
- insufficient VRAM

Every failure must degrade gracefully.

---

# 39. DEVELOPMENT STRATEGY

Do NOT attempt to implement every feature simultaneously.

Build in vertical slices.

Follow the manual's roadmap:

## Phase 0
Environment/capability gates.

## Phase 1
Resolve Adapter + EDG + events + Mission Control + snapshots.

Acceptance:

**5 clips + 1 song → watchable timeline → visible live progress → rollback works**

## Phase 2
Perception + calibration.

Acceptance:

Reference looks can be recovered reliably enough for downstream reasoning.

## Phase 3
Style Brain.

Acceptance:

Reference reels produce Style Capsules and validated principles/conditional rules.

## Phase 4
Grade Engine.

Acceptance:

DRX + LUT/CDL path works and is export-verified.

## Phase 5
Text + masks + Fusion templates.

Acceptance:

Behind-subject text and captions survive OCR QC.

## Phase 6
Music engine.

Acceptance:

Top candidates with reasons and beat alignment.

## Phase 7
Planner + Critic + Repair.

Acceptance:

Build → critique → patch → render without full rebuild.

## Phase 8
UI.

Acceptance:

User can control the system without writing code.

## Phase 9
Learning/hardening.

Acceptance:

User corrections measurably affect future decisions.

---

# 40. ACCEPTANCE LADDER

The first complete usable version should aim for:

### Level 1 — Foundation
5 reference reels  
10 raw clips  
1 song

Output:

- style capsule
- footage analysis
- 20–30 sec timeline
- non-destructive grade
- captions
- preview render

### Level 2 — Compositing
- masks
- behind-subject text
- editable Fusion comps

### Level 3 — Spatial + Rhythm
- 3D text
- music selection
- beat alignment
- speed changes

### Level 4 — Self Correction
- critic detects issues
- patches edit
- rerenders affected region

### Level 5 — Learning
User corrections from Edit 01 improve Edit 02.

### Level 6 — Visibility
Every execution step is visible and controllable.

---

# 41. PERFORMANCE TARGETS

Use the manual's planning estimates as targets rather than guarantees.

Target approximately:

```text
reference perception: 1–3 min / 30 sec reel
footage indexing: 2–5 min / raw minute
planning: 1–3 min
masking: 20–90 sec / shot
grade solve: ~5 sec / shot
compile/apply: <1 min in TURBO
proxy QC: 1–3 min
first draft: target <=20 min on the 24 GB tier
```

Instrument every stage.

Do not optimize blindly.

Record real measurements.

---

# 42. UI EXPERIENCE

VANTA should feel like a serious creative workstation, not a developer dashboard.

Main areas:

### Home
Project creation and recent projects.

### Mission Control
Live autonomous run.

### Review
Timeline decisions, thumbnails, confidence, reasons.

### Reference Inspector
Why a reference reel was interpreted the way it was.

### Style Explorer
Current learned principles and profiles.

### Transcript Editor
Semantic editing.

### Chat
Natural-language scoped patches.

Examples:

> “Make this more cinematic.”

> “Reduce the saturation.”

> “Cut faster during the chorus.”

> “Use the visual language of reference 07 but keep the typography from reference 12.”

> “Undo the last mask.”

These become structured operations.

---

# 43. DESIGN FOR REAL USERS

The application must assume the user is creative, not a programmer.

Avoid exposing raw implementation details unless the user opens an advanced panel.

Default interaction:

```text
give references
+
give footage
+
describe goal
=
VANTA builds draft
```

Advanced users can inspect:

- EDG
- style rules
- confidence
- solver outputs
- event stream
- model selection
- artifacts

---

# 44. NON-NEGOTIABLE ENGINEERING RULES

1. Never destroy originals.

2. Never silently mutate the user's project.

3. Never let the VLM directly control Resolve.

4. Never invent unavailable Resolve capabilities.

5. Never invent unavailable fonts/plugins/effects.

6. Never report an operation as successful without verification.

7. Never silently learn permanent style rules.

8. Never rebuild the whole edit for a local correction.

9. Never couple the system to one AI model.

10. Every major operation must be replayable.

11. Every decision must have provenance.

12. Every run must have a unique run ID.

13. Every execution must be observable.

14. Every failed stage must have a useful error.

15. All APIs must have timeouts and retry policies.

16. Model-generated arbitrary code execution is prohibited.

17. Prefer deterministic algorithms where deterministic algorithms are sufficient.

18. Preserve user control.

---

# 45. CODE QUALITY

This is intended to become a serious long-term project.

Therefore:

- type everything
- use clear interfaces
- use dependency injection where appropriate
- avoid giant files
- avoid duplicated logic
- use structured logging
- use configuration objects
- validate external inputs
- write docstrings for public APIs
- use meaningful exception classes
- write tests alongside functionality
- keep modules independently replaceable
- maintain a changelog

No placeholder:

```python
pass
```

or:

```python
TODO: implement later
```

unless the missing feature is genuinely blocked by an external dependency.

When something is blocked, document the exact reason and implement the best safe fallback.

---

# 46. WHEN THE MANUAL DOESN'T SPECIFY SOMETHING

Do not randomly invent architecture.

Use this priority:

1. Build manual
2. Existing working repository code
3. Actual installed environment/capabilities
4. Existing interfaces in the codebase
5. Conservative engineering judgment

When a decision remains ambiguous, choose the simplest implementation that preserves future extensibility.

Do not weaken the architecture merely to finish faster.

---

# 47. IMPLEMENTATION OUTPUT REQUIREMENTS

As you build, continuously maintain:

```text
README.md
VANTA_IMPLEMENTATION_STATUS.md
CHANGELOG.md
ARCHITECTURE.md
capabilities.yaml
.env.example
```

Also create setup scripts for the development environment.

The README must eventually contain:

- requirements
- Resolve setup
- model setup
- Python environment
- database setup
- GPU requirements
- starting backend
- starting Mission Control
- connecting Resolve
- importing references
- importing footage
- running first VANTA edit
- troubleshooting
- supported capabilities
- known limitations

---

# 48. DO NOT STOP AT THE FIRST WORKING DEMO

A common failure mode is:

> “I created a FastAPI server and a button that says Edit.”

That is NOT success.

The project is complete only when the system actually performs the core loop:

```text
reference reels
→ analyze style
→ analyze footage
→ create Edit Spec
→ generate EDG
→ solve edit
→ apply to Resolve
→ verify
→ render proxy
→ critique
→ patch
→ final candidate
```

and the user can see what VANTA is doing.

---

# 49. START NOW

Your first tasks are:

### Step 1
Read the complete build manual.

### Step 2
Audit the repository.

### Step 3
Create/update:

```text
VANTA_IMPLEMENTATION_STATUS.md
ARCHITECTURE.md
capabilities.yaml
```

### Step 4
Implement Phase 0 capability detection and environment validation.

### Step 5
Implement the core contracts:

```text
EditSpec
EDG
StyleCapsule
StyleRule
Patch
Event
Job
RunManifest
QCResult
```

### Step 6
Implement the Resolve Adapter abstraction.

### Step 7
Implement the event bus and persistent run system.

### Step 8
Implement Mission Control v0.

### Step 9
Implement the smallest complete vertical slice:

```text
5 clips
+
1 song
→
EDG
→
Resolve timeline
→
markers
→
proxy render
→
verification
→
rollback
```

### Step 10
Only after that, expand into perception, Style Brain, grading, masks, Fusion, music, critic and learning according to the roadmap.

---

# 50. MOST IMPORTANT INSTRUCTION

Build **VANTA**, not a mockup of VANTA.

Prioritize:

**working infrastructure > flashy UI**

**real Resolve control > simulated operations**

**structured decisions > giant prompts**

**verification > optimistic success messages**

**editable timelines > rendered videos**

**learning from user corrections > premature fine-tuning**

**deterministic solvers > asking an LLM to guess numeric values**

**safe fallbacks > pretending unsupported features work**

**observability > hidden automation**

**incremental vertical slices > massive unfinished architecture**

The end goal is a local AI creative co-editor that can look at reference Reels, understand the *reasoning and visual grammar* behind them, apply that knowledge to new footage, produce a professional-looking editable DaVinci Resolve project, visibly execute the work, critique its result, patch mistakes, and gradually become more aligned with the user's creative taste.

Begin by reading the manual and auditing the repository. Then start implementing Phase 0 immediately.
Do not wait for additional instructions unless the build is genuinely blocked by missing credentials, hardware, software, or an irreversible external action.