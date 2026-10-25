"""The loop in the always-on server that fires automations when they are due.

Two rules. A task that came due while the server was down fires once when it starts (`catchup`),
then keeps its schedule. A task whose previous run has not ended is skipped, never run twice at once.
What a run does is the `runner(task, trigger) -> TaskRun` passed in; the scheduler does not know the
engine or the manager.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Awaitable, Callable, Optional

from .models import ScheduledTask, TaskRun
from .store import TaskStore

logger = logging.getLogger("harness.integrations.automation")

Runner = Callable[[ScheduledTask, str], Awaitable[TaskRun]]


async def _cancelled(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class Scheduler:
    def __init__(
        self,
        store: TaskStore,
        runner: Runner,
        *,
        tick_seconds: float = 30.0,
        extra_tick: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.tick_seconds = tick_seconds
        # Something else to do each tick, such as waking the sessions whose wake time has come.
        self.extra_tick = extra_tick
        self._loop_task: Optional[asyncio.Task] = None
        self._busy: set[str] = set()  # ids of tasks with a run going now
        # Each run is its own asyncio task; holding it here keeps it from being collected mid-run.
        self._runs: set[asyncio.Task] = set()

    def start(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._loop_task is not None:
            await _cancelled(self._loop_task)
            self._loop_task = None
        # The runs are not the loop's children: one waiting on an approval would outlive the
        # scheduler unless it is cancelled here.
        for run in list(self._runs):
            await _cancelled(run)
        self._runs.clear()

    async def _loop(self) -> None:
        trigger = "catchup"
        while True:
            try:
                await self._tick(trigger=trigger)
            except Exception:
                logger.exception("scheduler %s failed", "catch-up" if trigger == "catchup" else "tick")
            trigger = "schedule"
            await asyncio.sleep(self.tick_seconds)

    async def _tick(self, *, trigger: str) -> None:
        for task in self.store.due():
            # Not awaited: a run may wait on an approval for hours, and that must hold up neither
            # this loop, the other due tasks nor the extra tick. `run_task` keeps runs apart.
            run = asyncio.create_task(self.run_task(task, trigger=trigger))
            self._runs.add(run)
            run.add_done_callback(self._runs.discard)
        if self.extra_tick is None:
            return
        try:
            await self.extra_tick()
        except Exception:
            logger.exception("scheduler extra_tick (wake resume) failed")

    def _already_ran(self, task: ScheduledTask) -> bool:
        """Whether a run finished after `task` was read. `_busy` only knows runs still going: a tick
        that read `due()` while a run was going, and starts after it ended, holds a stale copy, and
        without this check the task would fire a second time. A finished run moved the stored
        count and time, so the stale copy no longer matches."""
        stored = self.store.get(task.id)
        return stored is not None and (stored.run_count, stored.last_run) != (task.run_count, task.last_run)

    async def run_task(self, task: ScheduledTask, *, trigger: str) -> Optional[TaskRun]:
        if task.id in self._busy:
            logger.info("skipping %s — previous run still going", task.id)
            return None
        if self._already_ran(task):
            logger.info("skipping %s — it already ran since this tick read it", task.id)
            return None
        self._busy.add(task.id)
        try:
            run = await self.runner(task, trigger)
        except Exception as exc:
            logger.exception("task %s run failed", task.id)
            run = self.store.add_run(TaskRun(task_id=task.id, status="error", error=str(exc), trigger=trigger))
        finally:
            self._busy.discard(task.id)
        self._count(task.id, run)
        return run

    def _count(self, task_id: str, run: Optional[TaskRun]) -> None:
        """Put the run on the task's record; saving it works out the next time it fires."""
        task = self.store.get(task_id)
        if task is None:
            return
        task.run_count += 1
        task.last_run = run.started_at if run else None
        task.last_status = run.status if run else "error"
        self.store.save(task)
