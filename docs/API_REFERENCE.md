# VANTA Developer API & Protocol Reference

This document provides a technical reference for VANTA's core data schemas, closed Resolve communication protocol, deterministic solvers, and color science APIs.

---

## 1. Core Data Schemas (`vanta.core.schemas`)

All schemas inherit from Pydantic v2 `BaseModel` with `extra="forbid"` to reject unexpected fields and enforce schema integrity.

### 1.1 `EditSpec`
Specifies the high-level brief, constraints, and creative slider settings for an edit:
```python
class EditSpec(BaseModel):
    goal: str
    duration_s: float
    aspect: Literal["9:16", "16:9", "1:1", "4:5"]
    fps: int = 30
    resolution: tuple[int, int] = (1080, 1920)
    sliders: SliderSet
    footage_ids: list[str]
    music_id: str | None = None
    profile_id: str | None = None
    title_text: str | None = None
    captions: bool = False
    reframe: bool = True
```

### 1.2 `EDG` (Edit Decision Graph v2)
The authoritative, versioned source of truth representing the complete edit:
```python
class EDG(BaseModel):
    id: str
    version: int = 2
    revision: int = 0
    parent_id: str | None = None
    fps: int
    duration_frames: int
    resolution: tuple[int, int]
    sources: list[SourceMedia]
    shots: list[Shot]
    grades: list[Grade]
    texts: list[Text]
    music: MusicTrack | None
    sfx: list[SFXItem] = []
    metadata: dict[str, Any] = {}
```

### 1.3 `Shot`
Represents an individual video cut placed on the timeline:
```python
class Shot(BaseModel):
    id: str
    source_id: str
    source_in_frame: int
    source_out_frame: int
    timeline_start_frame: int
    track: int = 1
    speed: float = 1.0
    beat_aligned: bool = False
    reframe_track: ReframeTrack | None = None
    fusion_comp_id: str | None = None
    tags: list[str] = []
```

### 1.4 `Grade`
Defines the 8-node DRX / 3D LUT color grade for a shot:
```python
class Grade(BaseModel):
    id: str
    shot_id: str
    camera_profile: str  # e.g., 'slog3', 'vlog', 'rec709'
    cdl: CDLParams       # slope, offset, power, saturation
    lut_path: str        # absolute path to verified 33/65-point .cube LUT
    lut_size: int = 33   # 65 for log/HDR
    intensity: float = 1.0
    export_delta_e: float | None = None
```

### 1.5 `Patch` & Scoped Patch Algebra
Enables non-destructive, surgical edits without rewriting the entire timeline:
```python
class Patch(BaseModel):
    id: str
    target: Literal["shot", "grade", "text", "music", "global"]
    target_id: str
    op: Literal["replace", "modify", "trim", "shift", "retime"]
    changes: dict[str, Any]
    reason: str
```

---

## 2. Closed DaVinci Resolve Protocol (`vanta.resolve.protocol`)

All communication between VANTA and DaVinci Resolve travels through a strictly typed, closed request protocol. The broker running in Resolve's embedded Python 3.14 rejects any message containing raw code, `eval`, or unvetted action strings.

### 2.1 Action Categories (40 Declarative Actions)

#### Project & Environment Management
- `get_resolve_status`: Queries Resolve running state, version, and scripting readiness.
- `get_project_info`: Reads active project name, frame rate, and timeline count.
- `export_drp_snapshot`: Backs up project to a `.drp` archive before any mutations.
- `restore_drp_snapshot`: Restores project state from a previous snapshot.

#### Timeline Lifecycle
- `create_timeline`: Creates a new `VANTA_<timestamp>` timeline with specified resolution and frame rate.
- `get_timeline_state`: Reads back timeline track structure, clip count, and duration.
- `set_current_timeline`: Switches active timeline view in Resolve.
- `delete_timeline`: Deletes a timeline (strictly restricted to `VANTA_*` timeline IDs).

#### Clip Trims & Track Layout
- `import_media_item`: Imports media file into Media Pool with canonical path binding.
- `append_clip_to_timeline`: Appends clip with exact source in/out trims to target track.
- `insert_clip`: Inserts clip at target timeline frame offset.
- `set_clip_speed`: Applies constant speed or speed ramp.
- `set_clip_property`: Adjusts pan, tilt, zoom, or opacity.

#### Markers & Audit Breadcrumbs
- `add_marker`: Adds color-coded frame marker with unique VANTA GUID.
- `get_markers`: Reads back all markers to verify timing integrity.
- `delete_marker`: Cleans up temporary audit markers.

