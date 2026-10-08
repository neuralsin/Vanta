"""Cooperative pause/step/stop controls checked between executor calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from vanta.core.schemas import RunMode


class RunStopped(RuntimeError):
    """Raised at a checkpoint after the user requests Stop."""


@dataclass
class RunControl:
    run_mode: RunMode = RunMode.WATCH
    watch_delay_s: float = 0.7
    paused: bool = False
    stopped: bool = False
    _resume_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _step_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def __post_init__(self) -> None:
        self._resume_event.set()
        self._step_event.set()

    async def checkpoint(self) -> None:
        """Wait for pause/step gates and the configured WATCH delay; call before each step."""
        if self.stopped:
            raise RunStopped("run stopped by user")
        await self._resume_event.wait()
        if self.stopped:
            raise RunStopped("run stopped by user")
        if self.run_mode == RunMode.STEP:
            await self._step_event.wait()
            self._step_event.clear()
        elif self.run_mode == RunMode.WATCH and self.watch_delay_s:
            await asyncio.sleep(self.watch_delay_s)
        if self.stopped:
            raise RunStopped("run stopped by user")

    def pause(self) -> None:
        if not self.stopped:
            self.paused = True
            self._resume_event.clear()

    def resume(self) -> None:
        if not self.stopped:
            self.paused = False
            self._resume_event.set()

    def next_step(self) -> None:
        if not self.stopped:
            self._step_event.set()

    def stop(self) -> None:
        self.stopped = True
        self.paused = False
        self._resume_event.set()
        self._step_event.set()

    def snapshot(self) -> dict[str, object]:
        return {"run_mode": self.run_mode.value, "paused": self.paused, "stopped": self.stopped}
