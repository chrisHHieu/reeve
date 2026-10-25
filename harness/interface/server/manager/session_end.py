"""Ending a session's engine: its `session_end` hooks, before the session goes.

Two moments end an engine for good: the session is deleted, or the server shuts down. Every other
time an engine is dropped (a setting changed, a folder moved, plugins reloaded) the session goes on
in a new engine, so no end is announced. Both moments are bounded: a hook that hangs must not hold
a deletion or a quit.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger("harness.manager")

#: How long a deletion waits for the session's end hooks; a hook's own timeout is 10s by default.
DELETE_WAIT_SECONDS = 10.0
#: How long a shutdown waits for every session's end hooks together: quitting is not a hang.
SHUTDOWN_WAIT_SECONDS = 5.0


def end_before_delete(engine: Any, loop: Optional[asyncio.AbstractEventLoop]) -> None:
    """Run `engine`'s end hooks from the synchronous deletion path, waiting for them so they run
    while the session's files still exist. On the server's loop thread itself nothing may block,
    so the hooks are scheduled there instead."""
    end = getattr(engine, "end_session", None)
    if end is None:
        return
    try:
        asyncio.get_running_loop()
        on_a_loop = True
    except RuntimeError:
        on_a_loop = False
    try:
        if on_a_loop:
            asyncio.ensure_future(end("deleted"))
        elif loop is not None and loop.is_running() and not loop.is_closed():
            asyncio.run_coroutine_threadsafe(end("deleted"), loop).result(DELETE_WAIT_SECONDS)
        else:
            asyncio.run(asyncio.wait_for(end("deleted"), DELETE_WAIT_SECONDS))
    except Exception:  # noqa: BLE001 - a deletion never fails on a hook, timeouts included
        logger.debug("session_end on delete did not finish", exc_info=True)


async def end_all(engines: Iterable[Any], reason: str = "shutdown", timeout: float = SHUTDOWN_WAIT_SECONDS) -> None:
    """Run every engine's end hooks together, bounded as one wait."""
    ends = [engine.end_session(reason) for engine in engines if getattr(engine, "end_session", None)]
    if not ends:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*ends, return_exceptions=True), timeout)
    except asyncio.TimeoutError:
        logger.warning("shutdown: session_end hooks still running after %.0fs; going on", timeout)
