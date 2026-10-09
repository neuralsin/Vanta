"""Reusable widgets for the VANTA workstation UI."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from vanta.app import theme


def icon(name: str, color: str = theme.TEXT_DIM, size: int = 16):
    try:
        return qta.icon(name, color=color)
    except Exception:
        return qta.icon("fa5s.circle", color=color)


def label(text: str = "", role: str | None = None, *, wrap: bool = False, selectable: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return widget


def button(text: str, *, kind: str | None = None, icon_name: str | None = None, on_click: Callable | None = None, tooltip: str | None = None) -> QPushButton:
    # a single '&' would become a keyboard mnemonic and disappear from the label
    widget = QPushButton(re.sub(r"(?<!&)&(?!&)", "&&", text))
    if kind:
        widget.setProperty("kind", kind)
    if icon_name:
        color = "#1a1205" if kind == "primary" else theme.TEXT
        widget.setIcon(icon(icon_name, color))
        widget.setIconSize(QSize(15, 15))
    if on_click:
        widget.clicked.connect(on_click)
    if tooltip:
        widget.setToolTip(tooltip)
    widget.setCursor(Qt.PointingHandCursor)
    return widget


class Card(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None, *, padding: int = 16, spacing: int = 10, flat: bool = False, clickable: bool = False):
        super().__init__(parent)
        self.setProperty("role", "cardflat" if flat else "card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)
        self._clickable = clickable
        if clickable:
            self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if self._clickable and event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Pill(QLabel):
    """Rounded status pill with a coloured dot."""

    def __init__(self, text: str = "", color: str = theme.TEXT_DIM, parent: QWidget | None = None):
        super().__init__(parent)
        self.set(text, color)

    def set(self, text: str, color: str) -> None:
        self.setText(f"<span style='color:{color}'>●</span>&nbsp;&nbsp;{text}")
        self.setStyleSheet(f"QLabel {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER_HI}; border-radius: 11px; padding: 3px 10px; color: {theme.TEXT_DIM}; font-size: 12px; }}")


class Chip(QLabel):
    def __init__(self, text: str, color: str = theme.TEXT_DIM, *, filled: bool = False, parent: QWidget | None = None):
        super().__init__(text, parent)
        bg = color if filled else "transparent"
        fg = "#121212" if filled else color
        self.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; border: 1px solid {color}; border-radius: 9px; padding: 1px 8px; font-size: 11px; font-weight: 600; }}")


def status_chip(status: str) -> Chip:
    color = theme.STATUS_COLORS.get(status, theme.TEXT_DIM)
    return Chip(status.replace("_", " ").lower(), color)


class Thumb(QLabel):
    """Rounded, aspect-filled thumbnail from a file path."""

    def __init__(self, path: str | None = None, size: QSize = QSize(108, 192), radius: int = 8, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self._radius = radius
        self.setFixedSize(size)
        self.set_path(path)

    def set_path(self, path: str | None) -> None:
        canvas = QPixmap(self._size)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRect(QPoint(0, 0), self._size), self._radius, self._radius)
        painter.setClipPath(clip)
        source = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        if source.isNull():
            painter.fillRect(canvas.rect(), QColor(theme.PANEL_HI))
            painter.setPen(QColor(theme.TEXT_FAINT))
            painter.setFont(QFont(self.font().family(), 9))
            painter.drawText(canvas.rect(), Qt.AlignCenter, "no preview")
        else:
            scaled = source.scaled(self._size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (scaled.width() - self._size.width()) // 2
            y = (scaled.height() - self._size.height()) // 2
            painter.drawPixmap(0, 0, scaled, x, y, self._size.width(), self._size.height())
        painter.end()
        self.setPixmap(canvas)


class FlowLayout(QLayout):
    """Wrapping layout for cards and chips."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 12):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), dry=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout(rect, dry=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _layout(self, rect: QRect, *, dry: bool) -> int:
        x, y, row_h = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() and row_h > 0:
                x = rect.x()
                y += row_h + self._spacing
                row_h = 0
            if not dry:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            row_h = max(row_h, hint.height())
        return y + row_h - rect.y()

    def clear(self) -> None:
        while self._items:
            item = self._items.pop()
            if item.widget():
                item.widget().deleteLater()


