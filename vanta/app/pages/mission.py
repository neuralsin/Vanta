"""Mission Control (manual 4.5): watch VANTA work, live, and stay in control."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vanta.app import theme
from vanta.app.widgets.common import Card, Pill, Thumb, button, icon, label
from vanta.core.schemas import RunMode
from vanta.live.recorder import FlightRecorder

STATUS_ICON = {
    "planned": ("fa5s.circle", theme.INFO),
    "running": ("fa5s.spinner", theme.LIVE),
    "verified": ("fa5s.check-circle", theme.OK),
    "failed": ("fa5s.times-circle", theme.ERR),
    "rolled_back": ("fa5s.undo", theme.PURPLE),
    "skipped": ("fa5s.forward", theme.TEXT_FAINT),
    "warning": ("fa5s.exclamation-triangle", theme.WARN),
    "info": ("fa5s.info-circle", theme.TEXT_DIM),
}
STAGES = [
    ("plan", "Plan"),
    ("masks", "Mattes"),
    ("prerender", "Pre-renders"),
    ("snapshot", "Snapshot"),
    ("structure", "Timeline"),
    ("assembly", "Cuts"),
    ("audio", "Audio"),
    ("transitions", "Transitions"),
    ("grade", "Grade"),
    ("fusion", "Titles & effects"),
    ("markers", "Breadcrumbs"),
    ("render", "Proxy render"),
    ("qc", "QC & repair"),
    ("learning", "Learning"),
]


class MissionPage(QWidget):
    def __init__(self, bridge, window):
        super().__init__()
        self.bridge = bridge
        self.window = window
        self.service = bridge.service
        self.run_id: str | None = None
        self.events: list[dict] = []
        self.recorder: FlightRecorder | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 14, 22, 14)
        layout.setSpacing(12)
        layout.addWidget(self._header())
        layout.addWidget(self._status_line())
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._left())
        split.addWidget(self._log())
        split.addWidget(self._right())
        split.setSizes([300, 760, 420])
        layout.addWidget(split, 1)
        bridge.event.connect(self._on_event)
        bridge.run_changed.connect(self.select_run)
        bridge.telemetry.connect(self._on_telemetry)

    # ------------------------------------------------------------- layout
    def _header(self) -> QWidget:
        card = Card(padding=12)
        row = QHBoxLayout()
        row.addWidget(label("Run", "dim"))
        self.run_combo = QComboBox()
        self.run_combo.setMinimumWidth(360)
        self.run_combo.currentIndexChanged.connect(lambda _i: self.select_run(self.run_combo.currentData()))
        row.addWidget(self.run_combo)
        self.mode = QComboBox()
        self.mode.addItems([m.value for m in RunMode])
        self.mode.setToolTip("Switch WATCH / STEP / TURBO while running")
        self.mode.currentTextChanged.connect(self._set_mode)
        row.addWidget(self.mode)
        row.addStretch(1)
        self.btn_pause = button("Pause", icon_name="fa5s.pause", on_click=lambda: self._control("pause"))
        self.btn_resume = button("Resume", icon_name="fa5s.play", on_click=lambda: self._control("resume"))
        self.btn_step = button("Next step", icon_name="fa5s.step-forward", on_click=lambda: self._control("step"))
        self.btn_skip = button("Skip", icon_name="fa5s.forward", on_click=lambda: self._control("skip"))
        self.btn_stop = button("Stop", kind="danger", icon_name="fa5s.stop", on_click=lambda: self._control("stop"))
        self.btn_undo = button("Undo last", icon_name="fa5s.undo", on_click=lambda: self._control("undo"))
        for b in (self.btn_pause, self.btn_resume, self.btn_step, self.btn_skip, self.btn_stop, self.btn_undo):
            row.addWidget(b)
        self.btn_record = button("Record", kind="ghost", icon_name="fa5s.dot-circle", on_click=self._toggle_record, tooltip="Flight recorder: screen-record the Resolve window for replay")
        row.addWidget(self.btn_record)
        card.body.addLayout(row)
        return card

    def _status_line(self) -> QWidget:
        card = Card(padding=12)
        row = QHBoxLayout()
        self.stage = label("Idle", "h2")
        row.addWidget(self.stage, 2)
        self.conf = Pill("conf —", theme.TEXT_FAINT)
        self.model = Pill("model idle", theme.TEXT_FAINT)
        self.gpu = Pill("GPU —", theme.TEXT_FAINT)
        row.addWidget(self.conf)
        row.addWidget(self.model)
        row.addWidget(self.gpu)
        card.body.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setProperty("live", True)
        card.body.addWidget(self.progress)
        self.message = label("", "dim", wrap=True)
        card.body.addWidget(self.message)
        return card

    def _left(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 8, 0)
        plan = Card()
        plan.body.addWidget(label("PLAN", "eyebrow"))
        self.plan_tree = QTreeWidget()
        self.plan_tree.setHeaderHidden(True)
        self.plan_tree.setRootIsDecorated(False)
        self.plan_tree.setIconSize(QSize(14, 14))
        self._stage_items: dict[str, QTreeWidgetItem] = {}
        for key, title in STAGES:
            item = QTreeWidgetItem([title])
            item.setIcon(0, icon("fa5s.circle", theme.TEXT_FAINT))
            self.plan_tree.addTopLevelItem(item)
            self._stage_items[key] = item
        plan.body.addWidget(self.plan_tree)
        layout.addWidget(plan, 3)
        alerts = Card()
        alerts.body.addWidget(label("ALERTS", "eyebrow"))
        self.alerts = QListWidget()
        alerts.body.addWidget(self.alerts)
        layout.addWidget(alerts, 2)
        return w

    def _log(self) -> QWidget:
        card = Card()
        row = QHBoxLayout()
        row.addWidget(label("LIVE LOG", "eyebrow"))
        row.addStretch(1)
        self.filter = QComboBox()
        self.filter.addItems(["everything", "decisions & results", "warnings & errors"])
        self.filter.currentIndexChanged.connect(self._rerender_log)
        row.addWidget(self.filter)
        card.body.addLayout(row)
        self.log = QListWidget()
        self.log.setIconSize(QSize(14, 14))
        self.log.setUniformItemSizes(False)
        self.log.currentRowChanged.connect(self._select_event)
        card.body.addWidget(self.log)
        return card

    def _right(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 0, 0, 0)
        card = Card()
        card.body.addWidget(label("BEFORE  |  AFTER", "eyebrow"))
        pair = QHBoxLayout()
        self.before = Thumb(None, QSize(170, 302))
        self.after = Thumb(None, QSize(170, 302))
        pair.addWidget(self.before)
        pair.addWidget(self.after)
        card.body.addLayout(pair)
        self.detail = label("Select a step in the log.", "dim", wrap=True)
        card.body.addWidget(self.detail)
        actions = QHBoxLayout()
        actions.addWidget(button("Accept", icon_name="fa5s.thumbs-up", on_click=lambda: self._feedback("thumbs_up")))
        actions.addWidget(button("Revert", icon_name="fa5s.undo", on_click=lambda: self._control("undo")))
        actions.addWidget(button("Why?", icon_name="fa5s.question-circle", on_click=self._why))
        actions.addWidget(button("Change this…", kind="primary", icon_name="fa5s.comment-dots", on_click=self._change_this))
        card.body.addLayout(actions)
        fb = QHBoxLayout()
        for kind, text in (("too_much", "Too much"), ("too_little", "Too little"), ("wrong_shot", "Wrong shot"), ("wrong_colour", "Wrong colour"), ("wrong_text", "Wrong text"), ("wrong_effect", "Wrong effect"), ("wrong_pacing", "Wrong pacing")):
            b = button(text, kind="ghost", on_click=lambda _=False, k=kind: self._feedback(k))
            b.setStyleSheet("padding: 4px 6px; font-size: 11px;")
            fb.addWidget(b)
        card.body.addLayout(fb)
        layout.addWidget(card)
        layout.addStretch(1)
        return w

    # ------------------------------------------------------------- runs
    def on_show(self) -> None:
        self._refresh_runs()

    def _refresh_runs(self) -> None:
        current = self.run_id or self.bridge.current_run
        self.run_combo.blockSignals(True)
        self.run_combo.clear()
        for row in self.service.events.list_runs(50):
            if row["run_id"] == "run_library":
                continue
            project = self.service.library.get_project(row["project_id"]) or {}
            self.run_combo.addItem(f"{project.get('name', row['project_id'])} · {row['created_at'][:16].replace('T', ' ')} · {row['status'].lower()}", row["run_id"])
        self.run_combo.addItem("Library jobs (analysis, downloads, gates)", "run_library")
        if current:
            idx = self.run_combo.findData(current)
            if idx >= 0:
                self.run_combo.setCurrentIndex(idx)
        self.run_combo.blockSignals(False)
        if self.run_combo.currentData():
            self.select_run(self.run_combo.currentData())

    def select_run(self, run_id: str | None) -> None:
        if not run_id or run_id == self.run_id:
            return
        self.run_id = run_id
        if run_id != "run_library":
            try:
                state = self.service.load_run(run_id)
                self.mode.blockSignals(True)
                self.mode.setCurrentText(state.control.run_mode.value)
                self.mode.blockSignals(False)
            except KeyError:
                pass
        self.events = self.service.events.list_events(run_id=run_id, limit=1000)
        for item in self._stage_items.values():
            item.setIcon(0, icon("fa5s.circle", theme.TEXT_FAINT))
            item.setText(0, item.text(0).split("  ·")[0])
        self.alerts.clear()
        for ev in self.events:
            self._track(ev)
        self._rerender_log()
        last = next((e for e in reversed(self.events) if e.get("message")), None)
        if last:
            self.stage.setText(f"{(last.get('stage') or '').replace('.', ' › ').title()}  ·  {last.get('status')}")
            self.message.setText(last.get("message") or "")
            self.progress.setValue(int((last.get("progress") or 0) * 1000))
        if self.run_combo.findData(run_id) < 0:
            self._refresh_runs()

    # ------------------------------------------------------------- events
    def _visible(self, ev: dict) -> bool:
        mode = self.filter.currentIndex()
        if mode == 1:
            return ev.get("status") in {"verified", "failed", "planned", "rolled_back", "skipped", "warning"}
        if mode == 2:
            return ev.get("status") in {"failed", "warning", "rolled_back"}
        return True

    def _row_text(self, ev: dict) -> str:
        ts = ev.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts).astimezone().strftime("%H:%M:%S")
        except ValueError:
            ts = ts[11:19]
        msg = ev.get("message") or f"{ev.get('action')} {json.dumps(ev.get('target') or {})}"
        because = ", ".join(ev.get("because") or [])[:120]
        conf = f"  conf {ev['confidence']:.2f}" if ev.get("confidence") is not None else ""
        return f"{ts}  [{ev.get('stage')}]  {msg}{conf}" + (f"\n          because {because}" if because else "") + (f"\n          ✗ {ev['error'][:200]}" if ev.get("error") else "")

    def _add_row(self, ev: dict) -> None:
        if not self._visible(ev):
            return
        glyph, color = STATUS_ICON.get(ev.get("status"), ("fa5s.circle", theme.TEXT_DIM))
        item = QListWidgetItem(icon(glyph, color), self._row_text(ev))
        item.setData(Qt.UserRole, ev)
        if ev.get("status") == "failed":
            item.setForeground(QColor(theme.ERR))
        elif ev.get("status") == "warning":
            item.setForeground(QColor(theme.WARN))
        self.log.addItem(item)

    def _rerender_log(self) -> None:
        self.log.clear()
        for ev in self.events[-800:]:
            self._add_row(ev)
        self.log.scrollToBottom()

    def _track(self, ev: dict) -> None:
        stage = (ev.get("stage") or "").split(".")[0]
        key = {"preview": "qc", "repair": "qc", "patch": "qc", "execute": "structure", "capability_gate": "structure", "rollback": "structure", "analysis": "plan", "retrieve": "plan", "music": "plan", "assembly": "plan", "polish": "plan", "text": "plan", "effects": "plan", "finish": "plan", "grade": "grade"}.get(stage, stage)
        item = self._stage_items.get(key)
        if item:
            status = ev.get("status")
            glyph, color = STATUS_ICON.get(status, ("fa5s.circle", theme.TEXT_FAINT))
            if status in {"running", "planned"}:
                glyph, color = ("fa5s.play-circle", theme.LIVE)
            item.setIcon(0, icon(glyph, color))
        if ev.get("status") in {"failed", "warning", "rolled_back"} or ev.get("action") == "approval_required":
            glyph, color = STATUS_ICON.get(ev.get("status"), ("fa5s.bell", theme.WARN))
            self.alerts.insertItem(0, QListWidgetItem(icon(glyph, color), (ev.get("message") or ev.get("error") or "")[:160]))

    def _on_event(self, ev: dict) -> None:
        if ev.get("run_id") != self.run_id:
            if self.run_combo.findData(ev.get("run_id")) < 0 and ev.get("run_id") != "run_library":
                self._refresh_runs()
            return
        self.events.append(ev)
        self._track(ev)
        at_bottom = self.log.verticalScrollBar().value() >= self.log.verticalScrollBar().maximum() - 4
        self._add_row(ev)
        if at_bottom:
            self.log.scrollToBottom()
        self.stage.setText(f"{(ev.get('stage') or '').replace('.', ' › ').title()}  ·  {ev.get('action', '').replace('_', ' ')}")
        if ev.get("message"):
            self.message.setText(ev["message"])
        if ev.get("progress") is not None:
            self.progress.setValue(int(ev["progress"] * 1000))
        if ev.get("confidence") is not None:
            c = ev["confidence"]
            self.conf.set(f"conf {c:.2f}", theme.OK if c >= 0.9 else theme.WARN if c >= 0.7 else theme.ERR)
        if ev.get("before_thumbnail") or ev.get("after_thumbnail"):
            self.before.set_path(ev.get("before_thumbnail"))
            self.after.set_path(ev.get("after_thumbnail"))

    def _on_telemetry(self, data: dict) -> None:
        if data.get("vram_total_gb"):
            self.gpu.set(f"GPU {data.get('gpu_util') or 0:.0f}% · {data['vram_used_gb']:.1f}/{data['vram_total_gb']:.1f} GB", theme.LIVE)
        resident = data.get("resident_model")
        self.model.set(f"{resident.split('/')[-1]}" if resident else "model idle", theme.LIVE if resident else theme.TEXT_FAINT)

    def _select_event(self, row: int) -> None:
        item = self.log.item(row)
        if not item:
            return
        ev = item.data(Qt.UserRole)
        self._selected = ev
        if ev.get("before_thumbnail"):
            self.before.set_path(ev["before_thumbnail"])
        if ev.get("after_thumbnail"):
            self.after.set_path(ev["after_thumbnail"])
        lines = [f"<b>{ev.get('action', '').replace('_', ' ')}</b> — {ev.get('status')}"]
        if ev.get("because"):
            lines.append("because " + ", ".join(ev["because"]))
        if ev.get("target"):
            lines.append("target " + ", ".join(f"{k}={v}" for k, v in ev["target"].items()))
        if ev.get("undo_token"):
            lines.append(f"undo token {ev['undo_token']}")
        self.detail.setText("<br>".join(lines))

    # ------------------------------------------------------------- actions
    def _control(self, action: str) -> None:
        if not self.run_id or self.run_id == "run_library":
            return
        try:
            self.service.control(self.run_id, action)
        except Exception as exc:
            self.bridge.toast.emit(str(exc), theme.WARN)

    def _set_mode(self, text: str) -> None:
        state = self.service.runs.get(self.run_id or "")
        if state:
            state.control.set_mode(RunMode(text))

    def _feedback(self, kind: str) -> None:
        if not self.run_id or self.run_id == "run_library":
            return
        ev = getattr(self, "_selected", None) or {}
        target = (ev.get("target") or {}).get("shot_id") or (ev.get("target") or {}).get("text_id")
        dimension = {"grade": "color", "fusion": "fusion", "assembly": "cut"}.get((ev.get("stage") or "").split(".")[0])
        self.service.feedback(self.run_id, kind, target=target, dimension=dimension)
        self.bridge.toast.emit("Thanks — recorded as preference evidence (never applied silently).", theme.OK)

    def _why(self) -> None:
        ev = getattr(self, "_selected", None)
        if not ev:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Why did VANTA do this?")
        dialog.resize(720, 560)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(ev.get("message") or ev.get("action"), "h2", wrap=True))
        layout.addWidget(label("because: " + (", ".join(ev.get("because") or []) or "—"), "dim", wrap=True))
        state = self.service.runs.get(self.run_id or "")
        target = (ev.get("target") or {}).get("shot_id")
        extra = {}
        if state and state.edg and target:
            try:
                shot = state.edg.shot(target)
                extra["shot"] = {"rationale": shot.rationale, "confidence": shot.confidence, "provenance": shot.provenance.model_dump(), "footage": shot.footage_shot_id}
                grade = state.edg.grade_for(target)
                if grade:
                    extra["grade"] = {"intent": grade.intent, "rules": grade.provenance.rules, "target_stats": grade.target_stats, "predicted_stats": grade.predicted_stats, "rationale": grade.rationale}
            except KeyError:
                pass
        text = QPlainTextEdit(json.dumps({"event": ev.get("details"), **extra}, indent=2, default=str))
        text.setReadOnly(True)
        layout.addWidget(text, 1)
        dialog.exec()

    def _change_this(self) -> None:
        ev = getattr(self, "_selected", None) or {}
        target = (ev.get("target") or {}).get("shot_id") or ""
        self.window.navigate("review")
        self.window.pages["review"].prefill_chat(f"{target}: " if target else "")

    def _toggle_record(self) -> None:
        if self.recorder and self.recorder.recording:
            path = self.recorder.stop()
            self.btn_record.setText("Record")
            self.bridge.toast.emit(f"Flight recording saved: {path}", theme.OK)
            return
        state = self.service.runs.get(self.run_id or "")
        folder = state.run_dir if state else self.service.settings.data_dir / "recordings"
        self.recorder = FlightRecorder(Path(folder) / "flight_recorder.mp4")
        try:
            self.recorder.start()
            self.btn_record.setText("Stop recording")
            self.bridge.toast.emit("Recording the Resolve window (flight recorder).", theme.LIVE)
        except Exception as exc:
            self.bridge.toast.emit(f"Recorder failed: {exc}", theme.ERR)
