"""Style Brain: Style Explorer, rule review queue and Reference Inspector (manual 9.6)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vanta.app import theme
from vanta.app.widgets.charts import BarChart
from vanta.app.widgets.common import (
    Card,
    Chip,
    EmptyState,
    FlowWidget,
    Thumb,
    button,
    clear_layout,
    label,
    scroll,
    status_chip,
)
from vanta.app.widgets.video import VideoPlayer
from vanta.core.schemas import EditSpec


class StylePage(QWidget):
    def __init__(self, bridge, window):
        super().__init__()
        self.bridge = bridge
        self.window = window
        self.service = bridge.service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 14, 24, 14)
        top = QHBoxLayout()
        top.addWidget(label("Profile", "dim"))
        self.profile = QComboBox()
        self.profile.setMinimumWidth(260)
        self.profile.currentIndexChanged.connect(self._render)
        top.addWidget(self.profile)
        self.readiness = label("", "dim", wrap=True)
        top.addWidget(self.readiness, 1)
        top.addWidget(button("Rebuild profile", icon_name="fa5s.sync", on_click=self._rebuild))
        layout.addLayout(top)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.explorer = QWidget()
        self.explorer_grid = QGridLayout(self.explorer)
        self.explorer_grid.setSpacing(14)
        self.tabs.addTab(scroll(self.explorer), "Style Explorer")
        self.tabs.addTab(self._queue_tab(), "Rule review queue")
        self.tabs.addTab(self._inspector_tab(), "Reference Inspector")
        bridge.library_changed.connect(self.on_show)

    # ------------------------------------------------------------ explorer
    def on_show(self) -> None:
        current = self.profile.currentData()
        self.profile.blockSignals(True)
        self.profile.clear()
        for p in self.service.library.list_profiles():
            n = (p.get("data_json") or {}).get("reference_count", 0)
            self.profile.addItem(f"{p['name']}  ({n} reels)", p["id"])
        if current:
            idx = self.profile.findData(current)
            if idx >= 0:
                self.profile.setCurrentIndex(idx)
        self.profile.blockSignals(False)
        self._render()
        self._refresh_refs()

    def _profile_id(self) -> str | None:
        return self.profile.currentData()

    def _rebuild(self) -> None:
        if self._profile_id():
            self.service.rebuild_style(self._profile_id())
            self.bridge.toast.emit("Rebuilding the profile: statistics, principles and candidate rules.", theme.LIVE)

    def _render(self) -> None:
        clear_layout(self.explorer_grid)
        pid = self._profile_id()
        if not pid:
            self.explorer_grid.addWidget(EmptyState("fa5s.brain", "No style profile yet", "Add reference reels in the Library. VANTA groups coherent styles into profiles and learns per dimension — rhythm, colour, typography, composition, motion, masking, Fusion and audio."), 0, 0)
            self._refresh_queue()
            return
        profile = self.service.library.get_profile(pid)
        readiness = ((profile or {}).get("data_json") or {}).get("readiness") or {}
        self.readiness.setText(f"<b>{readiness.get('level', '—').title()}</b> — {readiness.get('note', '')}")
        spec = EditSpec(goal="explore", duration_s=30, aspect="9:16", fps=30, profile_id=pid)
        pkg = self.service.brain.retrieve(pid, spec)
        cards = []
        r = pkg.rhythm
        c = Card()
        c.body.addWidget(label("RHYTHM", "eyebrow"))
        c.body.addWidget(label(f"Median shot {r.get('median_s') or 0:.2f}s", "h2"))
        c.body.addWidget(label(f"p10 {r.get('p10_s') or 0:.2f}s · p90 {r.get('p90_s') or 0:.2f}s · {r.get('cuts_per_minute') or 0:.0f} cuts/min", "dim"))
        c.body.addWidget(label(f"Beat lock {((r.get('beat_sync') or 0) * 100):.0f}% of cuts within 40 ms · hook shot {r.get('first_shot_s') or 0:.2f}s", "dim", wrap=True))
        trans = r.get("transitions_per_min") or {}
        if trans:
            c.body.addWidget(BarChart(sorted(trans.items(), key=lambda kv: -kv[1])[:6], color=theme.LIVE, max_value=max(trans.values()) or 1))
        cards.append(c)
        col = pkg.color
        c = Card()
        c.body.addWidget(label("COLOUR", "eyebrow"))
        c.body.addWidget(label((col.get("relations") or {}).get("statement", "—").capitalize(), "h2", wrap=True))
        tl = col.get("target_look") or {}
        if tl:
            c.body.addWidget(label(f"contrast {tl.get('contrast', 0):.2f} · black point {tl.get('black_point', 0):.3f} · chroma {tl.get('chroma_mean', 0):.1f}", "dim"))
            c.body.addWidget(label(f"shadows a*/b* {tl.get('shadow_a', 0):+.1f}/{tl.get('shadow_b', 0):+.1f} · highlights b* {tl.get('high_b', 0):+.1f}", "dim"))
        if col.get("intent"):
            c.body.addWidget(label(f"“{col['intent']}”", "faint", wrap=True))
        cards.append(c)
        t = pkg.typography
        c = Card()
        c.body.addWidget(label("TYPOGRAPHY", "eyebrow"))
        c.body.addWidget(label(f"{t.get('font_class', '—')}, {t.get('case', '')} case", "h2"))
        c.body.addWidget(label(f"{t.get('texts_per_reel', 0):.1f} texts per reel · {t.get('animation', 'static')} animation · centred at y={t.get('y', 0):.2f}", "dim", wrap=True))
        fill = t.get("fill") or "#ffffff"
        c.body.addWidget(label(f"<span style='color:{fill}'>■</span> fill {fill} · outline {'yes' if t.get('outline') else 'no'} · captions in {t.get('captions', 0) * 100:.0f}% of reels", "dim"))
        cards.append(c)
        c = Card()
        c.body.addWidget(label("COMPOSITION", "eyebrow"))
        sizes = pkg.composition.get("shot_sizes") or {}
        c.body.addWidget(BarChart(sorted(sizes.items(), key=lambda kv: -kv[1])[:6]))
        c.body.addWidget(label(f"thirds alignment {pkg.composition.get('thirds', 0):.2f}", "faint"))
        cards.append(c)
        c = Card()
        c.body.addWidget(label("MOTION", "eyebrow"))
        moves = pkg.motion.get("moves") or {}
        c.body.addWidget(BarChart(sorted(moves.items(), key=lambda kv: -kv[1])[:6], color=theme.LIVE))
        c.body.addWidget(label(f"intensity {pkg.motion.get('intensity', 0):.2f} · {pkg.motion.get('speed_events_per_min', 0):.1f} speed effects/min", "faint"))
        cards.append(c)
        c = Card()
        c.body.addWidget(label("FUSION / EFFECTS", "eyebrow"))
        top_fx = [(e["effect"].replace("_", " "), e["share"]) for e in (pkg.fusion.get("top") or [])[:7]]
        c.body.addWidget(BarChart(top_fx, color=theme.PURPLE, max_value=1.0) if top_fx else label("No stylised effects measured.", "dim"))
        c.body.addWidget(label(f"text behind subject in {pkg.masking.get('text_behind_subject_share', 0) * 100:.0f}% of reels", "faint"))
        cards.append(c)
        c = Card()
        c.body.addWidget(label("AUDIO", "eyebrow"))
        c.body.addWidget(label(f"{pkg.audio['bpm']:.0f} BPM typical" if pkg.audio.get("bpm") else "No reference audio analysed", "h2"))
        c.body.addWidget(label(f"energy {pkg.audio.get('energy') or 0:.2f} · drops used in {pkg.audio.get('drop_usage', 0) * 100:.0f}% of reels", "dim"))
        c.body.addWidget(label("Reference music is an analysis signal only — never reused.", "faint", wrap=True))
        cards.append(c)
        c = Card()
        c.body.addWidget(label("RULES IN FORCE", "eyebrow"))
        approved = [rr for rr in pkg.rules if rr.get("approval_state") == "approved"]
        c.body.addWidget(label(f"{len(approved)} approved · {len(pkg.rules) - len(approved)} candidate", "h2"))
        for rule in approved[:5]:
            c.body.addWidget(label(f"• {rule.get('statement')}", "dim", wrap=True))
        if pkg.negatives:
            c.body.addWidget(label("Never: " + "; ".join(pkg.negatives[:6]), "faint", wrap=True))
        cards.append(c)
        for i, card in enumerate(cards):
            card.body.addStretch(1)
            self.explorer_grid.addWidget(card, i // 3, i % 3)
        for col_idx in range(3):
            self.explorer_grid.setColumnStretch(col_idx, 1)
        self._refresh_queue()

    # ------------------------------------------------------------ queue
    def _queue_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.addWidget(label("Candidate rules need your approval before they become authoritative. Conditional rules require ≥8 shots across ≥3 reels, a real effect size and a gain under leave-one-reel-out validation. Preference rules come from corrections repeated 4+ times.", "dim", wrap=True))
        self.queue = QTableWidget(0, 8)
        self.queue.setHorizontalHeaderLabels(["State", "Dimension", "Kind", "Rule", "Confidence", "Support", "Validation", "Actions"])
        self.queue.verticalHeader().setVisible(False)
        self.queue.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue.setWordWrap(True)
        self.queue.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.queue.setColumnWidth(7, 230)
        self.queue.itemSelectionChanged.connect(self._show_evidence)
        layout.addWidget(self.queue, 3)
        layout.addWidget(label("Evidence shots", "h3"))
        self.evidence = FlowWidget(spacing=8)
        layout.addWidget(scroll(self.evidence), 1)
        return page

    def _refresh_queue(self) -> None:
        rules = self.service.library.list_rules(profile_id=self._profile_id()) if self._profile_id() else self.service.library.list_rules()
        order = {"candidate": 0, "approved": 1, "stale": 2, "rejected": 3}
        rules.sort(key=lambda r: (order.get(r["approval_state"], 4), -r["confidence"]))
        self._rules = rules
        self.queue.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            payload = rule["json"]
            self.queue.setCellWidget(row, 0, status_chip(rule["approval_state"]))
            self.queue.setItem(row, 1, QTableWidgetItem(rule["dimension"]))
            self.queue.setItem(row, 2, QTableWidgetItem(rule["kind"]))
            self.queue.setItem(row, 3, QTableWidgetItem(payload.get("statement") or rule["id"]))
            self.queue.setItem(row, 4, QTableWidgetItem(f"{rule['confidence']:.2f}"))
            self.queue.setItem(row, 5, QTableWidgetItem(f"{rule['support']} shots / {payload.get('reels', 0)} reels" + (f", {rule['contradictions']} against" if rule["contradictions"] else "")))
            gain = payload.get("validation_gain")
            self.queue.setItem(row, 6, QTableWidgetItem(f"d={payload.get('effect_size')}, LORO +{gain * 100:.0f}%" if gain is not None else payload.get("origin", "")))
            actions = QWidget()
            box = QHBoxLayout(actions)
            box.setContentsMargins(2, 2, 2, 2)
            box.addWidget(button("Approve", kind="primary" if rule["approval_state"] != "approved" else None, on_click=lambda _=False, rid=rule["id"]: self._set_rule(rid, "approved")))
            box.addWidget(button("Reject", on_click=lambda _=False, rid=rule["id"]: self._set_rule(rid, "rejected")))
            self.queue.setCellWidget(row, 7, actions)
            self.queue.setRowHeight(row, 46)

    def _set_rule(self, rule_id: str, state: str) -> None:
        self.service.library.set_rule_state(rule_id, state)
        if self._profile_id():
            self.service.brain.export_kb(self._profile_id())
        self.bridge.toast.emit(f"Rule {state}. The KB file in kb/ was updated.", theme.OK if state == "approved" else theme.WARN)
        self._refresh_queue()

    def _show_evidence(self) -> None:
        self.evidence.clear()
        rows = {i.row() for i in self.queue.selectedIndexes()}
        if not rows:
            return
        rule = self._rules[min(rows)]
        for shot_id in rule["json"].get("evidence", [])[:10]:
            shot = self.service.library.get_shot(shot_id)
            if shot and shot.get("thumb"):
                self.evidence.addWidget(Thumb(shot["thumb"], QSize(72, 128)))
        for shot_id in rule["json"].get("counter_examples", [])[:4]:
            shot = self.service.library.get_shot(shot_id)
            if shot and shot.get("thumb"):
                t = Thumb(shot["thumb"], QSize(72, 128))
                t.setToolTip("counter-example")
                t.setStyleSheet(f"border: 2px solid {theme.ERR}; border-radius: 8px;")
                self.evidence.addWidget(t)

    # ------------------------------------------------------------ inspector
    def _inspector_tab(self) -> QWidget:
        split = QSplitter(Qt.Horizontal)
        self.ref_list = QListWidget()
        self.ref_list.setIconSize(QSize(54, 96))
        self.ref_list.currentItemChanged.connect(lambda cur, _prev: self._inspect_item(cur))
        split.addWidget(self.ref_list)
        right = QWidget()
        rl = QHBoxLayout(right)
        self.ref_player = VideoPlayer()
        rl.addWidget(self.ref_player, 2)
        self.techniques = QVBoxLayout()
        holder = QWidget()
        holder.setLayout(self.techniques)
        rl.addWidget(scroll(holder), 3)
        split.addWidget(right)
        split.setSizes([280, 1100])
        return split

    def _refresh_refs(self) -> None:
        from PySide6.QtGui import QIcon, QPixmap

        current = self.ref_list.currentItem().data(Qt.UserRole) if self.ref_list.currentItem() else None
        self.ref_list.clear()
        for media in self.service.library.list_media("reference"):
            item = QListWidgetItem(f"{(media.get('title') or '')[:42]}\n{media.get('analysis_status')}")
            if media.get("thumb") and Path(media["thumb"]).is_file():
                item.setIcon(QIcon(QPixmap(media["thumb"])))
            item.setData(Qt.UserRole, media["id"])
            self.ref_list.addItem(item)
            if media["id"] == current:
                self.ref_list.setCurrentItem(item)

    def inspect(self, media_id: str) -> None:
        self.tabs.setCurrentIndex(2)
        self._refresh_refs()
        for i in range(self.ref_list.count()):
            if self.ref_list.item(i).data(Qt.UserRole) == media_id:
                self.ref_list.setCurrentRow(i)
                return

    def _inspect_item(self, item: QListWidgetItem | None) -> None:
        clear_layout(self.techniques)
        if item is None:
            return
        media_id = item.data(Qt.UserRole)
        from vanta.workers.service import media_analysis

        analysis = media_analysis(media_id, self.service) or {}
        self.ref_player.load(analysis.get("proxy"))
        head = Card()
        head.body.addWidget(label(analysis.get("title") or media_id, "h2", wrap=True))
        vlm = analysis.get("vlm") or {}
        if vlm:
            head.body.addWidget(label(vlm.get("summary", ""), "dim", wrap=True))
            head.body.addWidget(label(f"Hook: {vlm.get('hook', '')}  ·  Payoff: {vlm.get('payoff', '')}", "faint", wrap=True))
        pacing = analysis.get("pacing") or {}
        head.body.addWidget(label(f"{pacing.get('shot_count', 0):.0f} shots · median {pacing.get('median_s', 0):.2f}s · {pacing.get('cuts_per_minute', 0):.0f} cuts/min" + (f" · {analysis['audio']['bpm']:.0f} BPM" if (analysis.get("audio") or {}).get("bpm") else ""), "dim"))
        self.techniques.addWidget(head)
        for capsule in self.service.library.list_capsules(media_id):
            data = capsule["json"]
            card = Card(padding=12, spacing=6)
            row = QHBoxLayout()
            row.addWidget(label(data["technique"].replace("_", " ").title(), "h3"))
            row.addWidget(Chip(data.get("dimension", ""), theme.INFO))
            for src in data.get("sources", []):
                row.addWidget(Chip(src.split(":")[0], theme.TEXT_DIM))
            row.addStretch(1)
            row.addWidget(label(f"{data['window_s'][0]:.1f}–{data['window_s'][1]:.1f}s", "mono"))
            row.addWidget(Chip(f"conf {capsule['confidence']:.2f}", theme.OK if capsule["confidence"] >= 0.7 else theme.WARN))
            card.body.addLayout(row)
            card.body.addWidget(label(f"<b>Observed:</b> {data['observed']}", wrap=True))
            inferred = data.get("inferred") or {}
            method = inferred.get("method") if inferred.get("method_known", True) else f"unknown (best guess: {inferred.get('method')})"
            text = f"<b>Inferred:</b> {method}"
            if inferred.get("alternative"):
                text += f" — alternative: {inferred['alternative']}"
            if inferred.get("intent"):
                text += f"<br><i>{inferred['intent']}</i>"
            card.body.addWidget(label(text, "dim", wrap=True))
            card.body.addWidget(label(f"<b>Purpose:</b> {data.get('purpose', '')}" + (f" · <b>when:</b> {data.get('conditions')}" if data.get("conditions") else ""), "faint", wrap=True))
            impl = data.get("implementation") or {}
            if impl.get("template"):
                card.body.addWidget(label(f"Implementation: {impl['template']} (fallback {impl.get('fallback')})", "faint"))
            evidence_row = QHBoxLayout()
            for ev in data.get("evidence", [])[:3]:
                if Path(ev.get("artifact", "")).is_file():
                    evidence_row.addWidget(Thumb(ev["artifact"], QSize(54, 96)))
            evidence_row.addStretch(1)
            if capsule["approved"]:
                evidence_row.addWidget(Chip("confirmed", theme.OK, filled=True))
            else:
                evidence_row.addWidget(button("Confirm", kind="ghost", icon_name="fa5s.check", on_click=lambda _=False, cid=capsule["id"]: self._capsule(cid, True)))
            evidence_row.addWidget(button("This is wrong", kind="ghost", icon_name="fa5s.times", on_click=lambda _=False, cid=capsule["id"]: self._capsule(cid, False)))
            card.body.addLayout(evidence_row)
            self.techniques.addWidget(card)
        self.techniques.addStretch(1)

    def _capsule(self, capsule_id: str, approve: bool) -> None:
        self.service.library.set_capsule_state(capsule_id, approved=approve, rejected=not approve)
        self.bridge.toast.emit("Capsule confirmed." if approve else "Capsule removed from what VANTA believes.", theme.OK if approve else theme.WARN)
        self._inspect_item(self.ref_list.currentItem())
