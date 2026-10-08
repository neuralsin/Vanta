from __future__ import annotations

import asyncio

import pytest

from vanta.core.schemas import RunMode
from vanta.live.run_control import RunControl, RunStopped


def test_step_mode_waits_for_next_between_checkpoints() -> None:
    async def scenario() -> None:
        control = RunControl(run_mode=RunMode.STEP, watch_delay_s=0)
        await control.checkpoint()
        task = asyncio.create_task(control.checkpoint())
        await asyncio.sleep(0)
        assert not task.done()
        control.next_step()
        await asyncio.wait_for(task, timeout=0.2)

    asyncio.run(scenario())


def test_stop_releases_pause_and_fails_checkpoint() -> None:
    async def scenario() -> None:
        control = RunControl(run_mode=RunMode.STEP)
        control.pause()
        task = asyncio.create_task(control.checkpoint())
        await asyncio.sleep(0)
        control.stop()
        with pytest.raises(RunStopped):
            await asyncio.wait_for(task, timeout=0.2)

    asyncio.run(scenario())
