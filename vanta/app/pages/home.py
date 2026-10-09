"""Home: what VANTA needs next, library stats and recent projects."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from vanta.app import theme
from vanta.app.widgets.common import (
    Card,
    EmptyState,
    StatTile,
    button,
    clear_layout,
    icon,
    label,
    scroll,
    status_chip,
)


class HomePage(QWidget):
    _checks_ready = Signal(list)

    def __init__(self, bridge, window):
        super().__init__()
        self.bridge = bridge
        self.window = window
        self.service = bridge.service
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(18)
        hero = Card(padding=26)
        hero.setObjectName("hero")
        hero.body.addWidget(label("SUPERVISED AI CO-EDITOR", "eyebrow"))
        title = label("Learn your taste from reels. Build the edit in Resolve, live.", "h1", wrap=True)
        hero.body.addWidget(title)
        hero.body.addWidget(label("Paste Instagram links you love, add your footage and a licensed track. VANTA studies the references, explains what it will borrow, lets you steer, then builds an editable, graded, titled timeline in DaVinci Resolve while you watch — and learns from every correction you make.", "dim", wrap=True))
        actions = QHBoxLayout()
        actions.addWidget(button("New edit", kind="primary", icon_name="fa5s.magic", on_click=lambda: window.navigate("create")))
        actions.addWidget(button("Add reference reels", icon_name="fa5s.link", on_click=lambda: window.navigate("library")))
        actions.addWidget(button("Open Mission Control", kind="ghost", icon_name="fa5s.satellite-dish", on_click=lambda: window.navigate("mission")))
        actions.addStretch(1)
        hero.body.addLayout(actions)
        layout.addWidget(hero)
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_refs = StatTile("References", "0", "analysed reels", "fa5s.film")
        self.t_footage = StatTile("Footage", "0", "clips in the knowledge graph", "fa5s.video")
        self.t_music = StatTile("Music", "0", "licensed tracks", "fa5s.music")
        self.t_style = StatTile("Style", "—", "profile readiness", "fa5s.brain")
        self.t_rules = StatTile("Rules", "0", "approved / waiting for review", "fa5s.balance-scale")
        for t in (self.t_refs, self.t_footage, self.t_music, self.t_style, self.t_rules):
            tiles.addWidget(t)
        layout.addLayout(tiles)
        lower = QHBoxLayout()
        lower.setSpacing(18)
        self.checks_card = Card()
        self.checks_card.body.addWidget(label("Setup", "h2"))
        self.checks_card.body.addWidget(label("Everything VANTA verifies before it touches your project.", "dim"))
        self.checks_grid = QGridLayout()
        self.checks_grid.setVerticalSpacing(10)
        self.checks_card.body.addLayout(self.checks_grid)
        self.checks_card.body.addStretch(1)
        lower.addWidget(self.checks_card, 2)
        self.projects_card = Card()
        self.projects_card.body.addWidget(label("Recent edits", "h2"))
        self.projects_box = QVBoxLayout()
        self.projects_box.setSpacing(8)
        self.projects_card.body.addLayout(self.projects_box)
        self.projects_card.body.addStretch(1)
        lower.addWidget(self.projects_card, 3)
        layout.addLayout(lower)
        layout.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll(content))
        self._checks_ready.connect(self._render_checks)
        bridge.library_changed.connect(self.on_show)
        bridge.resolve.connect(lambda _s: self._refresh_checks())

    def on_show(self) -> None:
        lib = self.service.library
        refs = [m for m in lib.list_media("reference") if m["analysis_status"] == "done"]
        footage = lib.list_media("footage")
        music = lib.list_media("music")
        licensed = [m for m in music if (m.get("rights_json") or {}).get("platform_cleared") or (m.get("rights_json") or {}).get("licence_confirmed")]
        self.t_refs.set(str(len(refs)), f"{len(lib.list_media('reference'))} in library")
        self.t_footage.set(str(len(footage)), f"{sum(1 for f in footage if f['analysis_status'] == 'done')} analysed")
        self.t_music.set(str(len(licensed)), f"{len(music)} tracks, {len(licensed)} licensed")
        profiles = lib.list_profiles()
        best = max(profiles, key=lambda p: (p.get("data_json") or {}).get("reference_count", 0), default=None)
        readiness = ((best or {}).get("data_json") or {}).get("readiness") or {}
        self.t_style.set(readiness.get("level", "—").title() if readiness else "—", f"{best['name']}: {readiness.get('note', '')}" if best else "add reference reels to start")
        rules = lib.list_rules()
        approved = sum(1 for r in rules if r["approval_state"] == "approved")
        candidates = sum(1 for r in rules if r["approval_state"] == "candidate")
        self.t_rules.set(str(approved), f"{candidates} candidate rule(s) waiting for your review")
        self._render_projects()
        self._refresh_checks()

    def _render_projects(self) -> None:
        clear_layout(self.projects_box)
        projects = self.service.library.list_projects()[:8]
        if not projects:
            self.projects_box.addWidget(EmptyState("fa5s.film", "No edits yet", "Create your first edit: pick footage, set the brief, and VANTA plans it.", button("New edit", kind="primary", on_click=lambda: self.window.navigate("create"))))
            return
        for project in projects:
            card = Card(padding=12, clickable=True)
            row = QHBoxLayout()
            col = QVBoxLayout()
            col.addWidget(label(project["name"], "h3"))
            spec = project.get("spec_json") or {}
            col.addWidget(label(f"{spec.get('duration_s', 0):.0f}s · {spec.get('platform', '')} · {len(project.get('footage_json') or [])} clips · updated {project['updated_at'][:16].replace('T', ' ')}", "faint"))
            row.addLayout(col, 1)
            row.addWidget(status_chip(project.get("status", "draft")))
            card.body.addLayout(row)
            card.clicked.connect(lambda pid=project["id"]: self._open_project(pid))
            self.projects_box.addWidget(card)

    def _open_project(self, project_id: str) -> None:
        self.bridge.set_project(project_id)
        self.window.navigate("create")

    def _refresh_checks(self) -> None:
        def work() -> None:
            from vanta.models.registry import refresh
            from vanta.resolve.ledger import load_ledger

            lib = self.service.library
            resolve = self.bridge.last_resolve
            ledger = load_ledger()
            specs = {s.key: s for s in refresh(self.service.settings)}
            refs = [m for m in lib.list_media("reference") if m["analysis_status"] == "done"]
            footage = [m for m in lib.list_media("footage") if m["analysis_status"] == "done"]
            music = [m for m in lib.list_media("music") if (m.get("rights_json") or {}).get("platform_cleared") or (m.get("rights_json") or {}).get("licence_confirmed")]
            checks = [
                ("DaVinci Resolve Studio connected (Local scripting on)", bool(resolve.get("connected")), resolve.get("reason") or f"{resolve.get('product') or ''} {resolve.get('version_string') or ''}", "system"),
                ("Resolve compatibility check (gates G1-G16)", ledger.passed("G1", current_version=resolve.get("version_string")), f"G1 {ledger.status('G1')}, G3 {ledger.status('G3')}, G4 {ledger.status('G4')}", "system"),
                ("Creative director model (Qwen3-VL)", bool(specs.get("director") and specs["director"].installed), specs["director"].detail if specs.get("director") else "", "system"),
                ("Specialist models (Whisper, DINOv2, SigLIP, CLAP, SAM 2.1)", all(specs[k].installed for k in ("whisper", "dinov2", "siglip", "clap", "sam2") if k in specs), ", ".join(k for k in ("whisper", "dinov2", "siglip", "clap", "sam2") if k in specs and not specs[k].installed) or "all installed", "system"),
                ("At least 3 analysed reference reels", len(refs) >= 3, f"{len(refs)} analysed (15-25 per profile give stable principles)", "library"),
                ("Footage analysed", bool(footage), f"{len(footage)} clip(s)", "library"),
                ("A licensed music track", bool(music), f"{len(music)} cleared track(s)", "library"),
            ]
            self._checks_ready.emit(checks)

        threading.Thread(target=work, daemon=True).start()

    def _render_checks(self, checks: list) -> None:
        clear_layout(self.checks_grid)
        for row, (title, ok, detail, page) in enumerate(checks):
            glyph = QLabel()
            glyph.setPixmap(icon("fa5s.check-circle" if ok else "fa5s.circle", theme.OK if ok else theme.TEXT_FAINT).pixmap(18, 18))
            self.checks_grid.addWidget(glyph, row, 0, Qt.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(1)
            text.addWidget(label(title, "h3"))
            text.addWidget(label(detail or "", "faint", wrap=True))
            holder = QWidget()
            holder.setLayout(text)
            self.checks_grid.addWidget(holder, row, 1)
            if not ok:
                self.checks_grid.addWidget(button("Fix", kind="ghost", icon_name="fa5s.arrow-right", on_click=lambda _=False, p=page: self.window.navigate(p)), row, 2, Qt.AlignTop)
        self.checks_grid.setColumnStretch(1, 1)
