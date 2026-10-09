"""Create: the brief (Edit Spec) and the steer board shown before VANTA touches Resolve."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vanta.app import theme
from vanta.app.widgets.charts import TimelineStrip
from vanta.app.widgets.common import (
    Card,
    EmptyState,
    FlowWidget,
    SliderRow,
    Thumb,
    button,
    clear_layout,
    label,
    scroll,
)
from vanta.app.widgets.video import VideoPlayer
from vanta.config import get_settings
from vanta.core.schemas import DIMENSIONS, AutonomyMode, EditSpec, RunMode, SliderSet

SLIDER_GROUPS = {
    "Editorial": [("pacing", "Pacing", "calm", "aggressive"), ("beat_sync", "Beat sync", "free", "locked to beats"), ("story_clarity", "Story clarity", "mood", "narrative"), ("shot_variety", "Shot variety", "repeat", "varied")],
    "Visual": [("color_intensity", "Colour intensity", "natural", "stylised"), ("contrast", "Contrast", "soft", "punchy"), ("saturation", "Saturation", "muted", "rich"), ("skin_priority", "Skin preservation", "free", "protected")],
    "Typography": [("typography_density", "Text amount", "none", "lots"), ("caption_density", "Caption density", "sparse", "dense")],
    "Effects": [("masking", "Masking", "off", "aggressive"), ("three_d", "3D", "off", "frequent"), ("motion_effects", "Motion graphics / transitions", "clean", "energetic"), ("glow", "Glow", "off", "strong"), ("speed_ramps", "Speed ramps", "off", "frequent")],
    "Audio": [("music_priority", "Music priority", "under", "lead"), ("dialogue_priority", "Dialogue priority", "music", "speech"), ("sfx_priority", "SFX", "none", "lots")],
}
PLATFORMS = [("instagram_reel", "9:16", (1080, 1920)), ("tiktok", "9:16", (1080, 1920)), ("youtube_short", "9:16", (1080, 1920)), ("landscape", "16:9", (1920, 1080)), ("square", "1:1", (1080, 1080))]


class CreatePage(QWidget):
    def __init__(self, bridge, window):
        super().__init__()
        self.bridge = bridge
        self.window = window
        self.service = bridge.service
        self.run_id: str | None = None
        self.project_id: str | None = None
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._brief())
        split.addWidget(self._steer())
        split.setSizes([560, 980])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)
        bridge.event.connect(self._on_event)
        bridge.project_changed.connect(self._load_project)
        bridge.library_changed.connect(self._refresh_sources)

    # ------------------------------------------------------------- brief
    def _brief(self) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 18, 16, 24)
        layout.setSpacing(14)
        top = Card()
        row = QHBoxLayout()
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(lambda _i: self._load_project(self.project_combo.currentData()))
        row.addWidget(self.project_combo, 1)
        row.addWidget(button("New", icon_name="fa5s.plus", on_click=self._new_project))
        top.body.addLayout(row)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.name = QLineEdit("My edit")
        form.addRow("Name", self.name)
        self.goal = QPlainTextEdit()
        self.goal.setPlaceholderText("e.g. dark cinematic motorcycle night reel, slow first half, hard drop near 11 s")
        self.goal.setFixedHeight(64)
        form.addRow("Brief", self.goal)
        self.title_text = QLineEdit()
        self.title_text.setPlaceholderText("optional title, e.g. NIGHT RIDE")
        form.addRow("Title", self.title_text)
        self.locked_text = QLineEdit()
        self.locked_text.setPlaceholderText("extra locked lines, separated by |")
        form.addRow("Locked text", self.locked_text)
        self.never = QLineEdit()
        self.never.setPlaceholderText("e.g. text over faces | zoom transitions on static shots")
        form.addRow("Never", self.never)
        specs = QHBoxLayout()
        self.platform = QComboBox()
        for key, aspect, res in PLATFORMS:
            self.platform.addItem(f"{key.replace('_', ' ')} ({aspect})", (key, aspect, res))
        specs.addWidget(self.platform, 2)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(3, 600)
        self.duration.setValue(get_settings().edit.duration_s)
        self.duration.setSuffix(" s")
        specs.addWidget(self.duration)
        self.fps = QComboBox()
        for f in ("23.976", "24", "25", "29.97", "30", "50", "60"):
            self.fps.addItem(f)
        self.fps.setCurrentText("30")
        specs.addWidget(self.fps)
        form.addRow("Format", specs)
        modes = QHBoxLayout()
        self.autonomy = QComboBox()
        self.autonomy.addItems([m.value for m in AutonomyMode])
        self.autonomy.setCurrentText("ASSISTED")
        self.run_mode = QComboBox()
        self.run_mode.addItems([m.value for m in RunMode])
        self.run_mode.setToolTip("WATCH: slow, follow along in Resolve · STEP: approve each step · TURBO: fast, breadcrumbs only")
        modes.addWidget(self.autonomy)
        modes.addWidget(self.run_mode)
        form.addRow("Run mode", modes)
        self.profile = QComboBox()
        form.addRow("Style profile", self.profile)
        self.music = QComboBox()
        form.addRow("Music", self.music)
        self.captions = QCheckBox("Word-level captions from speech")
        self.captions.setChecked(True)
        form.addRow("", self.captions)
        top.body.addLayout(form)
        layout.addWidget(top)
        footage_card = Card()
        footage_card.body.addWidget(label("Footage", "h3"))
        self.footage_list = QListWidget()
        self.footage_list.setIconSize(QSize(40, 72))
        self.footage_list.setMinimumHeight(170)
        footage_card.body.addWidget(self.footage_list)
        layout.addWidget(footage_card)
        refs_card = Card()
        refs_card.body.addWidget(label("Reference weights per dimension", "h3"))
        refs_card.body.addWidget(label("Borrow colour from one reel and typography from another. Empty = learned profile average. Unchecked reels are excluded.", "faint", wrap=True))
        self.ref_table = QTableWidget(0, len(DIMENSIONS) + 2)
        self.ref_table.setHorizontalHeaderLabels(["Use", "Reference"] + [d[:5] for d in DIMENSIONS])
        self.ref_table.verticalHeader().setVisible(False)
        self.ref_table.setMinimumHeight(170)
        self.ref_table.setColumnWidth(0, 40)
        self.ref_table.setColumnWidth(1, 170)
        for i in range(len(DIMENSIONS)):
            self.ref_table.setColumnWidth(i + 2, 54)
        refs_card.body.addWidget(self.ref_table)
        layout.addWidget(refs_card)
        self.sliders: dict[str, SliderRow] = {}
        tabs = QTabWidget()
        for group, items in SLIDER_GROUPS.items():
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(4, 10, 4, 4)
            for key, title, left, right in items:
                slider = SliderRow(title, getattr(SliderSet(), key), left=left, right=right)
                self.sliders[key] = slider
                pl.addWidget(slider)
            pl.addStretch(1)
            tabs.addTab(page, group)
        sl_card = Card()
        sl_card.body.addWidget(label("Controls", "h3"))
        sl_card.body.addWidget(tabs)
        layout.addWidget(sl_card)
        self.preflight_card = Card()
        self.preflight_card.body.addWidget(label("Preflight", "h3"))
        self.preflight_card.body.addWidget(label("Estimated run time and anything that would lower quality. Runs automatically when you plan.", "faint", wrap=True))
        self.preflight_body = QVBoxLayout()
        self.preflight_card.body.addLayout(self.preflight_body)
        self.preflight_card.body.addWidget(button("Check now", icon_name="fa5s.clipboard-check", on_click=self._preflight))
        layout.addWidget(self.preflight_card)
        actions = QHBoxLayout()
        actions.addWidget(button("Save brief", icon_name="fa5s.save", on_click=self._save))
        self.use_vlm = QCheckBox("Use creative director (VLM)")
        self.use_vlm.setChecked(True)
        actions.addWidget(self.use_vlm)
        actions.addStretch(1)
        actions.addWidget(button("Plan the edit", kind="primary", icon_name="fa5s.drafting-compass", on_click=self._plan))
        layout.addLayout(actions)
        return scroll(content)

    # ------------------------------------------------------------- steer
    def _steer(self) -> QWidget:
        content = QWidget()
        self.steer_layout = QVBoxLayout(content)
        self.steer_layout.setContentsMargins(16, 18, 24, 24)
        self.steer_layout.setSpacing(14)
        self.steer_empty = EmptyState("fa5s.compass", "Steer before anything is built", "Plan the edit to see which reels and techniques VANTA will draw on, the planned shot sequence, the solved looks and the music choice. Toggle anything off, re-plan, preview — then build in Resolve.")
        self.steer_layout.addWidget(self.steer_empty)
        self.steer_body = QWidget()
        self.steer_body.setVisible(False)
        body = QVBoxLayout(self.steer_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        head = QHBoxLayout()
        head.addWidget(label("STEER BOARD", "eyebrow"))
        head.addStretch(1)
        self.btn_replan = button("Re-plan", icon_name="fa5s.redo", on_click=self._plan)
        self.btn_preview = button("Render preview", icon_name="fa5s.play-circle", on_click=self._preview)
        self.btn_mattes = button("Generate mattes", icon_name="fa5s.user-circle", on_click=self._mattes)
        self.btn_build = button("Approve && build in Resolve", kind="primary", icon_name="fa5s.hammer", on_click=self._build)
        for b in (self.btn_replan, self.btn_mattes, self.btn_preview, self.btn_build):
            head.addWidget(b)
        body.addLayout(head)
        self.summary = label("", "h2", wrap=True)
        body.addWidget(self.summary)
        self.warnings = label("", "dim", wrap=True)
        self.warnings.setStyleSheet(f"color: {theme.WARN};")
        body.addWidget(self.warnings)
        strip_card = Card()
        strip_card.body.addWidget(label("Planned sequence", "h3"))
        self.strip = TimelineStrip()
        strip_card.body.addWidget(self.strip)
        holder = QWidget()
        self.thumbs = QHBoxLayout(holder)
        self.thumbs.setSpacing(6)
        self.thumbs.setContentsMargins(0, 0, 0, 0)
        strip_scroll = scroll(holder, horizontal=True)
        strip_scroll.setFixedHeight(168)
        strip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        strip_card.body.addWidget(strip_scroll)
        body.addWidget(strip_card)
        self.inspired_card = Card()
        self.effects_card = Card()
        self.looks_card = Card()
        self.music_card = Card()
        body.addWidget(self.inspired_card)
        pair_row = QHBoxLayout()
        pair_row.setSpacing(14)
        pair_row.addWidget(self.effects_card, 1, Qt.AlignTop)
        pair_row.addWidget(self.music_card, 1, Qt.AlignTop)
        body.addLayout(pair_row)
        body.addWidget(self.looks_card)
        preview_card = Card()
        preview_card.body.addWidget(label("Preview (VANTA render — transitions approximated; Resolve is the reference)", "h3"))
        self.player = VideoPlayer()
        preview_card.body.addWidget(self.player)
        body.addWidget(preview_card)
        self.steer_layout.addWidget(self.steer_body)
        self.steer_layout.addStretch(1)
        return scroll(content)

    # ------------------------------------------------------------ data
    def on_show(self) -> None:
        self._refresh_projects()
        self._refresh_sources()

    def _refresh_projects(self) -> None:
        current = self.project_id or self.bridge.current_project
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("— new edit —", None)
        for p in self.service.library.list_projects():
            self.project_combo.addItem(p["name"], p["id"])
        if current:
            idx = self.project_combo.findData(current)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)
        if current:
            self._load_project(current)

    def _refresh_sources(self) -> None:
        from PySide6.QtGui import QIcon, QPixmap

        lib = self.service.library
        checked = set(self._selected_footage()) if self.footage_list.count() else set()
        self.footage_list.clear()
        for media in lib.list_media("footage", enabled_only=True):
            item = QListWidgetItem(f"{media.get('title')}  ·  {(media.get('duration_s') or 0):.1f}s  ·  {media['analysis_status']}")
            if media.get("thumb") and Path(media["thumb"]).is_file():
                item.setIcon(QIcon(QPixmap(media["thumb"])))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if (media["id"] in checked or not checked) and media["analysis_status"] == "done" else Qt.Unchecked)
            item.setData(Qt.UserRole, media["id"])
            self.footage_list.addItem(item)
        prof = self.profile.currentData()
        self.profile.clear()
        self.profile.addItem("all references", None)
        for p in lib.list_profiles():
            self.profile.addItem(p["name"], p["id"])
        if prof:
            self.profile.setCurrentIndex(max(0, self.profile.findData(prof)))
        music = self.music.currentData()
        self.music.clear()
        self.music.addItem("auto: rank my licensed library", None)
        for track in lib.list_media("music", enabled_only=True):
            rights = track.get("rights_json") or {}
            mark = "" if rights.get("platform_cleared") or rights.get("licence_confirmed") else "  (licence unconfirmed)"
            self.music.addItem(f"{track.get('title')}{mark}", track["id"])
        if music:
            self.music.setCurrentIndex(max(0, self.music.findData(music)))
        refs = lib.list_media("reference")
        self.ref_table.setRowCount(len(refs))
        for row, ref in enumerate(refs):
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            box.setCheckState(Qt.Checked)
            self.ref_table.setItem(row, 0, box)
            name = QTableWidgetItem(f"{row + 1}. {(ref.get('title') or '')[:30]}")
            name.setData(Qt.UserRole, ref["id"])
            name.setFlags(Qt.ItemIsEnabled)
            self.ref_table.setItem(row, 1, name)
            for col in range(len(DIMENSIONS)):
                self.ref_table.setItem(row, col + 2, QTableWidgetItem(""))

    def _selected_footage(self) -> list[str]:
        return [self.footage_list.item(i).data(Qt.UserRole) for i in range(self.footage_list.count()) if self.footage_list.item(i).checkState() == Qt.Checked]

    def _spec(self) -> EditSpec:
        key, aspect, res = self.platform.currentData()
        weights: dict[str, dict[str, float]] = {}
        disabled = []
        for row in range(self.ref_table.rowCount()):
            ref_id = self.ref_table.item(row, 1).data(Qt.UserRole)
            if self.ref_table.item(row, 0).checkState() != Qt.Checked:
                disabled.append(ref_id)
                continue
            for col, dim in enumerate(DIMENSIONS):
                text = (self.ref_table.item(row, col + 2).text() or "").strip()
                if text:
                    try:
                        weights.setdefault(ref_id, {})[dim] = max(0.0, min(1.0, float(text)))
                    except ValueError:
                        pass
        locks = {}
        lines = [t.strip() for t in self.locked_text.text().split("|") if t.strip()]
        if self.title_text.text().strip():
            lines.insert(0, self.title_text.text().strip())
        if lines:
            locks["text"] = lines
        return EditSpec(
            goal=self.goal.toPlainText().strip() or self.name.text().strip() or "edit",
            platform=key,
            duration_s=self.duration.value(),
            aspect=aspect,
            fps=float(self.fps.currentText()),
            resolution=res,
            autonomy=AutonomyMode(self.autonomy.currentText()),
            run_mode=RunMode(self.run_mode.currentText()),
            sliders=SliderSet(**{k: s.value() for k, s in self.sliders.items()}),
            reference_weights=weights,
            disabled_references=disabled,
            locks=locks,
            never=[t.strip() for t in self.never.text().split("|") if t.strip()],
            profile_id=self.profile.currentData(),
            captions=self.captions.isChecked(),
            music_id=self.music.currentData(),
            footage_ids=self._selected_footage(),
            title_text=self.title_text.text().strip() or None,
        )

    def _new_project(self) -> None:
        self.project_id = None
        self.project_combo.setCurrentIndex(0)
        self.name.setText("My edit")
        self.goal.clear()

    def _load_project(self, project_id: str | None) -> None:
        if not project_id:
            return
        project = self.service.library.get_project(project_id)
        if not project:
            return
        self.project_id = project_id
        spec = EditSpec.model_validate(project["spec_json"])
        self.name.setText(project["name"])
        self.goal.setPlainText(spec.goal)
        self.title_text.setText(spec.title_text or "")
        self.locked_text.setText(" | ".join((spec.locks.get("text") or [])[1:] if spec.title_text else spec.locks.get("text") or []))
        self.never.setText(" | ".join(spec.never))
        self.duration.setValue(spec.duration_s)
        self.fps.setCurrentText(f"{spec.fps:g}")
        self.autonomy.setCurrentText(spec.autonomy.value)
        self.run_mode.setCurrentText(spec.run_mode.value)
        for key, slider in self.sliders.items():
            slider.set_value(getattr(spec.sliders, key))
        self.captions.setChecked(spec.captions)
        for i in range(self.footage_list.count()):
            item = self.footage_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole) in spec.footage_ids else Qt.Unchecked)
        last_run = (project.get("data_json") or {}).get("last_run")
        if last_run:
            try:
                self.service.load_run(last_run)
                self.run_id = last_run
                self._render_steer()
            except KeyError:
                pass

    def _save(self) -> str | None:
        try:
            spec = self._spec()
        except Exception as exc:
            self.bridge.toast.emit(f"Brief is invalid: {exc}", theme.ERR)
            return None
        if not spec.footage_ids:
            self.bridge.toast.emit("Select at least one analysed footage clip.", theme.WARN)
            return None
        if self.project_id:
            self.service.update_spec(self.project_id, spec)
            self.service.library.update_project(self.project_id, name=self.name.text().strip() or "Edit")
        else:
            project = self.service.create_project(self.name.text().strip() or "Edit", spec, footage=spec.footage_ids, profile_id=spec.profile_id)
            self.project_id = project["id"]
            self._refresh_projects()
        self.bridge.set_project(self.project_id)
        return self.project_id

    def _preflight(self, project_id: str | None = None) -> dict | None:
        project_id = project_id or self._save()
        if not project_id:
            return None
        try:
            result = self.service.preflight(project_id)
        except Exception as exc:
            self.bridge.toast.emit(f"Preflight failed: {exc}", theme.ERR)
            return None
        clear_layout(self.preflight_body)
        estimate = ", ".join(f"{k.replace('_', ' ')} {v:.0f} min" for k, v in result["estimate_min"].items() if v >= 0.5)
        self.preflight_body.addWidget(label(f"About {result['total_h']:.1f} h — {estimate}", "dim", wrap=True))
        for text, color in [(t, theme.ERR) for t in result["blockers"]] + [(t, theme.WARN) for t in result["warnings"]] + [(t, theme.OK) for t in result["good"]]:
            line = label(("✕ " if color == theme.ERR else "! " if color == theme.WARN else "✓ ") + text, None, wrap=True)
            line.setStyleSheet(f"color: {color};")
            self.preflight_body.addWidget(line)
        return result

    def _plan(self) -> None:
        project_id = self._save()
        if not project_id:
            return
        checked = self._preflight(project_id)
        if checked and checked["blockers"]:
            self.bridge.toast.emit("Preflight found a blocker — see the Preflight card.", theme.ERR)
            return
        state, _handle = self.service.plan(project_id, use_vlm=self.use_vlm.isChecked())
        self.run_id = state.run_id
        self.bridge.set_run(state.run_id)
        self.summary.setText("Planning…")
        self.bridge.toast.emit("Planning: retrieving style, ranking music, solving cuts and looks.", theme.LIVE)

    def _on_event(self, payload: dict) -> None:
        if payload.get("run_id") != self.run_id:
            return
        if payload.get("action") == "plan_ready":
            self._render_steer()
        elif payload.get("action") == "preview_ready":
            self.player.load(payload.get("details", {}).get("path"), autoplay=True)
        elif payload.get("stage") == "masks" and payload.get("status") in {"verified", "warning"}:
            self.btn_mattes.setText("Mattes ready")

    # ------------------------------------------------------- steer render
    def _render_steer(self) -> None:
        if not self.run_id:
            return
        state = self.service.runs.get(self.run_id)
        steer = self.service.steer(self.run_id)
        if not steer or not state or not state.edg:
            return
        edg = state.edg
        self.steer_empty.setVisible(False)
        self.steer_body.setVisible(True)
        prov = edg.provenance or {}
        director = prov.get("assembly_source", "deterministic")
        self.summary.setText(f"{len(edg.shots)} shots · {edg.duration_frames / edg.fps:.1f}s · {len(edg.texts)} title(s) · {len(edg.transitions)} transition(s) · director: {director}")
        self.warnings.setText("<br>".join(f"⚠ {w}" for w in (state.warnings or [])[:8]))
        self.btn_mattes.setVisible(bool(edg.mattes))
        self.strip.set_shots(steer.get("strip", []), (edg.music.beat_grid_s if edg.music else []))
        clear_layout(self.thumbs)
        for shot in steer.get("strip", []):
            col = QVBoxLayout()
            col.addWidget(Thumb(shot.get("thumb"), QSize(68, 120)))
            col.addWidget(label(f"{shot['role'] or ''} {shot['duration_s']:.1f}s", "faint"))
            w = QWidget()
            w.setLayout(col)
            w.setToolTip(shot.get("intent") or "")
            self.thumbs.addWidget(w)
        self.thumbs.addStretch(1)
        self._render_inspired(steer)
        self._render_effects(steer)
        self._render_looks(steer)
        self._render_music(steer)
        if state.preview:
            self.player.load(state.preview)

    def _render_inspired(self, steer: dict) -> None:
        clear_layout(self.inspired_card.body)
        self.inspired_card.body.addWidget(label("Inspired by", "h3"))
        lib = self.service.library
        for card in steer.get("references", [])[:6]:
            media = lib.get_media(card["reference_id"]) or {}
            row = QHBoxLayout()
            row.addWidget(Thumb(media.get("thumb"), QSize(40, 72), radius=6))
            col = QVBoxLayout()
            col.addWidget(label((media.get("title") or card["reference_id"])[:46], "h3"))
            badges = " ".join(
                f"<span style='color:{theme.INFO if weight >= 0.5 else theme.TEXT_FAINT}; border:1px solid; border-radius:8px;'>&nbsp;{dim} {weight:.1f}&nbsp;</span>"
                for dim, weight in list(card["dimensions"].items())[:8]
            )
            col.addWidget(label(badges, wrap=True))
            techniques = ", ".join(t["technique"].replace("_", " ") for t in card.get("techniques", [])[:5])
            if techniques:
                col.addWidget(label(f"techniques: {techniques}", "faint", wrap=True))
            row.addLayout(col, 1)
            off = button("Exclude", kind="ghost", on_click=lambda _=False, rid=card["reference_id"]: self._exclude_ref(rid))
            row.addWidget(off)
            self.inspired_card.body.addLayout(row)
        rules = [r for r in steer.get("rules", []) if r.get("statement")]
        if rules:
            self.inspired_card.body.addWidget(label("Rules applied", "h3"))
            for rule in rules[:6]:
                self.inspired_card.body.addWidget(label(f"{'✓' if rule.get('state') == 'approved' else '○'} {rule['statement']}", "faint", wrap=True))

    def _render_effects(self, steer: dict) -> None:
        clear_layout(self.effects_card.body)
        self.effects_card.body.addWidget(label("Planned techniques", "h3"))
        self.effects_card.body.addWidget(label("Every effect states the problem it solves. Untick to forbid it and re-plan.", "faint", wrap=True))
        self._effect_boxes: list[tuple[QCheckBox, dict]] = []
        for item in steer.get("planned_effects", []):
            box = QCheckBox(f"{item['name'].replace('_', ' ')} @ {item.get('at')} — {item.get('purpose', '')}" + (f"  “{item['text']}”" if item.get("text") else ""))
            box.setChecked(True)
            box.setToolTip(f"confidence {item.get('confidence', 0):.2f}")
            self._effect_boxes.append((box, item))
            self.effects_card.body.addWidget(box)
        for skipped in steer.get("skipped_effects", [])[:6]:
            self.effects_card.body.addWidget(label(f"✗ {skipped['effect']} at {skipped['at']}: {skipped['reason']}", "faint", wrap=True))
        self.effects_card.body.addWidget(button("Apply choices && re-plan", icon_name="fa5s.filter", on_click=self._apply_effect_choices))

    def _render_looks(self, steer: dict) -> None:
        clear_layout(self.looks_card.body)
        self.looks_card.body.addWidget(label("Solved looks (before → after)", "h3"))
        if steer.get("color_intent"):
            self.looks_card.body.addWidget(label(f"“{steer['color_intent']}”", "faint", wrap=True))
        flow = FlowWidget(spacing=10)
        for look in steer.get("looks", [])[:6]:
            pair = QWidget()
            pl = QHBoxLayout(pair)
            pl.setContentsMargins(0, 0, 0, 0)
            pl.setSpacing(2)
            pl.addWidget(Thumb(look["before"], QSize(64, 114), radius=6))
            pl.addWidget(Thumb(look["after"], QSize(64, 114), radius=6))
            pair.setToolTip(look.get("rationale", ""))
            flow.addWidget(pair)
        self.looks_card.body.addWidget(flow)

    def _render_music(self, steer: dict) -> None:
        clear_layout(self.music_card.body)
        self.music_card.body.addWidget(label("Music", "h3"))
        chosen = (steer.get("chosen_music") or {}).get("media_id")
        if not steer.get("music"):
            self.music_card.body.addWidget(label("No analysed licensed music: a silent guide grid is used and a sync marker added for your platform sound.", "dim", wrap=True))
        for cand in steer.get("music", [])[:5]:
            row = QHBoxLayout()
            col = QVBoxLayout()
            col.addWidget(label(f"{'▶ ' if cand['media_id'] == chosen else ''}{cand['title']} — {cand['bpm']:.0f} BPM · score {cand['score']:.2f}", "h3"))
            col.addWidget(label("; ".join(cand.get("reasons", [])[:3]) + ("  ⚠ " + cand["warnings"][0] if cand.get("warnings") else ""), "faint", wrap=True))
            row.addLayout(col, 1)
            if cand["media_id"] != chosen:
                row.addWidget(button("Use", kind="ghost", on_click=lambda _=False, mid=cand["media_id"]: self._use_music(mid)))
            self.music_card.body.addLayout(row)

    # ---------------------------------------------------------- actions
    def _exclude_ref(self, ref_id: str) -> None:
        for row in range(self.ref_table.rowCount()):
            if self.ref_table.item(row, 1).data(Qt.UserRole) == ref_id:
                self.ref_table.item(row, 0).setCheckState(Qt.Unchecked)
        self.bridge.toast.emit("Reference excluded — re-plan to apply.", theme.WARN)

    def _apply_effect_choices(self) -> None:
        never = [t.strip() for t in self.never.text().split("|") if t.strip()]
        for box, item in getattr(self, "_effect_boxes", []):
            if not box.isChecked():
                rule = f"no {item['name'].replace('_', ' ')}"
                if rule not in never:
                    never.append(rule)
                if item["kind"] == "text":
                    self.sliders["typography_density"].set_value(max(0.0, self.sliders["typography_density"].value() - 0.2))
                if item["kind"] == "transition":
                    self.sliders["motion_effects"].set_value(max(0.0, self.sliders["motion_effects"].value() - 0.2))
        self.never.setText(" | ".join(never))
        self._plan()

    def _use_music(self, media_id: str) -> None:
        idx = self.music.findData(media_id)
        if idx >= 0:
            self.music.setCurrentIndex(idx)
        self._plan()

    def _preview(self) -> None:
        if self.run_id:
            self.service.render_preview(self.run_id)
            self.bridge.toast.emit("Rendering the preview…", theme.LIVE)

    def _mattes(self) -> None:
        if self.run_id:
            self.service.generate_mattes(self.run_id)
            self.bridge.toast.emit("Generating subject mattes (SAM 2.1). Low-confidence mattes go to review.", theme.LIVE)

    def _build(self) -> None:
        if not self.run_id:
            return
        state = self.service.runs[self.run_id]
        if state.edg and any(m.method == "pending" for m in state.edg.mattes):
            self.service.generate_mattes(self.run_id)
        self.service.execute(self.run_id, mode=RunMode(self.run_mode.currentText()), autonomy=AutonomyMode(self.autonomy.currentText()))
        self.bridge.set_run(self.run_id)
        self.window.navigate("mission")
