"""Bridges service callbacks (worker threads) into Qt signals and holds UI-wide state."""

from __future__ import annotations

import threading
from dataclasses import asdict

from PySide6.QtCore import QObject, QTimer, Signal

from vanta.models.scheduler import scheduler
from vanta.workers.jobs import JobHandle
from vanta.workers.service import VantaService


class Bridge(QObject):
    event = Signal(dict)
    job = Signal(object)
    telemetry = Signal(dict)
    resolve = Signal(dict)
    run_changed = Signal(str)
    project_changed = Signal(str)
    library_changed = Signal()
    toast = Signal(str, str)

    def __init__(self, service: VantaService):
        super().__init__()
        self.service = service
        self.current_run: str | None = None
        self.current_project: str | None = None
        service.subscribe(self.event.emit)
        service.subscribe_jobs(self.job.emit)
        self.job.connect(self._on_job)
        self._tele = QTimer(self)
        self._tele.timeout.connect(self._emit_telemetry)
        self._tele.start(1500)
        self._resolve_timer = QTimer(self)
        self._resolve_timer.timeout.connect(self.refresh_resolve)
        self._resolve_timer.start(15000)
        self._resolve_busy = False
        self.last_resolve: dict = {"connected": False, "reason": "checking…"}
        QTimer.singleShot(400, self.refresh_resolve)

    def _on_job(self, handle: JobHandle) -> None:
        if handle.job.status.value in {"SUCCEEDED", "FAILED"} and handle.job.kind.startswith(("analyse", "download", "style")):
            self.library_changed.emit()

    def _emit_telemetry(self) -> None:
        data = asdict(scheduler.telemetry())
        data["jobs"] = [{"title": h.title, "progress": h.progress, "message": h.message, "status": h.job.status.value} for h in self.service.jobs.active()]
        self.telemetry.emit(data)

    def refresh_resolve(self) -> None:
        if self._resolve_busy:
            return
        self._resolve_busy = True

        def work() -> None:
            try:
                status = self.service.resolve_status()
            except Exception as exc:
                status = {"connected": False, "reason": str(exc)}
            self.last_resolve = status
            self.resolve.emit(status)
            self._resolve_busy = False

        threading.Thread(target=work, daemon=True).start()

    def set_run(self, run_id: str) -> None:
        self.current_run = run_id
        self.run_changed.emit(run_id)

    def set_project(self, project_id: str) -> None:
        self.current_project = project_id
        self.project_changed.emit(project_id)
