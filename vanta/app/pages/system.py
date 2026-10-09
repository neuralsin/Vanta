"""System: Resolve connection + compatibility gates, models, hardware and settings."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vanta import __version__
from vanta.app import theme
from vanta.app.widgets.common import Card, button, label, scroll, status_chip
from vanta.resolve.ledger import OPERATION_GATES, load_ledger

GATE_TITLES = {
    "G1": "Build: import, timeline, source trims, tracks, markers, idempotence",
    "G2": "Interchange: FCPXML import with exact durations",
    "G3": "Fusion text template + OCR round trip",
    "G3b": "Text behind subject",
    "G4": "Grade primitives: LUT/CDL/DRX, export-verified < 1 ΔE",
    "G4b": "8-node editable grade (TECH…POLISH) from your template, export-verified < 1 ΔE",
    "G6b": "Magic Mask tracked on the SKIN node only (people isolated for the colourist)",
    "G5": "Subject separation (external matte node)",
    "G6": "SAM mattes into the media pool",
    "G8": "Text-based edit, A/V sync",
    "G9": "Benchmarks (time, peak VRAM)",
    "G10": "Version drift guard",
    "G11": "Live-follow (page switching)",
    "G12": "Transitions API (21.1)",
    "G13": "Speed API (21.1)",
    "G14": "3D camera solve import",
    "G15": "Registry scan (fonts, LUTs, effects)",
    "G16": "Failure injection & rollback",
}


class SystemPage(QWidget):
    _models_ready = Signal(list)

    def __init__(self, bridge, window):
        super().__init__()
        self.bridge = bridge
        self.window = window
        self.service = bridge.service
        self.settings = self.service.settings
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 10, 24, 14)
        tabs = QTabWidget()
        tabs.addTab(scroll(self._resolve_tab()), "DaVinci Resolve")
        tabs.addTab(scroll(self._models_tab()), "Models")
        tabs.addTab(scroll(self._settings_tab()), "Settings")
        tabs.addTab(scroll(self._about_tab()), "About")
        layout.addWidget(tabs)
        bridge.resolve.connect(self._on_resolve)
        bridge.event.connect(self._on_event)
        self._models_ready.connect(self._render_models)

    # ------------------------------------------------------------ resolve
    def _resolve_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(14)
        card = Card()
        card.body.addWidget(label("Connection", "h2"))
        self.conn = label("checking…", "dim", wrap=True)
        card.body.addWidget(self.conn)
        card.body.addWidget(label("Requirements: DaVinci Resolve Studio 21.1+, Preferences › System › General › External scripting using: Local. VANTA talks to Resolve through its own Python (version-matched) and never exposes scripting on the network.", "faint", wrap=True))
        row = QHBoxLayout()
        self.resolve_root = QLineEdit(str(self.settings.resolve_root))
        row.addWidget(label("Install folder", "dim"))
        row.addWidget(self.resolve_root, 1)
        row.addWidget(button("Reconnect", icon_name="fa5s.plug", on_click=self._reconnect))
        card.body.addLayout(row)
        layout.addWidget(card)
        gates = Card()
        head = QHBoxLayout()
        head.addWidget(label("Compatibility gates", "h2"))
        head.addStretch(1)
        head.addWidget(button("Install in-Resolve HUD", icon_name="fa5s.window-maximize", on_click=self._install_hud, tooltip="Adds Workspace › Scripts › Utility › VANTA HUD inside Resolve"))
        head.addWidget(button("Capture 8-node grade template", icon_name="fa5s.palette", on_click=self._capture_drx, tooltip="Build 8 serial nodes labelled TECH, BALANCE, TONE, LOOK, SEP, BG, SKIN, POLISH on any clip, park the playhead on it, then capture"))
        head.addWidget(button("Run compatibility check", kind="primary", icon_name="fa5s.vial", on_click=self._run_gates))
        gates.body.addLayout(head)
        gates.body.addWidget(label("VANTA refuses any operation whose gate has not passed on your exact Resolve version and uses the manual's fallback instead (e.g. pre-rendered overlays when Fusion text is unverified). The check runs in a disposable VANTA_GATE_ project with synthetic media, then reloads your project.", "faint", wrap=True))
        self.gate_table = QTableWidget(0, 4)
        self.gate_table.setHorizontalHeaderLabels(["Gate", "What it proves", "Status", "Evidence"])
        self.gate_table.verticalHeader().setVisible(False)
        self.gate_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.gate_table.setColumnWidth(1, 360)
        self.gate_table.setMinimumHeight(520)
        self.gate_table.setWordWrap(True)
        gates.body.addWidget(self.gate_table)
        self.routes = label("", "faint", wrap=True)
        gates.body.addWidget(self.routes)
        layout.addWidget(gates)
        layout.addStretch(1)
        return w

    def _render_gates(self) -> None:
        ledger = load_ledger()
        self.gate_table.setRowCount(len(GATE_TITLES))
        for row, (gate, title) in enumerate(GATE_TITLES.items()):
            entry = ledger.gates.get(gate) or {}
            self.gate_table.setItem(row, 0, QTableWidgetItem(gate))
            self.gate_table.setItem(row, 1, QTableWidgetItem(title))
            self.gate_table.setCellWidget(row, 2, status_chip(entry.get("status", "NOT_RUN")))
            summary = (entry.get("evidence") or {}).get("summary", "")
            self.gate_table.setItem(row, 3, QTableWidgetItem(f"{summary} {('· ' + entry['at'][:16]) if entry.get('at') else ''}"))
            self.gate_table.setRowHeight(row, 40)
        current = self.bridge.last_resolve.get("version_string")
        lines = []
        for op, (_gate, fallback) in OPERATION_GATES.items():
            ok, _why = ledger.allows(op, current_version=current)
            lines.append(f"{'✓' if ok else '•'} {op.replace('_', ' ')}: {'live' if ok else 'fallback — ' + fallback}")
        self.routes.setText(f"Ledger version: {ledger.resolve_version or '—'}" + ("  ⚠ differs from running Resolve: re-run the check" if current and ledger.resolve_version and current != ledger.resolve_version else "") + "<br>" + "<br>".join(lines))

    def _on_resolve(self, status: dict) -> None:
        if status.get("connected"):
            self.conn.setText(f"<span style='color:{theme.OK}'>●</span> Connected to {status.get('product') or 'DaVinci Resolve'} {status.get('version_string') or ''} — project “{status.get('project_name')}”, timeline “{status.get('timeline_name')}”, page {status.get('page')}{' · rendering' if status.get('rendering') else ''}")
        else:
            self.conn.setText(f"<span style='color:{theme.ERR}'>●</span> Not connected: {status.get('reason')}")

    def _reconnect(self) -> None:
        from pathlib import Path

        self.settings.resolve_root = Path(self.resolve_root.text().strip())
        self.settings.save()
        self.bridge.refresh_resolve()

    def _run_gates(self) -> None:
        if not self.bridge.last_resolve.get("connected"):
            QMessageBox.warning(self, "Resolve not connected", "Open DaVinci Resolve Studio and enable Local external scripting first.")
            return
        answer = QMessageBox.question(
            self,
            "Run the compatibility check?",
            "VANTA will save your current project, create a disposable project named VANTA_GATE_<time>, import synthetic test media, exercise each gate, then reload your project.\n\nIt takes a few minutes. Continue?",
        )
        if answer != QMessageBox.Yes:
            return
        self.service.gate_check()
        self.bridge.toast.emit("Compatibility check running — watch Mission Control › Library jobs.", theme.LIVE)

    def _capture_drx(self) -> None:
        if not self.bridge.last_resolve.get("connected"):
            QMessageBox.warning(self, "Resolve not connected", "Open DaVinci Resolve Studio and enable Local external scripting first.")
            return
        answer = QMessageBox.question(
            self,
            "Capture the 8-node grade template?",
            "On any clip in Resolve, build 8 serial nodes labelled TECH, BALANCE, TONE, LOOK, SEP, BG, SKIN, POLISH (leave them neutral) and park the playhead on that clip.\n\n"
            "VANTA grabs a still of that grade, exports it as a .drx template and deletes the still. Then run the compatibility check: gate G4b verifies the tree and VANTA builds every edit as an editable 8-node grade.\n\nCapture now?",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            result = self.service.capture_drx_template()
        except Exception as exc:
            QMessageBox.warning(self, "Template not captured", str(exc))
            return
        self.bridge.toast.emit(f"8-node template captured ({result['path']}). Run the compatibility check to verify it (G4b).", theme.OK)

    def _install_hud(self) -> None:
        from vanta.app.hud_server import install_hud_script

        path = install_hud_script(self.settings.hud_port)
        QMessageBox.information(self, "HUD installed", f"Installed:\n{path}\n\nIn Resolve: Workspace › Scripts › Utility › VANTA HUD.")

    def _on_event(self, ev: dict) -> None:
        if (ev.get("stage") or "").startswith("gates."):
            self._render_gates()

    # ------------------------------------------------------------ models
    def _models_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        card = Card()
        head = QHBoxLayout()
        head.addWidget(label("Model roles", "h2"))
        head.addStretch(1)
        head.addWidget(button("Refresh", icon_name="fa5s.sync", on_click=self._refresh_models))
        head.addWidget(button("Install all required", kind="primary", icon_name="fa5s.download", on_click=self._install_all))
        card.body.addLayout(head)
        card.body.addWidget(label("Specialists, not one giant model: the VLM is the creative director; deterministic solvers own every number. One heavy model is resident at a time on your 6 GB GPU (keep_alive 0, OLLAMA_NUM_PARALLEL=1); inference pauses while Resolve renders.", "faint", wrap=True))
        self.model_table = QTableWidget(0, 7)
        self.model_table.setHorizontalHeaderLabels(["Role", "Model", "Provider", "VRAM", "Licence", "Status", ""])
        self.model_table.verticalHeader().setVisible(False)
        self.model_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.model_table.setMinimumHeight(420)
        card.body.addWidget(self.model_table)
        layout.addWidget(card)
        director = Card()
        director.body.addWidget(label("Creative director", "h2"))
        form = QFormLayout()
        self.director = QComboBox()
        self.director.setEditable(True)
        self.director.addItems(["qwen3-vl:8b", "qwen3-vl:4b", "qwen3-vl:2b"])
        self.director.setCurrentText(self.settings.models.director_model)
        form.addRow("Ollama model", self.director)
        self.ollama = QLineEdit(self.settings.models.ollama_host)
        form.addRow("Ollama host", self.ollama)
        self.provider = QComboBox()
        self.provider.addItems(["ollama", "disabled"])
        self.provider.setCurrentText(self.settings.models.provider)
        form.addRow("Provider", self.provider)
        director.body.addLayout(form)
        director.body.addWidget(label("SAM 3 is gated: accept its licence on Hugging Face and set HF_TOKEN to enable it; SAM 2.1 is used otherwise. AirLLM can be evaluated for a larger director (see docs/DEFERRED_VERIFICATION.md).", "faint", wrap=True))
        director.body.addWidget(button("Save model settings", on_click=self._save_models))
        layout.addWidget(director)
        layout.addStretch(1)
        return w

    def _refresh_models(self) -> None:
        def work() -> None:
            from vanta.models.registry import refresh

            self._models_ready.emit(refresh(self.settings))

        threading.Thread(target=work, daemon=True).start()

    def _render_models(self, specs: list) -> None:
        self._specs = specs
        self.model_table.setRowCount(len(specs))
        for row, spec in enumerate(specs):
            self.model_table.setItem(row, 0, QTableWidgetItem(spec.role))
            self.model_table.setItem(row, 1, QTableWidgetItem(spec.repo))
            self.model_table.setItem(row, 2, QTableWidgetItem(spec.provider))
            self.model_table.setItem(row, 3, QTableWidgetItem(f"{spec.vram_gb:.1f} GB"))
            self.model_table.setItem(row, 4, QTableWidgetItem(spec.licence + (" (gated)" if spec.gated else "")))
            self.model_table.setCellWidget(row, 5, status_chip("done" if spec.installed else "pending"))
            if not spec.installed and spec.provider != "onnx":
                self.model_table.setCellWidget(row, 6, button("Install", kind="ghost", on_click=lambda _=False, s=spec: self._install(s)))
            self.model_table.item(row, 1).setToolTip(spec.detail or spec.notes)

    def _install(self, spec) -> None:
        from vanta.models.installer import install

        def job(handle):
            return install(spec, self.settings, progress=lambda status, frac: (setattr(handle, "message", status), setattr(handle, "progress", frac)))

        self.service.jobs.submit("run_library", "install", f"Install {spec.repo}", job, lane="light", retries=1)
        self.bridge.toast.emit(f"Installing {spec.repo}…", theme.LIVE)

    def _install_all(self) -> None:
        for spec in getattr(self, "_specs", []):
            if not spec.installed and not spec.optional and not spec.gated and spec.provider != "onnx":
                self._install(spec)

    def _save_models(self) -> None:
        self.settings.models.director_model = self.director.currentText().strip()
        self.settings.models.ollama_host = self.ollama.text().strip()
        self.settings.models.provider = self.provider.currentText()
        self.settings.save()
        self.bridge.toast.emit("Model settings saved.", theme.OK)

    # ------------------------------------------------------------ settings
    def _settings_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        card = Card()
        card.body.addWidget(label("Ingest", "h2"))
        form = QFormLayout()
        self.cookies = QComboBox()
        self.cookies.addItems(["none", "chrome", "edge", "firefox", "brave"])
        self.cookies.setCurrentText(self.settings.ingest.cookies_from_browser or "none")
        self.cookies.setToolTip("Instagram often requires a signed-in session. yt-dlp can read your browser's cookies for your own downloads.")
        form.addRow("Use browser cookies", self.cookies)
        self.keep_refs = QCheckBox("Keep downloaded reference videos after analysis")
        self.keep_refs.setChecked(self.settings.ingest.keep_reference_media)
        form.addRow("", self.keep_refs)
        self.max_len = QDoubleSpinBox()
        self.max_len.setRange(10, 1800)
        self.max_len.setValue(self.settings.ingest.max_reference_duration_s)
        self.max_len.setSuffix(" s")
        form.addRow("Max reference length", self.max_len)
        card.body.addLayout(form)
        layout.addWidget(card)
        card2 = Card()
        card2.body.addWidget(label("Editing defaults", "h2"))
        form2 = QFormLayout()
        self.watch = QDoubleSpinBox()
        self.watch.setRange(0, 10)
        self.watch.setSingleStep(0.1)
        self.watch.setValue(self.settings.edit.watch_delay_s)
        self.watch.setSuffix(" s")
        form2.addRow("WATCH delay between steps", self.watch)
        self.lufs = QDoubleSpinBox()
        self.lufs.setRange(-30, -6)
        self.lufs.setValue(self.settings.edit.loudness_target_lufs)
        self.lufs.setSuffix(" LUFS")
        form2.addRow("Loudness target", self.lufs)
        card2.body.addLayout(form2)
        layout.addWidget(card2)
        card3 = Card()
        card3.body.addWidget(label("Storage", "h2"))
        card3.body.addWidget(label(f"Data: {self.settings.data_dir}<br>Models: {self.settings.models_dir}<br>Runs: {self.settings.runs_dir}<br>Knowledge base (git-versioned): {self.settings.kb_dir}", "dim", wrap=True))
        row = QHBoxLayout()
        row.addWidget(button("Open data folder", on_click=lambda: os.startfile(str(self.settings.data_dir))))
        row.addWidget(button("Open runs folder", on_click=lambda: os.startfile(str(self.settings.runs_dir))))
        row.addStretch(1)
        card3.body.addLayout(row)
        layout.addWidget(card3)
        save = button("Save settings", kind="primary", icon_name="fa5s.save", on_click=self._save_settings)
        layout.addWidget(save, 0, Qt.AlignLeft)
        layout.addStretch(1)
        return w

    def _save_settings(self) -> None:
        cookies = self.cookies.currentText()
        self.settings.ingest.cookies_from_browser = None if cookies == "none" else cookies
        self.settings.ingest.keep_reference_media = self.keep_refs.isChecked()
        self.settings.ingest.max_reference_duration_s = self.max_len.value()
        self.settings.edit.watch_delay_s = self.watch.value()
        self.settings.edit.loudness_target_lufs = self.lufs.value()
        self.settings.save()
        self.bridge.toast.emit("Settings saved.", theme.OK)

    def _about_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        card = Card()
        card.body.addWidget(label(f"VANTA {__version__}", "h1"))
        card.body.addWidget(label("A local, style-learning, reasoning co-editor that drives DaVinci Resolve Studio in front of your eyes. Supervised by design: nothing irreversible, every decision explained, every run replayable. It will not judge acting, guarantee the best take, build grades outside the template route, or replace your final eye.", "dim", wrap=True))
        card.body.addWidget(label("Local-first: no cloud calls. Reference reels are others' work — VANTA keeps derived features and thumbnails for your private analysis and never reuses their music.", "faint", wrap=True))
        layout.addWidget(card)
        layout.addStretch(1)
        return w

    def on_show(self) -> None:
        self._render_gates()
        self._refresh_models()
        self._on_resolve(self.bridge.last_resolve)
