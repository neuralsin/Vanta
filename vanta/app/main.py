"""VANTA desktop application entry point."""

from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path


def _configure_logging(data_dir: Path) -> Path:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "vanta.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[logging.FileHandler(path, encoding="utf-8")])
    return path


def main() -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PySide6.QtCore import QLockFile, Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

    from vanta.app import theme
    from vanta.config import get_settings

    app = QApplication(sys.argv)
    app.setApplicationName("VANTA")
    app.setOrganizationName("VANTA")
    from PySide6.QtGui import QIcon

    icon_path = Path(__file__).resolve().parents[2] / "assets" / "vanta.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    try:  # taskbar groups VANTA under its own icon instead of python.exe
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("VANTA.Editor")
    except Exception:
        pass
    theme.apply_theme(app)
    settings = get_settings()
    log_path = _configure_logging(settings.data_dir)

    lock = QLockFile(str(settings.data_dir / "vanta.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "VANTA", "VANTA is already running.")
        return 0

    pix = QPixmap(640, 360)
    pix.fill(QColor(theme.BG))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor(theme.ACCENT))
    painter.setFont(QFont(app.font().family(), 40, QFont.Black))
    painter.drawText(pix.rect().adjusted(0, -30, 0, 0), Qt.AlignCenter, "VANTA")
    painter.setPen(QColor(theme.TEXT_DIM))
    painter.setFont(QFont(app.font().family(), 11))
    painter.drawText(pix.rect().adjusted(0, 70, 0, 0), Qt.AlignCenter, "AI co-editor for DaVinci Resolve")
    painter.end()
    splash = QSplashScreen(pix)
    splash.show()
    splash.showMessage("Opening the knowledge base…", Qt.AlignBottom | Qt.AlignHCenter, QColor(theme.TEXT_FAINT))
    app.processEvents()

    def excepthook(kind, value, tb) -> None:
        text = "".join(traceback.format_exception(kind, value, tb))
        logging.getLogger("vanta").error(text)
        QMessageBox.critical(None, "VANTA error", f"{value}\n\nDetails were written to {log_path}")

    sys.excepthook = excepthook

    from vanta.app.bridge import Bridge
    from vanta.app.hud_server import HudServer
    from vanta.app.main_window import MainWindow
    from vanta.workers.service import get_service

    service = get_service()
    splash.showMessage("Connecting live services…", Qt.AlignBottom | Qt.AlignHCenter, QColor(theme.TEXT_FAINT))
    app.processEvents()
    bridge = Bridge(service)
    hud = HudServer(service, settings.hud_port)
    hud.start()
    window = MainWindow(bridge)
    window.show()
    splash.finish(window)
    QTimer.singleShot(200, lambda: window.pages["home"].on_show())
    if os.environ.get("VANTA_SMOKE_EXIT_MS"):  # automated launch check
        QTimer.singleShot(int(os.environ["VANTA_SMOKE_EXIT_MS"]), app.quit)
    code = app.exec()
    hud.stop()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
