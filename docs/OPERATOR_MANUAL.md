# VANTA Operator & Editor Manual

Welcome to the **VANTA Operator & Editor Manual**. This guide provides practical, step-by-step instructions for editing videos with VANTA and DaVinci Resolve Studio 21.1+.

---

## 1. System Setup & DaVinci Resolve Configuration

Before running VANTA, configure DaVinci Resolve Studio for external scripting and color science:

### 1.1 Enable External Scripting in Resolve Studio
1. Open **DaVinci Resolve Studio 21.1**.
2. From the top menu, navigate to **DaVinci Resolve ▸ Preferences** (or press `Ctrl + ,`).
3. Select the **System** tab, then click **General** in the left sidebar.
4. Locate the **External scripting using** setting and set it to **Local**.
5. Click **Save** and restart DaVinci Resolve Studio.

### 1.2 Timeline Color Science Requirement
VANTA includes an integrated camera-log decoding pipeline that transforms raw camera log (Sony S-Log3, Canon C-Log3, Panasonic V-Log, ARRI LogC3, etc.) directly inside generated 33/65-point 3D LUTs before applying creative looks.

> [!IMPORTANT]
> Always set your DaVinci Resolve project color science to **DaVinci YRGB** (not DaVinci YRGB Color Managed).  
> If an existing project uses DaVinci YRGB Color Managed or ACES, VANTA will refuse to apply grades to prevent double-decoding your footage.

---

## 2. Launching the Desktop Workstation

Launch VANTA using any of the following methods:
- **Application Shortcut:** Double-click the `VANTA.exe` desktop or Start Menu shortcut.
- **PowerShell / Terminal:**
  ```powershell
  .\.venv\Scripts\python.exe -m vanta.app
  ```

---

## 3. Step-by-Step Editing Workflow

```text
Library Ingest ──> Style Brain Exploration ──> Create & Steer ──> Mission Control Build ──> Review & QC ──> Final Export
```

### 3.1 Step 1: Library & Media Ingestion
Navigate to the **Library** page in the left navigation sidebar.

1. **Ingest Raw Footage:**
   - Drag and drop your camera video files into the footage drop area, or click **Import Footage**.
   - For long continuous camera rolls (e.g., 30–60 minutes of uninterrupted shooting), VANTA automatically runs `split_long_takes()` to segment the roll into 3–6 second coherent editorial *moments*.
2. **Assign Camera Log Profiles:**
   - In the footage list, select the correct camera color profile for each clip:
     - `slog3`: Sony S-Log3 / S-Gamut3.Cine
     - `vlog`: Panasonic V-Log / V-Gamut
     - `clog3`: Canon C-Log3 / Cinema Gamut
     - `logc3`: ARRI LogC3 / Alexa Wide Gamut
     - `flog`: Fujifilm F-Log
     - `dlog`: DJI D-Log / D-Gamut
     - `apple_log`: Apple Log (iPhone 15/16 Pro)
     - `hlg`: Hybrid Log-Gamma
     - `rec709`: Standard Rec.709 SDR
   - If left on `auto`, VANTA analyzes metadata tags and luminance histograms to detect the profile automatically.
3. **Ingest Reference Reels (Style Transfer):**
   - Paste public Instagram Reel or TikTok URLs into the reference input field and click **Download & Ingest**.
   - Built-in `yt-dlp` fetches the reel, runs multi-modal perception (shots, pacing, motion vectors, color palette, audio BPM), generates **Style Capsules**, and purges the raw video download to protect creator privacy.
4. **Import Music:**
   - Drop your audio track into the music tab. Ensure rights confirmation is toggled on (`Licence Confirmed`).

---

### 3.2 Step 2: Style Brain & Rule Review
Navigate to the **Style Brain** page.

- **Capsule Explorer:** Inspect observed visual characteristics (Average Shot Duration, color temperature, optical flow magnitude) alongside inferred director intent.
- **Rule Review Queue:** When VANTA observes a recurring technique across at least 8 shots and 3 distinct reference reels, it proposes a **Candidate Rule**.
  - Click **Approve** to adopt the rule into your creative profile.
  - Click **Reject** or **Veto** to ban the rule from future edits.
  - *Golden Rule:* VANTA never applies candidate rules silently without operator approval.

---

### 3.3 Step 3: Create, Sliders & Preflight
Navigate to the **Create** page.

1. **Creative Brief:** Enter your target editorial goal (e.g., *"Cinematic 9:16 night ride reel with punchy transitions"*).
2. **Target Parameters:** Select aspect ratio (`9:16`, `16:9`, `1:1`), target duration (e.g., `30s`), and delivery FPS.
3. **Creative Sliders:** Adjust the 5 core creative levers:
   - **Pacing (0.0 to 1.0):** Lower values favor long, continuous takes; higher values increase cut frequency.
   - **Beat Sync (0.0 to 1.0):** Controls how strictly cuts, transitions, and text animations snap to downbeats.
   - **Color Intensity (0.0 to 1.0):** Controls the contrast and creative look push applied over technical camera transforms.
   - **Typography Density (0.0 to 1.0):** Controls the frequency and visual weight of on-screen titles and captions.
   - **Motion Effects (0.0 to 1.0):** Controls speed ramps, parallax zooms, and dynamic subject tracking.
