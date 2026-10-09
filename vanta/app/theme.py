"""VANTA desktop theme: a dark creative-workstation look (Qt stylesheet + palette)."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0d0e11"
BG_RAISED = "#14161b"
PANEL = "#181b21"
PANEL_HI = "#1f232b"
BORDER = "#272c36"
BORDER_HI = "#353b47"
TEXT = "#e9ebef"
TEXT_DIM = "#9aa1ad"
TEXT_FAINT = "#6b7280"
ACCENT = "#ffb020"  # VANTA amber
ACCENT_DIM = "#3a2c10"
LIVE = "#38d6f5"  # live / running
OK = "#3ddc97"
WARN = "#ffb020"
ERR = "#ff5d5d"
INFO = "#5b8cff"
PURPLE = "#b07cff"

STATUS_COLORS = {
    "planned": INFO,
    "running": LIVE,
    "verified": OK,
    "failed": ERR,
    "rolled_back": PURPLE,
    "skipped": TEXT_FAINT,
    "info": TEXT_DIM,
    "warning": WARN,
    "QUEUED": TEXT_DIM,
    "RUNNING": LIVE,
    "SUCCEEDED": OK,
    "FAILED": ERR,
    "RETRYABLE": WARN,
    "CANCELLED": TEXT_FAINT,
    "PASS": OK,
    "PARTIAL": WARN,
    "FAIL": ERR,
    "NOT_RUN": TEXT_FAINT,
    "approved": OK,
    "candidate": WARN,
    "rejected": ERR,
    "stale": TEXT_FAINT,
    "done": OK,
    "pending": TEXT_DIM,
}

MARKER_LEGEND = [
    ("Blue", "#3b82f6", "decision"),
    ("Green", "#22c55e", "verified"),
    ("Yellow", "#eab308", "low confidence — look at this"),
    ("Red", "#ef4444", "failed or blocked"),
    ("Purple", "#a855f7", "your override honoured"),
    ("Cyan", "#06b6d4", "AI note for you"),
]


def _family() -> str:
    families = set(QFontDatabase.families())
    for name in ("Inter", "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"):
        if name in families:
            return name
    return QApplication.font().family()


QSS = """
* {{ font-family: "{family}"; }}
QWidget {{ color: {TEXT}; font-size: 13px; }}
QMainWindow, QDialog, QMessageBox, QSplashScreen {{ background: {BG}; }}
QWidget#pageRoot {{ background: {BG}; }}
QStackedWidget {{ background: {BG}; }}
QAbstractScrollArea > QWidget#qt_scrollarea_viewport {{ background: transparent; }}
QFrame#hero {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1d1708, stop:0.55 {PANEL}, stop:1 #0f1a20); border: 1px solid {BORDER}; border-radius: 14px; }}
QToolTip {{ background: {PANEL_HI}; color: {TEXT}; border: 1px solid {BORDER_HI}; padding: 6px 8px; border-radius: 6px; }}
QLabel {{ background: transparent; }}
QLabel[role="h1"] {{ font-size: 26px; font-weight: 650; letter-spacing: -0.3px; }}
QLabel[role="h2"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="h3"] {{ font-size: 14px; font-weight: 600; color: {TEXT}; }}
QLabel[role="dim"] {{ color: {TEXT_DIM}; }}
QLabel[role="faint"] {{ color: {TEXT_FAINT}; font-size: 12px; }}
QLabel[role="eyebrow"] {{ color: {ACCENT}; font-size: 11px; font-weight: 700; letter-spacing: 1.6px; }}
QLabel[role="mono"] {{ font-family: "Cascadia Mono", "Consolas"; font-size: 12px; color: {TEXT_DIM}; }}
QFrame[role="card"] {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame[role="card"]:hover {{ border-color: {BORDER_HI}; }}
QFrame[role="cardflat"] {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame[role="sidebar"] {{ background: {BG_RAISED}; border-right: 1px solid {BORDER}; }}
QFrame[role="topbar"] {{ background: {BG_RAISED}; border-bottom: 1px solid {BORDER}; }}
QFrame[role="banner"] {{ background: {ACCENT_DIM}; border: 1px solid {ACCENT}; border-radius: 10px; }}
QFrame[role="divider"] {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QPushButton {{ background: {PANEL_HI}; border: 1px solid {BORDER_HI}; border-radius: 8px; padding: 7px 14px; color: {TEXT}; font-weight: 550; }}
QPushButton:hover {{ background: #262b35; border-color: #444b59; }}
QPushButton:pressed {{ background: #1a1e25; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; background: {PANEL}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; color: #1a1205; border: 1px solid {ACCENT}; font-weight: 700; }}
QPushButton[kind="primary"]:hover {{ background: #ffc14d; }}
QPushButton[kind="primary"]:disabled {{ background: #5a4a26; color: #2a2210; border-color: #5a4a26; }}
QPushButton[kind="danger"] {{ background: #3a1518; border-color: {ERR}; color: #ffd6d6; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; color: {TEXT_DIM}; }}
QPushButton[kind="ghost"]:hover {{ background: {PANEL_HI}; color: {TEXT}; }}
QPushButton[kind="nav"] {{ background: transparent; border: none; border-radius: 10px; text-align: left; padding: 10px 14px; color: {TEXT_DIM}; font-weight: 550; font-size: 13px; }}
QPushButton[kind="nav"]:hover {{ background: {PANEL}; color: {TEXT}; }}
QPushButton[kind="nav"]:checked {{ background: {PANEL_HI}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {BG_RAISED}; border: 1px solid {BORDER_HI}; border-radius: 8px; padding: 7px 10px; selection-background-color: {ACCENT}; selection-color: #1a1205; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {PANEL_HI}; border: 1px solid {BORDER_HI}; selection-background-color: {ACCENT_DIM}; outline: none; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER_HI}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; border: 2px solid {ACCENT}; }}
QProgressBar {{ background: {BG_RAISED}; border: 1px solid {BORDER}; border-radius: 5px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 #ffd27a); border-radius: 4px; }}
QProgressBar[live="true"]::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {LIVE}, stop:1 #8be9ff); }}
QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {TEXT_DIM}; padding: 9px 16px; border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_HI}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4a5160; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_HI}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QTableWidget, QTreeWidget, QListWidget {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: {BORDER}; alternate-background-color: #1a1d23; outline: none; }}
QTableWidget::item, QTreeWidget::item, QListWidget::item {{ padding: 6px; border: none; }}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {{ background: {ACCENT_DIM}; color: {TEXT}; }}
QHeaderView::section {{ background: {BG_RAISED}; color: {TEXT_DIM}; border: none; border-bottom: 1px solid {BORDER}; padding: 8px; font-weight: 600; font-size: 12px; }}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator:unchecked {{ border: 1px solid {BORDER_HI}; border-radius: 4px; background: {BG_RAISED}; }}
QCheckBox::indicator:checked {{ border: 1px solid {ACCENT}; border-radius: 4px; background: {ACCENT}; }}
QSplitter::handle {{ background: {BORDER}; }}
QStatusBar {{ background: {BG_RAISED}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QMenu {{ background: {PANEL_HI}; border: 1px solid {BORDER_HI}; padding: 6px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}
"""


def apply_theme(app: QApplication) -> None:
    family = _family()
    app.setFont(QFont(family, 10))
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(BG_RAISED))
    palette.setColor(QPalette.AlternateBase, QColor(PANEL))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(PANEL_HI))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#1a1205"))
    palette.setColor(QPalette.ToolTipBase, QColor(PANEL_HI))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.PlaceholderText, QColor(TEXT_FAINT))
    app.setPalette(palette)
    values = {k: v for k, v in globals().items() if k.isupper() and isinstance(v, str)}
    app.setStyleSheet(QSS.format(family=family, **values))
