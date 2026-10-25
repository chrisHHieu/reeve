"""TurnStateMixin: which sessions are running a turn, and what a turn's end sets going."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger("harness.manager")


class TurnStateMixin:
    def mark_running(self, session_id: str) -> None:
        self._running_sessions.add(session_id)
        self._claim_on_disk(session_id)

    def try_mark_running(self, session_id: str) -> bool:
        """Claim an idle session for one turn. It is atomic on the server's event loop."""
        if session_id in self._running_sessions:
            return False
        self._running_sessions.add(session_id)
        self._claim_on_disk(session_id)
        return True

    def _claim_on_disk(self, session_id: str) -> None:
        """The durable half of the claim: a claim this process never releases is a
        turn the next one picks up. A background child names its parent, so its report can be
        delivered when it finishes in a later process."""
        store = getattr(self, "continuity", None)
        if store is not None:
            store.claim_turn(session_id, parent=getattr(self, "_background_parents", {}).get(session_id))

    def is_running(self, session_id: str) -> bool:
        return session_id in self._running_sessions

    def mark_idle(self, session_id: str) -> None:
        """The one moment every turn path shares (the socket, a background delivery, a durable
        resume), and so where everything that follows a turn hooks in without adding latency to
        the response itself."""
        self._running_sessions.discard(session_id)
        # A turn cut off by the shutdown passes here too, cancelled, and its claim is the record
        # that it did not end: kept, so the next process picks it up. A turn that finished in
        # the shutdown's grace window is told apart at startup by its thread (it ends in an
        # answer), so keeping its claim costs one read, never a repeated turn.
        store = getattr(self, "continuity", None)
        if store is not None and not getattr(self, "shutting_down", False):
            store.release_turn(session_id)
        # A finished child's isolated worktree is released when it holds no work.
        self._release_child_worktree(session_id)
        # The goal driver's moment too. An armed goal opens its next round only here, so a
        # person's turn, which claims `running` first, always wins and a session with no goal
        # costs one `stat()`. It is scheduled on the server loop because this method is
        # synchronous and every turn path ends through it.
        self._schedule_goal_continue(session_id)
        self._maybe_autotitle(session_id)
        if session_id in self._promotion_rebuild:
            # A promotion happened this turn, so the cached engine is dropped and the next turn
            # rebuilds with the new primary (relative anchoring, the environment snapshot, git).
            self._promotion_rebuild.discard(session_id)
            self._engines.pop(session_id, None)

    # -- background shell tasks ----------------------------------------------------------

    def _shell_finished(self, session_id: str, info: dict[str, Any]) -> None:
        """A background `run_shell` task finished: wake its session with the result.

        It is called from the executor's watcher THREAD, so it only schedules the delivery on the
        manager's loop. This is the half that makes "do not poll" true. Without it the only way to
        learn a background command's outcome was `shell_task_output`, and every poll is a
        round trip with the whole context: a 61-step session once spent a fifth of its tokens
        polling a job it could have been woken by.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return  # a bare test manager has no loop, and there is nothing to wake
        try:
            asyncio.run_coroutine_threadsafe(self._deliver_shell_result(session_id, dict(info)), loop)
        except RuntimeError:
            pass  # the loop shut down between the check and the call

    async def _deliver_shell_result(self, session_id: str, info: dict[str, Any]) -> None:
        if self.session_store.load(session_id) is None:
            return  # the session was deleted while the command ran
        # FENCING, the rule subagent children get: a task started before the session's latest
        # `/undo` belongs to the span the person just hid, so its completion must not reopen the
        # conversation over undone work.
        if self._revert_generation.get(session_id, 0) > int(info.get("generation") or 0):
            logger.info(
                "dropped a background shell completion from a reverted span: task=%s session=%s",
                info.get("task_id"),
                session_id,
            )
            return
        task_id = str(info.get("task_id") or "?")
        output = str(info.get("output") or "").strip()
        if info.get("event") == "matched":
            # `notify_when`: the line the model waited for, while the job runs on.
            lines = [
                f"[background task {task_id} printed a line matching {info.get('pattern')!r} "
                f"(output line {info.get('line_number')}) — it is still running]",
                f"$ {str(info.get('command') or '')[:200]}",
            ]
            if output:
                lines.append(output)
            lines.append("Its end will be reported too. Continue with what you were waiting to do.")
        else:
            lines = [
                f"[background task {task_id} finished — exit {info.get('exit_code')}]",
                f"$ {str(info.get('command') or '')[:200]}",
            ]
            if info.get("pattern") and not info.get("matched"):
                lines.append(f"It ended without ever printing a line matching {info['pattern']!r}.")
            if output:
                lines.append(output)
            lines.append("Nothing to poll: this message is the notification. Continue with what you were doing.")
        exit_code = info.get("exit_code")
        await self.deliver_to_session(
            session_id,
            "\n".join(lines),
            # Display only: what a surface says on the line that opens the turn this starts.
            source={
                "synthetic": "background_shell",
                "task_id": task_id,
                "title": str(info.get("command") or "")[:200],
                "state": "matched" if info.get("event") == "matched" else "finished",
                **({"exit_code": exit_code} if isinstance(exit_code, int) and not isinstance(exit_code, bool) else {}),
            },
        )
