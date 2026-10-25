"""Fire-and-forget tasks the server starts and never awaits.

asyncio holds a running task only weakly, so a task nobody stores can be collected in
mid-flight: the coroutine is cancelled and, with nobody awaiting it, silently.
A one-click sign-in is exactly that shape (minutes spent waiting on a browser while the GUI
polls for a flip that never comes), and it once failed this way with no trace in the log.
Tasks started through `TaskKeeper.spawn` are held until they finish, and what they raise is
printed instead of vanishing.
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Coroutine


class TaskKeeper:
    def __init__(self) -> None:
        self.tasks: set[asyncio.Task] = set()

    def spawn(self, coro: Coroutine, *, what: str) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(lambda done: self._finished(done, what))
        return task

    def _finished(self, task: asyncio.Task, what: str) -> None:
        self.tasks.discard(task)
        if task.cancelled():
            print(f"[reeve] background task cancelled: {what}")
            return
        error = task.exception()
        if error is not None:
            print(f"[reeve] background task failed: {what}: {error!r}")
            traceback.print_exception(type(error), error, error.__traceback__)
