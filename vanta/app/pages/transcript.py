"""Transcript Editor (manual 12.3): edit footage by editing its words."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vanta.analysis.transcribe import Transcript
from vanta.app import theme
from vanta.app.widgets.common import Card, EmptyState, FlowWidget, button, label, scroll
from vanta.solvers import textedit


class WordChip(QPushButton):
    def __init__(self, word, on_toggle):
        super().__init__(word.text)
        self.word = word
        self.dropped = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{word.start_s:.2f}–{word.end_s:.2f}s")
        self.clicked.connect(lambda: on_toggle(self))
        self.refresh()

    def set_dropped(self, dropped: bool) -> None:
        self.dropped = dropped
        self.refresh()

    def refresh(self) -> None:
        if self.dropped:
            style = f"background: #2a1416; color: {theme.TEXT_FAINT}; text-decoration: line-through; border: 1px solid #4a2226;"
        else:
            style = f"background: {theme.PANEL_HI}; color: {theme.TEXT}; border: 1px solid {theme.BORDER};"
        self.setStyleSheet(f"QPushButton {{ {style} border-radius: 6px; padding: 3px 7px; font-size: 13px; font-weight: 500; }}")


class TranscriptPage(QWidget):
    def __init__(self, bridge, window):
        super().__init__()
        self.bridge = bridge
        self.window = window
        self.service = bridge.service
        self.chips: list[WordChip] = []
        self.transcript: Transcript | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 14, 24, 14)
        layout.setSpacing(12)
        top = Card(padding=14)
        row = QHBoxLayout()
        row.addWidget(label("Clip", "dim"))
        self.clip = QComboBox()
        self.clip.setMinimumWidth(320)
        self.clip.currentIndexChanged.connect(self._load)
        row.addWidget(self.clip)
        row.addStretch(1)
        self.stats = label("", "dim")
        row.addWidget(self.stats)
        top.body.addLayout(row)
        cmds = QHBoxLayout()
        for text, command in (("Remove fillers", "remove filler words"), ("Remove repeats", "remove repeated phrases")):
            cmds.addWidget(button(text, icon_name="fa5s.cut", on_click=lambda _=False, c=command: self._command(c)))
        self.keep = QLineEdit()
        self.keep.setPlaceholderText("only keep sentences mentioning… (e.g. the bike, the ride)")
        cmds.addWidget(self.keep, 1)
        cmds.addWidget(button("Keep", on_click=lambda: self._command(f"only keep mentions of {self.keep.text()}")))
        cmds.addWidget(button("Reset", kind="ghost", on_click=self._reset))
        top.body.addLayout(cmds)
        build = QHBoxLayout()
        build.addWidget(label("Target length", "dim"))
        self.target = QDoubleSpinBox()
        self.target.setRange(0, 600)
        self.target.setSuffix(" s (0 = as edited)")
        build.addWidget(self.target)
        build.addStretch(1)
        build.addWidget(button("Build text-based edit", kind="primary", icon_name="fa5s.film", on_click=self._build))
        top.body.addLayout(build)
        top.body.addWidget(label("Click words to strike them out. Cuts get ~40 ms pre / ~80 ms post handles snapped to audio zero-crossings; linked audio stays in sync; captions are regenerated from the edited transcript.", "faint", wrap=True))
        layout.addWidget(top)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        self.flow = FlowWidget(spacing=6)
        holder_layout.addWidget(self.flow)
        holder_layout.addStretch(1)
        self.empty = EmptyState("fa5s.align-left", "No transcript", "Analyse footage with speech: VANTA transcribes it with word timestamps (Whisper large-v3-turbo).")
        layout.addWidget(self.empty)
        layout.addWidget(scroll(holder), 1)
        bridge.library_changed.connect(self.on_show)

    def on_show(self) -> None:
        from vanta.workers.service import media_analysis

        current = self.clip.currentData()
        self.clip.blockSignals(True)
        self.clip.clear()
        for media in self.service.library.list_media("footage"):
            analysis = media_analysis(media["id"], self.service) or {}
            tr = analysis.get("transcript") or {}
            if tr and not tr.get("error") and any(seg.get("words") for seg in tr.get("segments", [])):
                words = sum(len(seg.get("words", [])) for seg in tr["segments"])
                self.clip.addItem(f"{media.get('title')} · {words} words", media["id"])
        if current:
            self.clip.setCurrentIndex(max(0, self.clip.findData(current)))
        self.clip.blockSignals(False)
        self._load()

    def _load(self) -> None:
        from vanta.workers.service import media_analysis

        self.flow.clear()
        self.chips = []
        media_id = self.clip.currentData()
        self.empty.setVisible(media_id is None)
        if not media_id:
            return
        analysis = media_analysis(media_id, self.service) or {}
        self.transcript = Transcript.from_dict(analysis["transcript"])
        for word in textedit.flatten(self.transcript):
            chip = WordChip(word, self._toggle)
            self.chips.append(chip)
            self.flow.addWidget(chip)
        self._stats()

    def _toggle(self, chip: WordChip) -> None:
        chip.set_dropped(not chip.dropped)
        self._stats()

    def _dropped(self) -> set[int]:
        return {c.word.index for c in self.chips if c.dropped}

    def _stats(self) -> None:
        if not self.transcript:
            return
        words = textedit.flatten(self.transcript)
        kept = textedit.kept_ranges(words, self._dropped())
        total = sum(r.end_s - r.start_s for r in kept)
        self.stats.setText(f"{len(words) - len(self._dropped())}/{len(words)} words · ≈{total:.1f}s kept")

    def _command(self, command: str) -> None:
        if not self.transcript:
            return
        drop, understood = textedit.apply_command(self.transcript, command)
        for chip in self.chips:
            if chip.word.index in drop:
                chip.set_dropped(True)
        self._stats()
        self.bridge.toast.emit(understood, theme.OK if drop else theme.WARN)

    def _reset(self) -> None:
        for chip in self.chips:
            chip.set_dropped(False)
        self._stats()

    def _build(self) -> None:
        media_id = self.clip.currentData()
        project_id = self.bridge.current_project
        if not media_id:
            return
        if not project_id:
            from vanta.core.schemas import EditSpec

            spec = EditSpec(goal="text-based edit", duration_s=max(1.0, self.target.value() or 60), aspect="9:16", fps=30, footage_ids=[media_id])
            project_id = self.service.create_project("Text edit", spec, footage=[media_id])["id"]
            self.bridge.set_project(project_id)
        try:
            state = self.service.text_edit(project_id, media_id, self._dropped(), target_duration_s=self.target.value() or None)
        except Exception as exc:
            self.bridge.toast.emit(str(exc), theme.ERR)
            return
        self.bridge.set_run(state.run_id)
        self.service.render_preview(state.run_id)
        self.window.navigate("review")
