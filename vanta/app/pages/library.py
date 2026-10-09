"""Library: reference reels (links or files), footage and the licensed music library."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QMenu,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from vanta.analysis.media import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from vanta.app import theme
from vanta.app.widgets.common import (
    Card,
    EmptyState,
    FlowWidget,
    Thumb,
    button,
    label,
    scroll,
    status_chip,
)


class MediaCard(Card):
    def __init__(self, media: dict, on_action):
        super().__init__(padding=10, spacing=6)
        self.setFixedWidth(178)
        self.media = media
        vertical = (media.get("height") or 16) >= (media.get("width") or 9)
        self.body.addWidget(Thumb(media.get("thumb"), QSize(156, 277) if vertical else QSize(156, 88)), 0, Qt.AlignHCenter)
        title = label((media.get("title") or Path(media["path"]).stem)[:60], "h3", wrap=True)
        title.setMaximumHeight(38)
        self.body.addWidget(title)
        meta = f"{(media.get('duration_s') or 0):.1f}s"
        if media.get("uploader"):
            meta += f" · @{media['uploader'][:18]}"
        self.body.addWidget(label(meta, "faint"))
        row = QHBoxLayout()
        status = media.get("analysis_status", "pending")
        row.addWidget(status_chip(status))
        from vanta.analysis.pipeline import is_outdated

        if is_outdated(media):
            stale = status_chip("stale")
            stale.setText("outdated")
            stale.setToolTip("Analysed by an older VANTA (before exact colour decoding). Re-analyse for correct grading statistics.")
            row.addWidget(stale)
            row.addWidget(button("Re-analyse", kind="ghost", icon_name="fa5s.redo", on_click=lambda _=False: on_action("analyse", media)))
        row.addStretch(1)
        more = button("", kind="ghost", icon_name="fa5s.ellipsis-h")
        menu = QMenu(more)
        menu.addAction("Re-analyse", lambda: on_action("analyse", media))
        if media["kind"] == "reference":
            menu.addAction("Open in Reference Inspector", lambda: on_action("inspect", media))
        if media["kind"] == "footage":
            from vanta.core.camera_log import PROFILES

            current = (media.get("info_json") or {}).get("color_profile")
            profile_menu = menu.addMenu("Colour profile (camera log)")
            auto = profile_menu.addAction("Auto-detect")
            auto.setCheckable(True)
            auto.setChecked(current is None)
            auto.triggered.connect(lambda _=False: on_action("profile:", media))
            for key, prof in PROFILES.items():
                action = profile_menu.addAction(prof.label)
                action.setCheckable(True)
                action.setChecked(current == key)
                action.triggered.connect(lambda _=False, k=key: on_action(f"profile:{k}", media))
        menu.addAction("Disable" if media.get("enabled", 1) else "Enable", lambda: on_action("toggle", media))
        menu.addAction("Remove from library (file is kept)", lambda: on_action("remove", media))
        more.setMenu(menu)
        row.addWidget(more)
        self.body.addLayout(row)
        if media["kind"] == "footage":
            profile_note = _profile_note(media)
            if profile_note:
                self.body.addWidget(label(profile_note, "faint", wrap=True))
        if media.get("error"):
            err = label(media["error"][:140], "faint", wrap=True)
            err.setStyleSheet(f"color: {theme.ERR};")
            self.body.addWidget(err)
        if not media.get("enabled", 1):
            self.setStyleSheet("QFrame { opacity: 0.5; }")


def _profile_note(media: dict) -> str:
    from vanta.analysis.pipeline import load_analysis
    from vanta.core.camera_log import PROFILES

    chosen = (media.get("info_json") or {}).get("color_profile")
    if chosen in PROFILES:
        return f"colour: {PROFILES[chosen].label} (set by you)"
    if media.get("analysis_status") != "done":
        return ""
    detected = (load_analysis(media) or {}).get("color_profile") or {}
    key = detected.get("key")
    if key and key != "rec709":
        return f"colour: {PROFILES[key].label} (detected) — set the exact camera profile"
    return ""


class DropZone(QWidget):
    def __init__(self, on_files, extensions: set[str]):
        super().__init__()
        self.on_files = on_files
        self.extensions = extensions
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_dir():
                paths += [str(p) for p in path.rglob("*") if p.suffix.lower() in self.extensions]
            elif path.suffix.lower() in self.extensions:
                paths.append(str(path))
        if paths:
            self.on_files(paths)


class MediaGrid(DropZone):
    def __init__(self, page, kind: str, header: QWidget):
        super().__init__(lambda paths: page.add_files(paths, kind), VIDEO_EXTENSIONS)
        self.page = page
        self.kind = kind
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(14)
        layout.addWidget(header)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        self.flow = FlowWidget(spacing=14)
        holder_layout.addWidget(self.flow)
        holder_layout.addStretch(1)
        self.empty = EmptyState("fa5s.film" if kind == "reference" else "fa5s.video", "Nothing here yet", "Drop files here, or use the buttons above." if kind == "footage" else "Paste reel links above or drop video files. VANTA analyses each reel: shots, pacing, colour, text, effects, music.")
        layout.addWidget(self.empty)
        layout.addWidget(scroll(holder), 1)

    def refresh(self) -> None:
        self.flow.clear()
        items = self.page.service.library.list_media(self.kind)
        self.empty.setVisible(not items)
        for media in items:
            self.flow.addWidget(MediaCard(media, self.page.on_action))


class LibraryPage(QWidget):
    def __init__(self, bridge, window):
        super().__init__()
        self.bridge = bridge
        self.window = window
        self.service = bridge.service
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.refs = MediaGrid(self, "reference", self._ref_header())
        self.footage = MediaGrid(self, "footage", self._footage_header())
        self.tabs.addTab(self.refs, "Reference reels")
        self.tabs.addTab(self.footage, "Footage")
        self.tabs.addTab(self._music_tab(), "Music library")
        bridge.library_changed.connect(self.on_show)
        bridge.job.connect(lambda _h: self._schedule_refresh())
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.on_show)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)

    def _schedule_refresh(self) -> None:
        if not self._timer.isActive():
            self._timer.start(1200)

    # ---------------------------------------------------------- headers
    def _ref_header(self) -> QWidget:
        card = Card(padding=16)
        card.body.addWidget(label("Learn from reels you love", "h2"))
        card.body.addWidget(label("Paste Instagram / TikTok / YouTube Shorts links (one per line or mixed with text). Downloads are used for private style analysis only; reference music is never reused, and source videos are deleted after analysis unless you keep them in System › Ingest.", "dim", wrap=True))
        self.links = QPlainTextEdit()
        self.links.setPlaceholderText("https://www.instagram.com/reel/…\nhttps://www.instagram.com/reel/…")
        self.links.setFixedHeight(84)
        card.body.addWidget(self.links)
        row = QHBoxLayout()
        row.addWidget(label("Style profile", "dim"))
        self.profile = QComboBox()
        self.profile.setEditable(True)
        self.profile.setMinimumWidth(220)
        self.profile.setToolTip("Profiles keep coherent styles apart (e.g. night-moto-cinematic vs clean-daylight). Leave as auto to let VANTA suggest one.")
        row.addWidget(self.profile)
        row.addStretch(1)
        row.addWidget(button("Add files…", icon_name="fa5s.folder-open", on_click=lambda: self._pick("reference")))
        row.addWidget(button("Download & learn", kind="primary", icon_name="fa5s.cloud-download-alt", on_click=self._download))
        card.body.addLayout(row)
        return card

    def _footage_header(self) -> QWidget:
        card = Card(padding=16)
        card.body.addWidget(label("Your footage", "h2"))
        card.body.addWidget(label("Clips are referenced in place and never modified. VANTA indexes shots, quality, usable ranges, motion, faces, speech and searchable descriptions (the footage knowledge graph).", "dim", wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Re-analyse outdated", icon_name="fa5s.redo", on_click=self._reanalyse_outdated, tooltip="Re-run analysis for clips and reels measured by an older VANTA"))
        row.addWidget(button("Add folder…", icon_name="fa5s.folder-plus", on_click=self._pick_folder))
        row.addWidget(button("Add clips…", kind="primary", icon_name="fa5s.plus", on_click=lambda: self._pick("footage")))
        card.body.addLayout(row)
        return card

    def _music_tab(self) -> QWidget:
        page = DropZone(lambda paths: self.add_files(paths, "music"), AUDIO_EXTENSIONS | VIDEO_EXTENSIONS)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 18, 24, 18)
        card = Card(padding=16)
        card.body.addWidget(label("Licensed / owned music", "h2"))
        card.body.addWidget(label("Only tracks you have rights to. Confirm the licence per track; unconfirmed tracks can be planned with but block the final render. Platform-library trending sounds are added in the app after export (VANTA leaves a silent guide and a sync marker).", "dim", wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Add tracks…", kind="primary", icon_name="fa5s.music", on_click=lambda: self._pick("music")))
        card.body.addLayout(row)
        layout.addWidget(card)
        self.music_table = QTableWidget(0, 9)
        self.music_table.setHorizontalHeaderLabels(["", "Title", "BPM", "Key", "Length", "Drops", "LUFS", "Licence confirmed", "★"])
        self.music_table.verticalHeader().setVisible(False)
        self.music_table.setAlternatingRowColors(True)
        self.music_table.horizontalHeader().setStretchLastSection(False)
        self.music_table.setColumnWidth(0, 44)
        self.music_table.setColumnWidth(1, 320)
        self.music_table.setColumnWidth(7, 150)
        layout.addWidget(self.music_table, 1)
        return page

    # ---------------------------------------------------------- actions
    def on_show(self) -> None:
        current = self.profile.currentText()
        self.profile.clear()
        self.profile.addItem("auto (VANTA suggests)")
        for profile in self.service.library.list_profiles():
            self.profile.addItem(profile["name"])
        if current:
            self.profile.setCurrentText(current)
        self.refs.refresh()
        self.footage.refresh()
        self._refresh_music()

    def _profile_name(self) -> str | None:
        text = self.profile.currentText().strip()
        return None if not text or text.startswith("auto") else text

    def _download(self) -> None:
        handle = self.service.import_links(self.links.toPlainText(), profile_name=self._profile_name())
        if handle is None:
            self.bridge.toast.emit("No supported reel links found in the text.", theme.WARN)
            return
        self.links.clear()
        self.bridge.toast.emit("Downloading reels — they will be analysed automatically.", theme.LIVE)

    def _pick(self, kind: str) -> None:
        exts = AUDIO_EXTENSIONS if kind == "music" else VIDEO_EXTENSIONS
        pattern = " ".join(f"*{e}" for e in sorted(exts))
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose files", "", f"Media ({pattern})")
        if paths:
            self.add_files(paths, kind)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a footage folder")
        if folder:
            paths = [str(p) for p in Path(folder).rglob("*") if p.suffix.lower() in VIDEO_EXTENSIONS]
            self.add_files(paths, "footage")

    def add_files(self, paths: list[str], kind: str) -> None:
        records = self.service.import_files(paths, kind, profile_name=self._profile_name() if kind == "reference" else None)
        self.bridge.toast.emit(f"Added {len(records)} {kind} file(s); analysis queued.", theme.LIVE)
        self.on_show()

    def _reanalyse_outdated(self) -> None:
        count = self.service.reanalyse_outdated()
        self.bridge.toast.emit(f"Re-analysing {count} outdated item(s)." if count else "Everything is analysed by the current VANTA.", theme.LIVE if count else theme.OK)
        self._schedule_refresh()

    def on_action(self, action: str, media: dict) -> None:
        lib = self.service.library
        if action.startswith("profile:"):
            info = dict(media.get("info_json") or {})
            key = action.split(":", 1)[1] or None
            if key:
                info["color_profile"] = key
            else:
                info.pop("color_profile", None)
            lib.update_media(media["id"], info_json=info)
            self.bridge.toast.emit("Colour profile saved — it applies to the next plan.", theme.OK)
        elif action == "analyse":
            lib.update_media(media["id"], analysis_status="pending", analysis_path=None)
            self.service.analyse(media["id"])
        elif action == "toggle":
            lib.update_media(media["id"], enabled=0 if media.get("enabled", 1) else 1)
        elif action == "remove":
            lib.delete_media(media["id"])
        elif action == "inspect":
            self.window.navigate("style")
            self.window.pages["style"].inspect(media["id"])
            return
        self.on_show()

    def _refresh_music(self) -> None:
        from vanta.analysis.pipeline import load_analysis

        tracks = self.service.library.list_media("music")
        self.music_table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            analysis = load_analysis(track, self.service.settings) or {}
            audio = analysis.get("audio") or {}
            play = button("", kind="ghost", icon_name="fa5s.play", on_click=lambda _=False, p=track["path"], a=audio: self._preview(p, a))
            self.music_table.setCellWidget(row, 0, play)
            self.music_table.setItem(row, 1, QTableWidgetItem(track.get("title") or Path(track["path"]).stem))
            self.music_table.setItem(row, 2, QTableWidgetItem(f"{audio.get('bpm', 0):.0f}" if audio else track.get("analysis_status")))
            self.music_table.setItem(row, 3, QTableWidgetItem(audio.get("key", "")))
            self.music_table.setItem(row, 4, QTableWidgetItem(f"{audio.get('duration_s', track.get('duration_s') or 0):.0f}s"))
            self.music_table.setItem(row, 5, QTableWidgetItem(", ".join(f"{d:.0f}s" for d in audio.get("drops_s", [])[:3])))
            self.music_table.setItem(row, 6, QTableWidgetItem(f"{audio.get('integrated_lufs')}" if audio.get("integrated_lufs") is not None else ""))
            rights = track.get("rights_json") or {}
            box = QCheckBox("I own / licensed it")
            box.setChecked(bool(rights.get("platform_cleared") or rights.get("licence_confirmed")))
            box.toggled.connect(lambda checked, t=track: self._licence(t, checked))
            self.music_table.setCellWidget(row, 7, box)
            star = button("★" if track.get("favorite") else "☆", kind="ghost", on_click=lambda _=False, t=track: self._favorite(t))
            self.music_table.setCellWidget(row, 8, star)

    def _licence(self, track: dict, checked: bool) -> None:
        rights = dict(track.get("rights_json") or {})
        rights.update(licence_confirmed=checked, platform_cleared=checked, confirmed_by="user")
        self.service.library.update_media(track["id"], rights_json=rights)

    def _favorite(self, track: dict) -> None:
        self.service.library.update_media(track["id"], favorite=0 if track.get("favorite") else 1)
        self._refresh_music()

    def _preview(self, path: str, audio: dict) -> None:
        """15-second preview starting at the first drop (or 20% in)."""
        start = (audio.get("drops_s") or [audio.get("duration_s", 60) * 0.2])[0]
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.setPosition(int(max(0.0, start - 4) * 1000))
        self.player.play()
        QTimer.singleShot(15000, self.player.pause)
