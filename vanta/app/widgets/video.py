"""Embedded video player (QtMultimedia) for previews, references and proxy renders."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from vanta.app import theme
from vanta.app.widgets.common import button, icon


def _fmt(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}.{(max(0, ms) % 1000) // 100}"


class VideoPlayer(QWidget):
    def __init__(self, *, vertical: bool = True):
        super().__init__()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget(self)
        self.video.setStyleSheet(f"background: #000; border-radius: 10px; border: 1px solid {theme.BORDER};")
        if vertical:
            self.video.setMinimumSize(270, 480)
        else:
            self.video.setMinimumSize(480, 270)
        self.player.setVideoOutput(self.video)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.video, 1)
        controls = QHBoxLayout()
        self.play_btn = button("", kind="ghost", icon_name="fa5s.play", on_click=self.toggle)
        controls.addWidget(self.play_btn)
        self.position = QSlider(Qt.Horizontal)
        self.position.sliderMoved.connect(self.player.setPosition)
        controls.addWidget(self.position, 1)
        self.time = QLabel("0:00.0")
        self.time.setProperty("role", "mono")
        controls.addWidget(self.time)
        layout.addLayout(controls)
        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(lambda d: self.position.setRange(0, d))
        self.player.playbackStateChanged.connect(self._state)

    def load(self, path: str | Path | None, *, autoplay: bool = False) -> None:
        if not path or not Path(path).is_file():
            self.player.setSource(QUrl())
            return
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        if autoplay:
            self.player.play()

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_seconds(self, seconds: float) -> None:
        self.player.setPosition(int(seconds * 1000))

    def _position(self, ms: int) -> None:
        if not self.position.isSliderDown():
            self.position.setValue(ms)
        self.time.setText(_fmt(ms))

    def _state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(icon("fa5s.pause" if playing else "fa5s.play", theme.TEXT))

    def stop(self) -> None:
        self.player.stop()
