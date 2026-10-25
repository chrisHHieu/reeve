"""The controls a surface has over a running turn: stop, steer, change mode or plan."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from harness.safety.permissions import Mode
from harness.session.turns import is_user_turn


class ControlsMixin:
    def request_interrupt(self) -> None:
        """Stop the turn as soon as possible, from any state: mid-stream (the producer thread drops
        the stream between chunks), mid-tool (interrupt hooks kill the running command), waiting on an
        approval, question or plan (the wait resolves as interrupted), or between iterations (the loop
        checkpoint). Every pending tool call still gets an error result, so the history never carries
        an orphan: hosted templates reject those, and a durable resume would ask about them again."""
        self._cancel.set()
        for hook in self._interrupt_hooks:
            try:
                hook()
            except Exception:
                pass  # best effort: a dead executor must not block the stop

    def _begin_turn_controls(self) -> None:
        """Start a turn with the person's controls cleared and bound to this event loop.

        An `asyncio.Event` binds to the first loop that awaits it and raises RuntimeError on any
        other, and an engine outlives the loop of a single turn wherever turns run under
        `asyncio.run`. The engine races `self._cancel.wait()` against real work in three places (the
        stream reader, an approval wait and a running tool), and each race is decided by which future
        finished, so the raise looked exactly like the person pressing Stop. The failure was silent
        for that reason: from the second turn on, streams were cut mid-answer, approval prompts
        resolved themselves, and tool calls came back "interrupted by user" with nobody having
        pressed anything.

        A fresh event is also a cleared one, which every caller wanted anyway: a new turn must not
        inherit the last turn's Stop. Nothing outside this class holds `_cancel`, so replacing it
        strands no waiter.
        """
        self._cancel = asyncio.Event()
        self._uncertain_calls = set()
        self._turn_end_logged = False
        # `run()` sets the @-mentions right after this call, and a retry or resume must never
        # inherit the last turn's bypass.
        self.turn_mentions = []
        # The goal-round binding is likewise cleared here and re-derived from the turn's opening
        # user message, retries and resumes included.
        self.goal_round = 0
        # A message that missed the last turn's final read opens this one: nobody waits on it now.
        self.person_waiting.clear()
        self._person_steered_at = None
        self._close_dangling_turn()
        if self.changes is not None:
            self.changes.begin_turn()

    def _goal_round_of_last_turn(self) -> int:
        """The goal-round number of the CURRENT turn, read from its opening user message.

        It is derived and not stored, so `retry()` and `resume()`, which re-run a turn without
        receiving its source again, keep the completion authority the original turn had. The scan
        stops at the latest user message: anything older belongs to a previous turn, and an ordinary
        message from the person resets the binding to 0. A to-do nudge or a screenshot inside the
        round is not a message (`is_user_turn`); counting one as a message cost a retried or resumed
        round its completion authority.
        """
        opening = next((m for m in reversed(self.messages) if is_user_turn(m)), None)
        source = opening.get("source") if opening else None
        if not isinstance(source, dict) or source.get("kind") != "goal_round":
            return 0
        try:
            return max(0, int(source.get("round") or 0))
        except (TypeError, ValueError):
            return 0

    async def _interruptible(self, coro: Any, interrupted: Any) -> Any:
        """Await `coro`, but give back `interrupted` as soon as the person stops the turn. The
        pending task is cancelled, so an inbox card answered later does nothing."""
        task = asyncio.ensure_future(coro)
        stop = asyncio.ensure_future(self._cancel.wait())
        try:
            done, _ = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                return task.result()
            task.cancel()
            return interrupted
        finally:
            stop.cancel()

    def queue_steering(
        self,
        text: str,
        source: Optional[dict[str, Any]] = None,
        display: Optional[str] = None,
    ) -> None:
        """Hold a message for the live turn's next step.

        `display` is the `_display` sidecar: the transcript may show something other than the
        model-facing `text` (a forced skill's literal `/name ...` line), and the injected message
        carries the same sidecar a fresh `run` would give it.
        """
        self._steering.append((text, source, display))

    def person_steered(self) -> None:
        """A message the person typed has joined the live turn: what the step waits on gives up.

        A queued message is read after the step it arrived in, and a step can wait minutes on one
        command or a subagent. On the session store (2026-10-24), 48 steers came right after a tool
        that ran 30 s or more. The waits that read `person_waiting` hand their work to the
        background, where it keeps running and reports when it ends, so the step ends and the
        message is read. Only the person's own message does this: a harness note or a parent's
        message to its child waits for the step like before."""
        if self._person_steered_at is None:
            self._person_steered_at = time.monotonic()
        self.person_waiting.set()
        for hook in list(self._person_steer_hooks):
            try:
                hook()
            except Exception:  # noqa: BLE001 - waking a wait must never cost the message
                pass

    def add_person_steer_hook(self, hook: Callable[[], None]) -> None:
        """Run `hook` when the person steers: a world wakes the waits it has in flight."""
        self._person_steer_hooks.append(hook)

    def take_steering(self) -> list[tuple[str, Optional[dict[str, Any]], Optional[str]]]:
        """Hand back what was queued and never read, and forget it.

        A turn reads its queue after each step and once more before it finishes, so a message
        queued after that last read is still here when the turn has ended. The manager takes it
        then and opens a turn for it, instead of leaving it for a turn that may never
        come."""
        pending, self._steering = self._steering, []
        return pending

    def set_mode(self, mode: Mode, *, reason: str = "user") -> bool:
        """Adopt a permission mode and record the change in the session log.

        This is the one live write path for `permissions.mode`. Every surface goes through it: the
        websocket `set_mode` frame, the TUI's `/mode` and `/plan`, and the adoption of an approved
        plan in `_handle_plan_proposal`. So the durable log always agrees with the mode the requests
        actually ran under. `reason` names the doorway (`user` or `plan_approval`).

        It returns whether the mode moved, and the log row is written only then, so the event is a
        change and not a declaration.
        """
        mode = mode if isinstance(mode, Mode) else Mode(mode)
        previous = self.permissions.mode
        if mode == previous:
            return False
        self.permissions.mode = mode
        self.log.append("mode/changed", **{"from": previous.value, "to": mode.value, "reason": reason})
        return True

    def set_plan(self, on: bool, *, reason: str = "user") -> bool:
        """Switch the plan OVERLAY and record the change in the session log.

        Plan is not a mode (see `permissions.Mode`): it sits over whichever preset is selected and
        hard-denies consequential actions. This is the one live write path for `permissions.plan`,
        shared by the websocket `set_plan` frame, the TUI's `/plan` and the clearance an approved plan
        performs in `_handle_plan_proposal`, so the durable log agrees with the state the requests ran
        under. `reason` names the doorway (`user` or `plan_approval`).

        It returns whether the overlay moved, and the log row is written only then.
        """
        on = bool(on)
        if on == bool(self.permissions.plan):
            return False
        self.permissions.plan = on
        self.log.append("plan/mode", active=on, reason=reason)
        return True
