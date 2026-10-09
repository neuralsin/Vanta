"""VANTA main window: sidebar navigation, live top bar, page stack, toasts."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from vanta.app import theme
from vanta.app.bridge import Bridge
from vanta.app.widgets.common import Pill, Toast, icon, label

NAV = [
    ("home", "Home", "fa5s.home"),
    ("library", "Library", "fa5s.photo-video"),
    ("style", "Style Brain", "fa5s.brain"),
    ("create", "Create", "fa5s.magic"),
    ("mission", "Mission Control", "fa5s.satellite-dish"),
    ("review", "Review", "fa5s.check-double"),
    ("transcript", "Transcript", "fa5s.align-left"),
    ("learning", "Learning", "fa5s.chart-line"),
    ("system", "System", "fa5s.sliders-h"),
]


class MainWindow(QMainWindow):
    def __init__(self, bridge: Bridge):
        super().__init__()
        self.bridge = bridge
        self.setWindowTitle("VANTA — AI co-editor for DaVinci Resolve")
        app_icon = Path(__file__).resolve().parents[2] / "assets" / "vanta.ico"
        self.setWindowIcon(QIcon(str(app_icon)) if app_icon.is_file() else icon("fa5s.film", theme.ACCENT))
        self.resize(1560, 960)
        self.setMinimumSize(1180, 720)
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar())
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._topbar())
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        holder = QWidget()
        holder.setLayout(right)
        outer.addWidget(holder, 1)
        self.setCentralWidget(root)
        self.pages: dict[str, QWidget] = {}
        self._build_pages()
        self.navigate("home")
        bridge.toast.connect(self.toast)
        bridge.event.connect(self._on_event)
        bridge.telemetry.connect(self._on_telemetry)
        bridge.resolve.connect(self._on_resolve)

    # ------------------------------------------------------------ chrome
    def _sidebar(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "sidebar")
        frame.setFixedWidth(232)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icon("fa5s.film", theme.ACCENT).pixmap(22, 22))
        brand.addWidget(logo)
        name = label("VANTA", "h2")
        name.setStyleSheet("font-size: 20px; font-weight: 800; letter-spacing: 3px;")
        brand.addWidget(name)
        brand.addStretch(1)
        layout.addLayout(brand)
        tagline = label("AI co-editor for Resolve", "faint")
        layout.addWidget(tagline)
        layout.addSpacing(18)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, title, icon_name in NAV:
            btn = QPushButton(f"  {title}")
            btn.setProperty("kind", "nav")
            btn.setCheckable(True)
            btn.setIcon(icon(icon_name, theme.TEXT_DIM))
            btn.setIconSize(QSize(16, 16))
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            layout.addWidget(btn)
        layout.addStretch(1)
        self.side_jobs = label("", "faint", wrap=True)
        layout.addWidget(self.side_jobs)
        self.side_progress = QProgressBar()
        self.side_progress.setRange(0, 100)
        self.side_progress.setVisible(False)
        layout.addWidget(self.side_progress)
        legend = label("Breadcrumb markers in Resolve:", "faint")
        layout.addWidget(legend)
        for _, hex_color, meaning in theme.MARKER_LEGEND:
            layout.addWidget(label(f"<span style='color:{hex_color}'>■</span>&nbsp; {meaning}", "faint"))
        return frame

    def _topbar(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "topbar")
        frame.setFixedHeight(56)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(22, 8, 18, 8)
        self.page_title = label("", "h2")
        layout.addWidget(self.page_title)
        layout.addStretch(1)
        self.model_pill = Pill("model idle", theme.TEXT_FAINT)
        self.gpu_pill = Pill("GPU —", theme.TEXT_FAINT)
        self.resolve_pill = Pill("Resolve: checking", theme.TEXT_FAINT)
        self.resolve_pill.setCursor(Qt.PointingHandCursor)
        self.resolve_pill.mousePressEvent = lambda _e: self.bridge.refresh_resolve()  # type: ignore[assignment]
        for pill in (self.model_pill, self.gpu_pill, self.resolve_pill):
            layout.addWidget(pill)
        return frame

    def _build_pages(self) -> None:
        from vanta.app.pages.create import CreatePage
        from vanta.app.pages.home import HomePage
        from vanta.app.pages.learning import LearningPage
        from vanta.app.pages.library import LibraryPage
        from vanta.app.pages.mission import MissionPage
        from vanta.app.pages.review import ReviewPage
        from vanta.app.pages.style import StylePage
        from vanta.app.pages.system import SystemPage
        from vanta.app.pages.transcript import TranscriptPage

        for key, cls in (
            ("home", HomePage),
            ("library", LibraryPage),
            ("style", StylePage),
            ("create", CreatePage),
            ("mission", MissionPage),
            ("review", ReviewPage),
            ("transcript", TranscriptPage),
            ("learning", LearningPage),
            ("system", SystemPage),
        ):
            page = cls(self.bridge, self)
            self.pages[key] = page
            self.stack.addWidget(page)

    def navigate(self, key: str) -> None:
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.nav_buttons[key].setChecked(True)
        self.page_title.setText(next(title for k, title, _ in NAV if k == key))
        for k, btn in self.nav_buttons.items():
            btn.setIcon(icon(next(i for kk, _, i in NAV if kk == k), theme.ACCENT if k == key else theme.TEXT_DIM))
        if hasattr(page, "on_show"):
            page.on_show()

    # ------------------------------------------------------------ live
    def toast(self, text: str, color: str = theme.ACCENT) -> None:
        Toast(self, text, color or theme.ACCENT)

    def _on_event(self, payload: dict) -> None:
        action = payload.get("action")
        status = payload.get("status")
        if action == "approval_required":
            self._ask_approval(payload)
        elif action in {"plan_ready", "preview_ready", "execution_done", "final_candidate"}:
            self.toast(payload.get("message") or action, theme.OK)
        elif action in {"execution_failed"} or (status == "failed" and payload.get("stage") in {"plan", "execute", "rollback"}):
            self.toast(payload.get("message") or "Failed", theme.ERR)

    def _ask_approval(self, payload: dict) -> None:
        run_id = payload.get("run_id")
        box = QMessageBox(self)
        box.setWindowTitle("VANTA needs your approval")
        conf = payload.get("confidence")
        because = ", ".join(payload.get("because") or [])
        box.setText(f"<b>{payload.get('message')}</b><br><br>Confidence {conf:.2f}<br>{because}" if conf is not None else payload.get("message"))
        approve = box.addButton("Approve", QMessageBox.AcceptRole)
        box.addButton("Skip", QMessageBox.RejectRole)
        box.exec()
        self.bridge.service.answer_approval(run_id, box.clickedButton() is approve)

    def _on_telemetry(self, data: dict) -> None:
        if data.get("vram_total_gb"):
            pct = data.get("gpu_util") or 0
            self.gpu_pill.set(f"GPU {pct:.0f}%  ·  VRAM {data['vram_used_gb']:.1f}/{data['vram_total_gb']:.1f} GB", theme.LIVE if pct > 30 else theme.OK)
        else:
            self.gpu_pill.set("GPU n/a", theme.TEXT_FAINT)
        resident = data.get("resident_model") or data.get("waiting_for")
        self.model_pill.set(f"model: {resident.split('/')[-1]}" if resident else "model idle", theme.LIVE if resident else theme.TEXT_FAINT)
        jobs = data.get("jobs") or []
        if jobs:
            first = jobs[0]
            self.side_jobs.setText(f"<b>{first['title']}</b><br>{first.get('message') or first['status'].lower()}" + (f"<br>+{len(jobs) - 1} more queued" if len(jobs) > 1 else ""))
            self.side_progress.setVisible(True)
            self.side_progress.setValue(int((first.get("progress") or 0) * 100))
        else:
            self.side_jobs.setText("")
            self.side_progress.setVisible(False)

    def _on_resolve(self, status: dict) -> None:
        if status.get("connected"):
            text = f"Resolve {status.get('version_string') or ''} · {status.get('project_name') or 'no project'}"
            self.resolve_pill.set(text, theme.OK)
            self.resolve_pill.setToolTip(f"Page: {status.get('page')}\nTimeline: {status.get('timeline_name')}")
        else:
            self.resolve_pill.set("Resolve offline", theme.ERR)
            self.resolve_pill.setToolTip(status.get("reason", ""))

    def closeEvent(self, event) -> None:
        active = self.bridge.service.jobs.active()
        if active:
            answer = QMessageBox.question(self, "Quit VANTA?", f"{len(active)} job(s) are still running. Quit and cancel them?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.bridge.service.shutdown()
        super().closeEvent(event)