def clear_layout(layout: QLayout) -> None:
    """Remove and delete everything in a layout (detached immediately, deleted later)."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
            item.layout().deleteLater()


def scroll(widget: QWidget, *, horizontal: bool = False) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    area.setFrameShape(QFrame.NoFrame)
    if not horizontal:
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return area


class FlowWidget(QWidget):
    """Container whose height follows its wrapped content (fixes overlap inside cards)."""

    def __init__(self, spacing: int = 10, parent: QWidget | None = None):
        super().__init__(parent)
        self.flow = FlowLayout(self, spacing=spacing)
        policy = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def addWidget(self, widget: QWidget) -> None:
        self.flow.addWidget(widget)
        self.updateGeometry()

    def clear(self) -> None:
        self.flow.clear()
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return max(1, self.flow.heightForWidth(width))

    def sizeHint(self) -> QSize:
        width = max(self.width(), 200)
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        width = max(self.width(), 120)
        return QSize(0, self.heightForWidth(width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        wanted = self.heightForWidth(self.width())
        if wanted != self.minimumHeight():
            self.setMinimumHeight(wanted)


def divider() -> QFrame:
    line = QFrame()
    line.setProperty("role", "divider")
    return line


def hrow(*widgets, spacing: int = 8, stretch_end: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(spacing)
    row.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        if w is None:
            row.addStretch(1)
        elif isinstance(w, QLayout):
            row.addLayout(w)
        else:
            row.addWidget(w)
    if stretch_end:
        row.addStretch(1)
    return row


class SliderRow(QWidget):
    """Labelled 0..1 slider with value readout and a hint."""

    changed = Signal(float)

    def __init__(self, title: str, value: float = 0.5, hint: str = "", *, left: str = "", right: str = ""):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(4)
        top = QHBoxLayout()
        self.title = label(title, "h3")
        self.value_label = label("", "mono")
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.value_label)
        layout.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(round(value * 100)))
        self.slider.valueChanged.connect(self._changed)
        layout.addWidget(self.slider)
        if left or right:
            ends = QHBoxLayout()
            ends.addWidget(label(left, "faint"))
            ends.addStretch(1)
            ends.addWidget(label(right, "faint"))
            layout.addLayout(ends)
        if hint:
            layout.addWidget(label(hint, "faint", wrap=True))
        self._changed(self.slider.value())

    def _changed(self, raw: int) -> None:
        self.value_label.setText(f"{raw / 100:.2f}")
        self.changed.emit(raw / 100)

    def value(self) -> float:
        return self.slider.value() / 100

    def set_value(self, value: float) -> None:
        self.slider.setValue(int(round(value * 100)))


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str, action: QPushButton | None = None):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)
        glyph = QLabel()
        glyph.setPixmap(icon(icon_name, theme.TEXT_FAINT).pixmap(44, 44))
        glyph.setAlignment(Qt.AlignCenter)
        layout.addWidget(glyph)
        heading = label(title, "h2")
        heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(heading)
        body = label(text, "dim", wrap=True)
        body.setAlignment(Qt.AlignCenter)
        body.setMaximumWidth(460)
        layout.addWidget(body, 0, Qt.AlignHCenter)
        if action:
            layout.addWidget(action, 0, Qt.AlignHCenter)


class Toast(QFrame):
    """Transient notification in the corner of the main window."""

    def __init__(self, parent: QWidget, text: str, color: str = theme.ACCENT, timeout_ms: int = 4200):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setStyleSheet(f"QFrame#toast {{ background: {theme.PANEL_HI}; border: 1px solid {color}; border-radius: 10px; }} QLabel {{ color: {theme.TEXT}; background: transparent; border: none; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        dot = QLabel(f"<span style='color:{color}; font-size:16px'>●</span>")
        layout.addWidget(dot)
        msg = label(text, wrap=True)
        msg.setMaximumWidth(380)
        layout.addWidget(msg)
        self.adjustSize()
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.97)
        self.setGraphicsEffect(effect)
        self._reposition()
        self.show()
        self.raise_()
        QTimer.singleShot(timeout_ms, self.deleteLater)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        existing = [c for c in parent.findChildren(Toast) if c is not self and c.isVisible()]
        offset = sum(c.height() + 8 for c in existing)
        self.move(parent.width() - self.width() - 24, parent.height() - self.height() - 40 - offset)


class StatTile(QFrame):
    def __init__(self, title: str, value: str = "—", caption: str = "", icon_name: str | None = None):
        super().__init__()
        self.setProperty("role", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        top = QHBoxLayout()
        if icon_name:
            glyph = QLabel()
            glyph.setPixmap(icon(icon_name, theme.ACCENT).pixmap(16, 16))
            top.addWidget(glyph)
        top.addWidget(label(title.upper(), "eyebrow"))
        top.addStretch(1)
        layout.addLayout(top)
        self.value = label(value, "h1")
        layout.addWidget(self.value)
        self.caption = label(caption, "faint", wrap=True)
        layout.addWidget(self.caption)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, value: str, caption: str | None = None) -> None:
        self.value.setText(value)
        if caption is not None:
            self.caption.setText(caption)
