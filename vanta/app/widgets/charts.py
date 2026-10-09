"""Small custom-painted charts (no plotting dependency): bars, sparkline, calibration, swatches."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from vanta.app import theme


class BarChart(QWidget):
    """Horizontal labelled bars for shares/frequencies (0..1 or raw values)."""

    def __init__(self, items: list[tuple[str, float]] | None = None, *, color: str = theme.ACCENT, max_value: float | None = None):
        super().__init__()
        self.items = items or []
        self.color = color
        self.max_value = max_value
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_items(self, items: list[tuple[str, float]]) -> None:
        self.items = items
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(260, max(28, 24 * len(self.items) + 4))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        label_w = min(150, int(self.width() * 0.42))
        top = max((v for _, v in self.items), default=1.0)
        top = self.max_value or top or 1.0
        p.setFont(QFont(self.font().family(), 9))
        for i, (name, value) in enumerate(self.items):
            y = 4 + i * 24
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(QRectF(0, y, label_w - 8, 18), Qt.AlignVCenter | Qt.AlignLeft, name)
            track = QRectF(label_w, y + 5, self.width() - label_w - 46, 8)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.BORDER))
            p.drawRoundedRect(track, 4, 4)
            fill = QRectF(track.x(), track.y(), max(2.0, track.width() * min(1.0, value / top)), track.height())
            p.setBrush(QColor(self.color))
            p.drawRoundedRect(fill, 4, 4)
            p.setPen(QColor(theme.TEXT))
            text = f"{value:.0%}" if top <= 1.0 else f"{value:.1f}"
            p.drawText(QRectF(self.width() - 42, y, 42, 18), Qt.AlignVCenter | Qt.AlignRight, text)
        p.end()


class Sparkline(QWidget):
    def __init__(self, values: list[float] | None = None, *, color: str = theme.LIVE, height: int = 56, fill: bool = True, markers: list[float] | None = None):
        super().__init__()
        self.values = values or []
        self.color = color
        self.fill = fill
        self.markers = markers or []
        self.setFixedHeight(height)

    def set_values(self, values: list[float], markers: list[float] | None = None) -> None:
        self.values = values
        if markers is not None:
            self.markers = markers
        self.update()

    def paintEvent(self, _event) -> None:
        if len(self.values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height() - 6
        lo, hi = min(self.values), max(self.values)
        span = (hi - lo) or 1.0
        pts = [QPointF(i / (len(self.values) - 1) * w, 3 + h - (v - lo) / span * h) for i, v in enumerate(self.values)]
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        if self.fill:
            area = QPainterPath(path)
            area.lineTo(w, h + 3)
            area.lineTo(0, h + 3)
            area.closeSubpath()
            c = QColor(self.color)
            c.setAlpha(40)
            p.fillPath(area, c)
        p.setPen(QPen(QColor(self.color), 2))
        p.drawPath(path)
        p.setPen(QPen(QColor(theme.ACCENT), 1, Qt.DashLine))
        for m in self.markers:
            x = m * w
            p.drawLine(QPointF(x, 0), QPointF(x, h + 3))
        p.end()


class CalibrationChart(QWidget):
    """Predicted confidence vs observed acceptance (manual 10/18)."""

    def __init__(self):
        super().__init__()
        self.bins: list[dict] = []
        self.setMinimumHeight(220)

    def set_bins(self, bins: list[dict]) -> None:
        self.bins = bins
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = 28
        rect = QRectF(m, 8, self.width() - m - 10, self.height() - m - 8)
        p.setPen(QPen(QColor(theme.BORDER_HI), 1))
        p.drawRect(rect)
        p.setPen(QPen(QColor(theme.TEXT_FAINT), 1, Qt.DashLine))
        p.drawLine(rect.bottomLeft(), rect.topRight())
        p.setFont(QFont(self.font().family(), 8))
        p.setPen(QColor(theme.TEXT_FAINT))
        p.drawText(QRectF(rect.left(), rect.bottom() + 4, rect.width(), 16), Qt.AlignCenter, "predicted confidence")
        p.save()
        p.translate(10, rect.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-60, -8, 120, 16), Qt.AlignCenter, "accepted")
        p.restore()
        if not self.bins:
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(rect, Qt.AlignCenter, "No resolved decisions yet")
            p.end()
            return
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        prev = None
        for b in self.bins:
            x = rect.left() + b["predicted"] * rect.width()
            y = rect.bottom() - b["accepted"] * rect.height()
            pt = QPointF(x, y)
            if prev:
                p.drawLine(prev, pt)
            p.setBrush(QColor(theme.ACCENT))
            radius = 3 + min(8, b["n"] ** 0.5)
            p.drawEllipse(pt, radius, radius)
            prev = pt
        p.end()


class Swatches(QWidget):
    """Colour palette strip (dominant colours or target look relations)."""

    def __init__(self, colors: list[tuple[str, float]] | None = None, height: int = 26):
        super().__init__()
        self.colors = colors or []
        self.setFixedHeight(height)

    def set_colors(self, colors: list[tuple[str, float]]) -> None:
        self.colors = colors
        self.update()

    def paintEvent(self, _event) -> None:
        if not self.colors:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        total = sum(max(0.02, s) for _, s in self.colors)
        x = 0.0
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 6, 6)
        p.setClipPath(clip)
        for color, share in self.colors:
            w = self.width() * max(0.02, share) / total
            p.fillRect(QRectF(x, 0, w + 1, self.height()), QColor(color))
            x += w
        p.end()


class TimelineStrip(QWidget):
    """Proportional shot strip with role colouring and a playhead for live runs."""

    ROLE_COLORS = {"hook": theme.ACCENT, "drop": theme.ERR, "outro": theme.PURPLE, "build": theme.LIVE}

    def __init__(self):
        super().__init__()
        self.shots: list[dict] = []
        self.playhead: float | None = None
        self.beats: list[float] = []
        self.setFixedHeight(46)

    def set_shots(self, shots: list[dict], beats: list[float] | None = None) -> None:
        self.shots = shots
        self.beats = beats or []
        self.update()

    def paintEvent(self, _event) -> None:
        if not self.shots:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        total = max(s["start_s"] + s["duration_s"] for s in self.shots) or 1.0
        w = self.width()
        for s in self.shots:
            x = s["start_s"] / total * w
            width = max(2.0, s["duration_s"] / total * w - 2)
            color = QColor(self.ROLE_COLORS.get(s.get("role") or "", theme.BORDER_HI))
            conf = s.get("confidence", 1.0)
            color.setAlpha(int(110 + 145 * conf))
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x, 10, width, 26), 4, 4)
        p.setPen(QPen(QColor(theme.TEXT_FAINT), 1))
        for b in self.beats:
            x = b / total * w
            p.drawLine(QPointF(x, 38), QPointF(x, 44))
        if self.playhead is not None:
            p.setPen(QPen(QColor(theme.LIVE), 2))
            x = self.playhead * w
            p.drawLine(QPointF(x, 2), QPointF(x, 44))
        p.end()