#### Color Science & Grading (8-Node DRX Route)
- `apply_drx_template`: Applies 8-node DRX template to shot node tree.
- `set_cdl_values`: Sets slope, offset, power, saturation on node 2.
- `bind_lut_file`: Bakes camera log transform and look into 33/65-point `.cube` LUT on node 1.
- `export_node_lut`: Exports active node LUT to disk for read-back verification.
- `verify_lut_delta_e`: Compares exported LUT against target to confirm $\Delta E_{00} < 1.0$.

#### Fusion Compositions & Comp Chaining
- `import_fusion_comp`: Imports `.comp` file onto target timeline clip.
- `set_fusion_comp_parameters`: Injects verified token parameters into node graph.
- `chain_fusion_comps`: Merges multiple effect layers into one active comp per clip.

#### Rendering & Headless Export
- `add_render_job`: Configures export preset, resolution, codec, and output path.
- `start_rendering`: Launches asynchronous rendering job.
- `get_render_job_status`: Polls render progress and completion percentage.
- `export_fcpxml`: Emits FCPXML 1.10 file for external sequence interchange.

---

## 3. Deterministic Solver APIs (`vanta.solvers`)

### 3.1 Viterbi Cut Solver (`vanta.solvers.cut`)
```python
def solve_viterbi_cut(
    moments: list[Moment],
    target_duration_frames: int,
    beat_grid_frames: list[int],
    pacing_asd_frames: float,
    variety_weight: float = 0.8
) -> list[Shot]:
    """Computes optimal shot sequence maximizing take variety and beat snap alignment."""
```

### 3.2 8-Node DRX Grade Solver (`vanta.solvers.grade`)
```python
def solve_shot_grade(
    shot: Shot,
    source_profile: str,
    target_look: LookDescriptor,
    lut_dir: Path
) -> Grade:
    """Calculates CDL offsets and bakes camera-log decode + look into a 33/65-point .cube LUT."""
```

### 3.3 Subject-Follow Reframe Solver (`vanta.solvers.reframe`)
```python
def solve_subject_follow_reframe(
    bounding_boxes: list[tuple[float, float, float, float]],
    source_resolution: tuple[int, int],
    target_aspect: str = "9:16",
    dead_zone_ratio: float = 0.15,
    smoothing_half_life_s: float = 0.5
) -> ReframeTrack:
    """Computes dead-zone smoothed camera follow keyframes for vertical reframing."""
```

---

## 4. Camera Log Transfer Functions (`vanta.core.camera_log`)

All log transfer functions map normalized code values $[0, 1]$ to linear scene luminance, with 18% middle grey aligned to display midpoint:

| Camera Profile | Standard / Transfer Function | Middle Grey Code Value ($L \approx 0.41$) |
|---|---|---|
| `slog3` | Sony S-Gamut3.Cine / S-Log3 | $0.4105$ |
| `vlog` | Panasonic V-Gamut / V-Log | $0.4230$ |
| `clog3` | Canon Cinema Gamut / C-Log3 | $0.3430$ |
| `logc3` | ARRI Alexa Wide Gamut / LogC3 | $0.3910$ |
| `flog` | Fujifilm F-Log | $0.4590$ |
| `dlog` | DJI D-Log | $0.3980$ |
| `apple_log` | Apple Log (Rec.2020) | $0.4900$ |
| `hlg` | ITU-R BT.2100 HLG | $0.5000$ |

---

## 5. Automated QC Metric Thresholds (`vanta.qc.levels`)

| QC Level | Inspected Dimension | Pass Criteria / Threshold |
|---|---|---|
| **L0** | Schema & Path Sanity | `extra="forbid"`, all media paths exist, valid hashes. |
| **L1** | Timeline Structure | 0 video gaps, 0 track overlaps, duration error $<0.1\text{ s}$. |
| **L2** | Visual Geometry | Title safe margins $\ge 10\%$, black/white clipping $<0.1\%$. |
| **L3** | Audio Loudness | Integrated loudness: $-14.0 \pm 1.0\text{ LUFS}$, True Peak $\le -1.0\text{ dBTP}$. |
| **L4** | Style Consistency | Style distance $\Delta E_{00} < 3.0$ from reference profile. |
| **L5** | Editorial Rhythm | Downbeat alignment within $\pm 40\text{ ms}$; no back-to-back shot repeats. |