4. **Preflight Estimator:** Review the preflight card before initiating the build:
   - Estimated pipeline run-time (e.g., `4m 12s`).
   - Hardware resource checks (RAM, VRAM lease availability, disk space).
   - Quality warnings (e.g., mixed frame rates, underexposed shots).
5. **Interactive Steer Board:**
   - Review planned shots, musical downbeats, solved looks, and scheduled effects.
   - Toggle individual shots or techniques on/off.
   - Click **Preview** to render an immediate 16-bit RGB video preview without modifying your DaVinci Resolve project.

---

### 3.4 Step 4: Mission Control (Live Resolve Build)
Click **Build in Resolve** to open **Mission Control**.

1. **Select Execution Mode:**
   - **WATCH (Recommended for review):** Slow, educational execution pace (0.7s per operation). Allows you to watch the timeline assemble clip-by-clip in DaVinci Resolve.
   - **STEP:** Pauses at each operation. Requires clicking **Next Step** to proceed.
   - **TURBO:** Maximum-speed headless execution. Assembles the entire edit in seconds.
2. **Runtime Controls:**
   - **Pause / Resume:** Temporarily freezes timeline mutations.
   - **Step Forward:** Advances a single operation in STEP mode.
   - **Skip:** Skips an optional decorative layer (e.g., skips a title or light leak) while keeping the cut intact.
   - **Stop & Rollback:** Halts execution and safely rolls back your Resolve project to its initial `.drp` snapshot.
   - **Undo:** Reverts the previous timeline modification.
3. **Breadcrumbs & Safety:**
   - VANTA generates a unique `VANTA_*` timeline for each run. It never mutates or overwrites existing user timelines.
   - Every clip is tagged with custom metadata breadcrumbs for complete auditability.

---

### 3.5 Step 5: Review & Automated QC
Once execution finishes, navigate to the **Review** page.

1. **6-Tier Quality Control Report Card:**
   - **L0 (Schema):** Verifies data contracts and asset integrity.
   - **L1 (Timeline):** Scans for unintended black gaps, freeze frames, and track overlaps.
   - **L2 (Visual/OCR):** Checks for luma/chroma clipping, face occlusion, and title safe-zone violations.
   - **L3 (Audio):** Verifies integrated loudness (-14 LUFS standard) and true peak limits ($-1.0\text{ dBTP}$).
   - **L4 (Style):** Measures style consistency against the selected Style Brain profile.
   - **L5 (Editorial):** Evaluates pacing rhythm, shot variety, and downbeat synchronization.
2. **Critic Feedback & Chat Patcher:**
   - The automated critic summarizes editorial findings.
   - Use the natural language chat box to request adjustments (e.g., *"Make the hook shot 1 second faster"* or *"Tone down the grade saturation by 20%"*).
   - VANTA compiles your request into **scoped patches** and updates only the targeted parameters without rewriting the entire cut.
3. **Delivery Render:**
   - Click **Final Export** to queue the timeline in DaVinci Resolve's native render queue or trigger an internal Master render.

---

### 3.6 Step 6: Learning & Preference Memory
Navigate to the **Learning** page.

- If you make manual adjustments to the timeline inside DaVinci Resolve (trimming a cut, swapping a shot, tweaking a CDL node), VANTA's timeline diff detector analyzes your changes.
- Repeated corrections are synthesized into **Learned Preferences** (e.g., *"Editor prefers wider shots for dialogue"*), which automatically steer future edits.

---

## 4. In-Resolve HUD Client

For dual-monitor setups or direct inside-Resolve control, VANTA includes an embedded Fusion HUD script:

1. In DaVinci Resolve, navigate to **Workspace ▸ Scripts ▸ VANTA_HUD**.
2. The floating HUD window connects to the local VANTA desktop app via WebSocket (`localhost:8765`).
3. View real-time QC scores, current operation progress, and trigger Pause/Resume directly from inside DaVinci Resolve.

---

## 5. Troubleshooting & Frequently Asked Questions

### Resolve Scripting Disconnected
- **Symptom:** Mission Control displays `Resolve Disconnected` or `Unable to initialize scripting module`.
- **Solution:** Verify that DaVinci Resolve Studio is open, and confirm **Preferences ▸ System ▸ General ▸ External scripting using** is set to **Local**. Restart Resolve after changing this setting.

### CUDA Out of Memory (OOM)
- **Symptom:** Neural models fail during perception or transcription.
- **Solution:** VANTA's single GPU lease scheduler strictly enforces one model resident in VRAM. Ensure no external heavy processes (games, 3D renderers, or multiple Resolve Studio instances) are consuming the RTX 4050's 6 GB VRAM budget.

### Double-Decoded / Washed Out Colors
- **Symptom:** Footage appears overly contrasty or washed out after grading.
- **Solution:** Ensure project color science is set to **DaVinci YRGB**. Do not apply camera LUTs in the media pool; VANTA bakes the camera decode directly into the node 1 LUT.
