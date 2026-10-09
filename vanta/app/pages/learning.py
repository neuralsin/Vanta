"""Learning: how much you still have to fix, calibration, corrections and preference memory."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vanta.app import theme
from vanta.app.widgets.charts import BarChart, CalibrationChart
from vanta.app.widgets.common import Card, StatTile, button, label, scroll
from vanta.learning.preferences import agreement, calibration, mine_preferences


class LearningPage(QWidget):
    def __init__(self, bridge, window):
        super().__init__()
        self.bridge = bridge
        self.window = window
        self.service = bridge.service
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 16, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(label("The headline question is how much you have to fix — not how pretty the render looks. Expect many corrections at V1, fewer by V5, mostly polish by V20 (an expectation to test, not a promise).", "dim", wrap=True))
        tiles = QHBoxLayout()
        self.t_dec = StatTile("Decisions", "0", "recorded with predicted confidence", "fa5s.list")
        self.t_res = StatTile("Resolved", "0", "accepted or corrected by you", "fa5s.check")
        self.t_corr = StatTile("Corrections", "0", "explicit + implicit", "fa5s.pen")
        self.t_pref = StatTile("Preferences", "0", "candidate rules from corrections", "fa5s.heart")
        for t in (self.t_dec, self.t_res, self.t_corr, self.t_pref):
            tiles.addWidget(t)
        layout.addLayout(tiles)
        grid = QGridLayout()
        grid.setSpacing(14)
        cal = Card()
        cal.body.addWidget(label("Confidence calibration", "h3"))
        cal.body.addWidget(label("Does “0.9” really mean ~90% accepted? After 50–100 decisions VANTA suggests new gate thresholds.", "faint", wrap=True))
        self.cal_chart = CalibrationChart()
        cal.body.addWidget(self.cal_chart)
        self.cal_note = label("", "dim", wrap=True)
        cal.body.addWidget(self.cal_note)
        grid.addWidget(cal, 0, 0)
        agr = Card()
        agr.body.addWidget(label("Agreement by dimension", "h3"))
        self.agree_chart = BarChart([], color=theme.OK, max_value=1.0)
        agr.body.addWidget(self.agree_chart)
        agr.body.addWidget(label("Share of VANTA's choices you kept (shot, music, colour, text, effects).", "faint", wrap=True))
        grid.addWidget(agr, 0, 1)
        layout.addLayout(grid)
        corr = Card()
        row = QHBoxLayout()
        row.addWidget(label("Corrections log", "h3"))
        row.addStretch(1)
        row.addWidget(button("Mine preferences now", icon_name="fa5s.search", on_click=self._mine))
        row.addWidget(button("Open review queue", kind="primary", on_click=lambda: (window.navigate("style"), window.pages["style"].tabs.setCurrentIndex(1))))
        corr.body.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["When", "Dimension", "Kind", "What changed", "Confidence"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setMinimumHeight(280)
        corr.body.addWidget(self.table)
        layout.addWidget(corr)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(content))

    def on_show(self) -> None:
        lib = self.service.library
        decisions = lib.list_decisions()
        resolved = [d for d in decisions if d["accepted"] is not None]
        corrections = lib.list_corrections()
        prefs = [r for r in lib.list_rules() if r["kind"] in {"preference", "negative"}]
        self.t_dec.set(str(len(decisions)))
        self.t_res.set(str(len(resolved)), f"{(sum(d['accepted'] for d in resolved) / len(resolved) * 100):.0f}% accepted" if resolved else "accepted or corrected by you")
        self.t_corr.set(str(len(corrections)))
        self.t_pref.set(str(len(prefs)), f"{sum(1 for r in prefs if r['approval_state'] == 'approved')} approved")
        cal = calibration(lib)
        self.cal_chart.set_bins(cal.get("bins", []))
        suggested = cal.get("suggested")
        self.cal_note.setText(f"Suggested thresholds: execute ≥ {suggested['execute']}, note ≥ {suggested['note']}, preview ≥ {suggested['preview']}" if suggested else f"{cal.get('decisions', 0)} resolved decisions — calibration suggestions appear after 50.")
        self.agree_chart.set_items(sorted(agreement(lib).items()))
        self.table.setRowCount(len(corrections))
        for row, c in enumerate(reversed(corrections)):
            payload = c["json"]
            self.table.setItem(row, 0, QTableWidgetItem(c["created_at"][:16].replace("T", " ")))
            self.table.setItem(row, 1, QTableWidgetItem(c["dimension"]))
            self.table.setItem(row, 2, QTableWidgetItem(c["kind"].replace("_", " ")))
            self.table.setItem(row, 3, QTableWidgetItem(payload.get("description", "")))
            self.table.setItem(row, 4, QTableWidgetItem(f"{c['confidence']:.2f}"))

    def _mine(self) -> None:
        proposed = mine_preferences(self.service.library)
        self.bridge.toast.emit(f"{len(proposed)} preference rule(s) proposed for review." if proposed else "No correction has recurred 4+ times yet.", theme.OK if proposed else theme.TEXT_DIM)
        self.on_show()
