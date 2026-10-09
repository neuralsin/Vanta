"""Review: decisions with reasons, QC findings, critique/repair, chat patches, corrections."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vanta.app import theme
from vanta.app.widgets.common import (
    Card,
    Chip,
    EmptyState,
    Thumb,
    button,
    clear_layout,
    label,
    scroll,
)
from vanta.app.widgets.video import VideoPlayer


class ReviewPage(QWidget):
    def __init__(self, bridge, window):
        super().__init__()
        self.bridge = bridge
        self.window = window
        self.service = bridge.service
        self.run_id: str | None = None
        self.pending = None
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._decisions())
        split.addWidget(self._center())
        split.addWidget(self._chat())
        split.setSizes([460, 620, 420])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.addWidget(split)
        bridge.run_changed.connect(self._set_run)
        bridge.event.connect(self._on_event)

    # ------------------------------------------------------------- panes
    def _decisions(self) -> QWidget:
        content = QWidget()
        self.dec_layout = QVBoxLayout(content)
        self.dec_layout.setContentsMargins(4, 4, 8, 4)
        self.dec_layout.setSpacing(10)
        return scroll(content)

    def _center(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 4, 8, 4)
        actions = QHBoxLayout()
        actions.addWidget(button("Run QC, critique && repair", kind="primary", icon_name="fa5s.clipboard-check", on_click=self._qc))
        actions.addWidget(button("Render preview", icon_name="fa5s.play-circle", on_click=self._preview))
        actions.addWidget(button("Rebuild in Resolve (new version)", icon_name="fa5s.hammer", on_click=self._rebuild))
        actions.addStretch(1)
        layout.addLayout(actions)
        actions2 = QHBoxLayout()
        actions2.addWidget(button("Learn from my final timeline", icon_name="fa5s.graduation-cap", on_click=self._corrections, tooltip="Reads the VANTA timeline after your edits in Resolve and learns from the differences"))
        actions2.addWidget(button("Final render", icon_name="fa5s.file-export", on_click=self._final_render))
        actions2.addStretch(1)
        layout.addLayout(actions2)
        tabs = QTabWidget()
        player_holder = QWidget()
        pl = QVBoxLayout(player_holder)
        self.player = VideoPlayer()
        pl.addWidget(self.player)
        tabs.addTab(player_holder, "Watch")
        self.qc_table = QTableWidget(0, 5)
        self.qc_table.setHorizontalHeaderLabels(["Level", "Issue", "Severity", "Repair", "Where"])
        self.qc_table.verticalHeader().setVisible(False)
        self.qc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.qc_table.setWordWrap(True)
        tabs.addTab(self.qc_table, "QC findings")
        self.qc_summary = QTextBrowser()
        tabs.addTab(self.qc_summary, "Critic")
        layout.addWidget(tabs, 1)
        self.tabs = tabs
        return w

    def _chat(self) -> QWidget:
        card = Card()
        card.body.addWidget(label("CHAT · scoped patches", "eyebrow"))
        card.body.addWidget(label("“Make the grade 15% less aggressive”, “cut faster during the drop”, “move the title up”, “use reference 2's colour but keep reference 3's typography”. You confirm every change; only the affected stage is regenerated.", "faint", wrap=True))
        self.history = QTextBrowser()
        self.history.setOpenExternalLinks(False)
        card.body.addWidget(self.history, 1)
        self.confirm_row = QWidget()
        cr = QHBoxLayout(self.confirm_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addWidget(button("Apply", kind="primary", icon_name="fa5s.check", on_click=self._apply))
        cr.addWidget(button("Cancel", on_click=self._cancel))
        cr.addStretch(1)
        self.confirm_row.setVisible(False)
        card.body.addWidget(self.confirm_row)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Tell VANTA what to change…")
        self.input.returnPressed.connect(self._send)
        row.addWidget(self.input, 1)
        row.addWidget(button("", kind="primary", icon_name="fa5s.paper-plane", on_click=self._send))
        card.body.addLayout(row)
        return card

    # ------------------------------------------------------------- data
    def on_show(self) -> None:
        run = self.bridge.current_run or self.run_id
        if run and run != "run_library":
            self._set_run(run)

    def prefill_chat(self, text: str) -> None:
        self.input.setText(text)
        self.input.setFocus()

    def _set_run(self, run_id: str) -> None:
        if not run_id or run_id == "run_library":
            return
        self.run_id = run_id
        try:
            state = self.service.load_run(run_id)
        except KeyError:
            return
        self._render_decisions(state)
        source = state.report.render_path if state.report and getattr(state.report, "render_path", None) else state.preview
        self.player.load(source)
        self._render_qc(state.qc or [r["json"] for r in self.service.library.list_qc(run_id)])

    def _render_decisions(self, state) -> None:
        clear_layout(self.dec_layout)
        edg = state.edg
        if not edg:
            self.dec_layout.addWidget(EmptyState("fa5s.check-double", "Nothing to review yet", "Plan an edit in Create first."))
            return
        self.dec_layout.addWidget(label(f"{edg.timeline_name} · revision {edg.revision}", "h2"))
        graph = state.plan.graph if state.plan else None
        for shot in edg.ordered_shots(1):
            card = Card(padding=10, spacing=6)
            row = QHBoxLayout()
            thumb = None
            if graph and shot.footage_shot_id in graph.shots:
                thumb = graph.shots[shot.footage_shot_id].get("thumb")
            else:
                rec = self.service.library.get_shot(shot.footage_shot_id or "")
                thumb = rec.get("thumb") if rec else None
            row.addWidget(Thumb(thumb, QSize(54, 96), radius=6))
            col = QVBoxLayout()
            head = QHBoxLayout()
            head.addWidget(label(f"{shot.id} · {shot.role or ''} · {edg.timeline_duration(shot) / edg.fps:.2f}s", "h3"))
            head.addStretch(1)
            tone = theme.OK if shot.confidence >= 0.9 else theme.WARN if shot.confidence >= 0.7 else theme.INFO
            head.addWidget(Chip(f"conf {shot.confidence:.2f}", tone))
            col.addLayout(head)
            col.addWidget(label("because " + " · ".join(shot.rationale[:2]), "faint", wrap=True))
            grade = edg.grade_for(shot.id)
            if grade:
                col.addWidget(label(f"grade: {grade.intent[:80] or 'solved look'} (conf {grade.confidence:.2f})", "faint", wrap=True))
            fb = QHBoxLayout()
            fb.addWidget(button("👍", kind="ghost", on_click=lambda _=False, s=shot.id: self._fb("thumbs_up", s, "cut")))
            fb.addWidget(button("Wrong shot", kind="ghost", on_click=lambda _=False, s=shot.id: self._replace(s)))
            fb.addWidget(button("Grade too much", kind="ghost", on_click=lambda _=False, s=shot.id: self._chat_direct(f"make the grade 15% less aggressive on {s}", s)))
            fb.addStretch(1)
            col.addLayout(fb)
            row.addLayout(col, 1)
            card.body.addLayout(row)
            self.dec_layout.addWidget(card)
        for layer in edg.texts:
            card = Card(padding=10, spacing=4)
            card.body.addWidget(label(f"Text “{layer.text}” · {layer.template} · {layer.start_frame / edg.fps:.1f}s", "h3"))
            card.body.addWidget(label(f"{layer.purpose} · {layer.provenance.note or ''}"[:220], "faint", wrap=True))
            row = QHBoxLayout()
            row.addWidget(button("Move up", kind="ghost", on_click=lambda _=False, t=layer.id: self._chat_direct("move the title up", t)))
            row.addWidget(button("Remove", kind="ghost", on_click=lambda _=False, t=layer.id: self._fb("wrong_text", t, "typography")))
            row.addStretch(1)
            card.body.addLayout(row)
            self.dec_layout.addWidget(card)
        for t in edg.transitions:
            self.dec_layout.addWidget(label(f"Transition {t.kind} → {t.to_shot}: {t.purpose} (conf {t.confidence:.2f})", "faint", wrap=True))
        for e in edg.effects:
            self.dec_layout.addWidget(label(f"Effect {e.template} on {e.shot_id}: {e.purpose}", "faint", wrap=True))
        self.dec_layout.addStretch(1)

    def _render_qc(self, history: list[dict]) -> None:
        self.qc_table.setRowCount(0)
        if not history:
            self.qc_summary.setHtml("<p style='color:#9aa1ad'>Run QC to see findings.</p>")
            return
        last = history[-1]
        findings = last.get("findings", [])
        self.qc_table.setRowCount(len(findings))
        for row, f in enumerate(findings):
            self.qc_table.setItem(row, 0, QTableWidgetItem(f"L{f['level']}"))
            item = QTableWidgetItem(f["description"])
            if f.get("blocking"):
                item.setForeground(Qt.red)
            self.qc_table.setItem(row, 1, item)
            self.qc_table.setItem(row, 2, QTableWidgetItem(f"{f['severity']:.2f}"))
            self.qc_table.setItem(row, 3, QTableWidgetItem(f.get("repair") or ""))
            rng = f.get("affected_range_s")
            self.qc_table.setItem(row, 4, QTableWidgetItem(f"{rng[0]:.1f}–{rng[1]:.1f}s" if rng else f.get("target_id") or ""))
        critic = (last.get("metrics") or {}).get("critic") or {}
        report = critic.get("report") or {}
        html = [f"<h3>Iteration {last.get('iteration')} — {last.get('status')}</h3>"]
        metrics = last.get("metrics") or {}
        if metrics.get("integrated_lufs") is not None:
            html.append(f"<p>Loudness {metrics['integrated_lufs']:.1f} LUFS · true peak {metrics.get('true_peak_dbtp', 0):.1f} dBTP · render: {metrics.get('render_kind')}</p>")
        if report:
            html.append(f"<p><b>Hook works:</b> {report.get('hook_works')} — {report.get('notes', '')}</p>")
        elif critic.get("error"):
            html.append(f"<p style='color:#ffb020'>Editorial critic unavailable: {critic['error']}</p>")
        self.qc_summary.setHtml("".join(html))

    def _on_event(self, ev: dict) -> None:
        if ev.get("run_id") != self.run_id:
            return
        if ev.get("action") in {"final_candidate", "needs_review"} or ev.get("stage") == "repair":
            state = self.service.runs.get(self.run_id)
            if state:
                self._render_qc(state.qc)
                self._render_decisions(state)
                if state.preview:
                    self.player.load(state.preview)
        if ev.get("action") == "preview_ready":
            self.player.load(ev.get("details", {}).get("path"))

    # ------------------------------------------------------------- actions
    def _qc(self) -> None:
        if self.run_id:
            self.service.qc_and_repair(self.run_id)
            self.tabs.setCurrentIndex(1)
            self.bridge.toast.emit("QC L0-L5, critic and up to 3 scoped repair iterations…", theme.LIVE)

    def _preview(self) -> None:
        if self.run_id:
            self.service.render_preview(self.run_id)

    def _rebuild(self) -> None:
        if self.run_id:
            self.service.execute(self.run_id)
            self.window.navigate("mission")

    def _corrections(self) -> None:
        if self.run_id:
            self.service.capture_corrections(self.run_id)
            self.bridge.toast.emit("Reading your finished timeline from Resolve…", theme.LIVE)

    def _final_render(self) -> None:
        if not self.run_id:
            return
        try:
            self.service.final_render(self.run_id)
            self.bridge.toast.emit("Final render queued in Resolve.", theme.LIVE)
        except Exception as exc:
            self.bridge.toast.emit(str(exc), theme.WARN)

    def _fb(self, kind: str, target: str, dimension: str) -> None:
        if self.run_id:
            self.service.feedback(self.run_id, kind, target=target, dimension=dimension)
            self.bridge.toast.emit("Recorded.", theme.OK)

    def _replace(self, shot_id: str) -> None:
        self._fb("wrong_shot", shot_id, "cut")
        self._chat_direct(f"replace shot {int(shot_id.split('_')[-1]) + 1}", shot_id)

    def _chat_direct(self, text: str, target: str) -> None:
        self.input.setText(text)
        self._send(target=target)

    # ---------------------------------------------------------------- chat
    def _send(self, target: str | None = None) -> None:
        text = self.input.text().strip()
        if not text or not self.run_id:
            return
        self.input.clear()
        self.history.append(f"<p><b style='color:{theme.ACCENT}'>You:</b> {text}</p>")
        try:
            result = self.service.chat(self.run_id, text)
        except Exception as exc:
            self.history.append(f"<p style='color:{theme.ERR}'>{exc}</p>")
            return
        if target:
            for patch in result.patches:
                if patch.target_id in {"all", "*"}:
                    patch.target_id = target
        self._resolve_next_best(result)
        self.pending = result
        ops = [f"{p.dimension}/{p.operation} → {p.target_id}: {p.changes}" for p in result.patches]
        if result.spec_changes:
            ops.append(f"brief: {result.spec_changes}")
        if result.replan:
            ops.append(f"re-cut range {result.replan}")
        html = f"<p><b>VANTA:</b> {result.understood}<br><span style='color:{theme.TEXT_DIM}'>" + "<br>".join(ops) + "</span>"
        if result.question:
            html += f"<br><i>{result.question}</i>"
        self.history.append(html + "</p>")
        self.confirm_row.setVisible(bool(result.patches or result.spec_changes or result.replan or result.undo))

    def _resolve_next_best(self, result) -> None:
        state = self.service.runs.get(self.run_id or "")
        if not state or not state.plan or not state.plan.graph:
            return
        from vanta.reasoning.critic import next_best_footage

        for patch in result.patches:
            if patch.changes.get("footage_shot_id") == "__next_best__":
                patch.changes["footage_shot_id"] = next_best_footage(state.plan.graph, state.edg, patch.target_id)

    def _apply(self) -> None:
        if not self.pending or not self.run_id:
            return
        result = self.pending
        self.pending = None
        self.confirm_row.setVisible(False)
        try:
            if result.replan or (result.spec_changes and not result.patches):
                out = self.service.apply_chat(self.run_id, result) if result.spec_changes else {}
                self.history.append(f"<p style='color:{theme.OK}'>Brief updated. Re-plan in Create (or use Re-plan) to regenerate only what changed.</p>")
                if result.replan:
                    self.service.replan_range(self.run_id, result.replan["range"], result.replan["pacing_delta"])
                    self.history.append(f"<p style='color:{theme.OK}'>Re-cutting frames {result.replan['range'][0]}-{result.replan['range'][1]} only.</p>")
                del out
            elif result.undo:
                self.service.undo_dimension(self.run_id, result.undo)
                self.history.append(f"<p style='color:{theme.OK}'>Undone ({result.undo}).</p>")
            else:
                out = self.service.apply_chat(self.run_id, result)
                changed = sum(len(v) for v in (out.get("changed") or {}).values())
                self.history.append(f"<p style='color:{theme.OK}'>Applied — {changed} element(s) changed in a new revision. Render a preview or rebuild in Resolve.</p>")
            state = self.service.runs[self.run_id]
            self._render_decisions(state)
        except Exception as exc:
            self.history.append(f"<p style='color:{theme.ERR}'>Could not apply: {exc}</p>")

    def _cancel(self) -> None:
        self.pending = None
        self.confirm_row.setVisible(False)
        self.history.append(f"<p style='color:{theme.TEXT_DIM}'>Cancelled.</p>")
