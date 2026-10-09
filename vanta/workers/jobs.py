"""Background job runner with persisted states (manual 5, 25).

Two lanes: ``heavy`` (GPU/Resolve work, strictly sequential — one heavy model
or Resolve operation at a time) and ``light`` (downloads, imports, previews of
cached data). Job states follow QUEUED -> RUNNING -> SUCCEEDED / FAILED /
RETRYABLE / CANCELLED; retryable failures are retried with backoff.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from vanta.core.schemas import Job, JobStatus
from vanta.storage.event_store import EventStore

RETRYABLE_ERRORS = ("LeaseTimeout", "ModelUnavailable", "TimeoutError", "ConnectionError", "ResolveUnavailable")


class JobCancelled(RuntimeError):
    pass


@dataclass
class JobHandle:
    job: Job
    title: str
    lane: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    future: Future | None = None

    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


JobFn = Callable[[JobHandle], Any]


class JobRunner:
    def __init__(self, store: EventStore, *, on_update: Callable[[JobHandle], None] | None = None):
        self.store = store
        self.on_update = on_update or (lambda handle: None)
        self.lanes = {"heavy": ThreadPoolExecutor(max_workers=1, thread_name_prefix="vanta-heavy"), "light": ThreadPoolExecutor(max_workers=3, thread_name_prefix="vanta-light")}
        self.handles: dict[str, JobHandle] = {}
        self._lock = threading.Lock()

    def submit(self, run_id: str, kind: str, title: str, fn: JobFn, *, lane: str = "heavy", retries: int = 2) -> JobHandle:
        job = Job(run_id=run_id, kind=kind)
        self.store.create_job(job)
        handle = JobHandle(job=job, title=title, lane=lane)
        with self._lock:
            self.handles[job.id] = handle
        self.on_update(handle)

        def runner() -> Any:
            attempt = 0
            while True:
                if handle.cancelled:
                    handle.job = self.store.transition_job(job.id, JobStatus.CANCELLED)
                    self.on_update(handle)
                    return None
                handle.job = self.store.transition_job(job.id, JobStatus.RUNNING)
                self.on_update(handle)
                try:
                    handle.result = fn(handle)
                    handle.progress = 1.0
                    handle.job = self.store.transition_job(job.id, JobStatus.SUCCEEDED)
                    self.on_update(handle)
                    return handle.result
                except JobCancelled:
                    handle.job = self.store.transition_job(job.id, JobStatus.CANCELLED)
                    self.on_update(handle)
                    return None
                except Exception as exc:
                    name = type(exc).__name__
                    handle.error = f"{name}: {exc}"
                    retryable = name in RETRYABLE_ERRORS and attempt < retries and not handle.cancelled
                    if retryable:
                        handle.job = self.store.transition_job(job.id, JobStatus.RETRYABLE, error=handle.error)
                        self.on_update(handle)
                        attempt += 1
                        time.sleep(min(30, 3 * 2**attempt))
                        handle.job = self.store.transition_job(job.id, JobStatus.QUEUED)
                        continue
                    handle.job = self.store.transition_job(job.id, JobStatus.FAILED, error=handle.error + "\n" + traceback.format_exc(limit=6))
                    self.on_update(handle)
                    return None

        handle.future = self.lanes[lane].submit(runner)
        return handle

    def active(self) -> list[JobHandle]:
        with self._lock:
            return [h for h in self.handles.values() if h.job.status in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYABLE}]

    def recent(self, limit: int = 50) -> list[JobHandle]:
        with self._lock:
            return list(self.handles.values())[-limit:]

    def shutdown(self) -> None:
        for handle in self.active():
            handle.cancel()
        for pool in self.lanes.values():
            pool.shutdown(wait=False, cancel_futures=True)
