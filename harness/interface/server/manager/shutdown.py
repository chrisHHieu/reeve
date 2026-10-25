"""ShutdownMixin: closing the manager without losing the work in flight."""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("harness.manager")


class ShutdownMixin:
    #: How long a shutdown waits for turns in flight, in seconds: a compromise between "a step
    #: can finish" and "quit is not a hang". A turn that outlives it is killed and repaired on
    #: the next attach.
    SHUTDOWN_GRACE_SECONDS = 3.5

    async def aclose(self) -> None:
        # Two phases. First STOP admitting work and give the turns in flight a short window to
        # land, then take the machinery down. With only the second phase, a turn that was one
        # tool call from finishing died with the process and came back as a repaired dangling
        # turn: honest, but it threw away work the person had already paid for.
        self.shutting_down = True
        still_running = await self.drain_running()
        if still_running:
            logger.warning("shutdown: %d turn(s) still running after the grace window", still_running)
        # Before anything is closed: what the background commands still running had printed,
        # so their sessions are shown it after the restart.
        try:
            self.keep_job_tails()
        except Exception:  # noqa: BLE001 - a shutdown must never fail on bookkeeping
            logger.debug("shutdown: job tails not kept", exc_info=True)
        # Every open session's `session_end` hooks, while what they may touch is still up
        # (bounded: quitting is not a hang).
        from .session_end import end_all

        await end_all(list(getattr(self, "_engines", {}).values()))
        await self.scheduler.stop()
        await self.stop_gateway()
        await self.mcp.aclose()
        self.stop_whatsapp()
        # The preview door is a listener of ours: no grant outlives the server.
        door = getattr(self, "_preview_door", None)
        if door is not None:
            door.close()
        # Language servers are process trees of ours, like the MCP connections above: exiting
        # without closing them leaves orphaned servers that a restart cannot adopt. Terminating
        # them can block on a server that ignores the signal, so it runs off the event loop.
        await asyncio.to_thread(self.lsp.close)
        # The person's shells are process trees of ours too.
        self.close_user_terminals()
        self.audit_store.close()

    async def drain_running(self, timeout: float | None = None) -> int:
        """Wait, bounded, for turns in flight to finish, and return how many were still running
        when the window closed."""
        grace = self.SHUTDOWN_GRACE_SECONDS if timeout is None else timeout
        deadline = time.monotonic() + max(grace, 0.0)
        while True:
            running = len(self._running_sessions)
            if running == 0 or time.monotonic() >= deadline:
                return running
            await asyncio.sleep(0.05)
