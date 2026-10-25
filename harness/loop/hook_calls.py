"""HookCallsMixin: running the session's lifecycle hooks (harness/loop/hooks.py)."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from harness.loop.hooks import HookOutcome, hook_payload
from harness.session.turns import ATTACHED_BY

logger = logging.getLogger(__name__)


class HookCallsMixin:
    def _hook_runner(self) -> Any:
        """The session's hook runner with its log bound, or None.

        The manager attaches `self.log` after `build_engine`, so the runner's log is filled in here
        at first use and not at construction. A runner built with a stale log reference would record
        nothing and look as if it had never run."""
        runner = getattr(self, "hooks", None)
        if runner is not None and runner.log is None:
            runner.log = self.log
        return runner

    async def end_session(self, reason: str) -> None:
        """Run `session_end` once, for an engine whose `session_start` ran: the pair a hook
        written for a session's lifetime (a log, a cleanup) expects. `reason` is "deleted" or
        "shutdown". It steers nothing, since no turn follows, and a failure is the hook's own:
        ending a session must never fail on it."""
        if not getattr(self, "_session_started", False) or getattr(self, "_session_ended", False):
            return
        self._session_ended = True
        try:
            await self._run_hooks("session_end", reason=reason)
        except Exception:  # noqa: BLE001 - a hook must not cost the deletion or the shutdown
            logger.debug("session_end hooks failed", exc_info=True)

    def _hook_message(self, event: str, context: str) -> dict[str, Any]:
        """The message a hook's context travels in. The model sees it, so it is logged as a
        `steering/message` naming the hook as its source (invariant 0001)."""
        self.log.append(
            "steering/message",
            turn=self._turn_no,
            step=self._step_no,
            text=context,
            source=f"hook:{event}",
        )
        return {"role": "user", "content": context, ATTACHED_BY: "hook", "ts": time.time()}

    async def _run_hooks(self, event: str, *, tool: str = "", **payload: Any) -> Optional[HookOutcome]:
        runner = self._hook_runner()
        if runner is None or not runner.enabled:
            return None
        return await runner.run(event, hook_payload(**payload), tool=tool)
