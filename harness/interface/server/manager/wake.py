"""WakeMixin: resuming a session that put itself to sleep, and delivering messages out of band."""

from __future__ import annotations

import logging
from typing import Any, Optional

from harness.loop.selfwake import WAKE_TURNS_PER_HOUR

logger = logging.getLogger("harness.manager")


class WakeMixin:
    #: Wakes acted on per scheduler tick. The tick runs every 30 s, so a burst of due
    #: wakes is spread over ticks instead of arriving as one pile of concurrent turns —
    #: and a chain that has replicated itself `k` times cannot deliver all `k` at once.
    #: The remainder stays `due` and is picked up next tick. (It lived in the teams mixin
    #: until teams were removed.)
    MAX_WAKES_PER_TICK = 8

    async def _scheduler_tick(self) -> None:
        """The shared work of each tick: resume the self-wakes that are due."""
        await self.resume_due_wakes()

    async def resume_due_wakes(self, *, limit: Optional[int] = None) -> int:
        """Resume the sessions whose self-wakes are due, on each scheduler tick, and return how
        many. A suspended agent (it called `sleep_until`, `wake_on` or `wake_on_event` and ended
        its turn) is invoked again on its own session with a wake message, so it continues where
        it left off.

        This is the one place a session can START A TURN WITH NOBODY INVOLVED, so it is where the
        two bounds that cross turns are applied: the per-tick cap and the session's rolling-hour
        allowance. Neither exists inside the turn loop. Every limit there (`max_iterations`, the
        repeat guard) counts within one turn and resets when it ends, and a self-wake is the
        mechanism for opening the next one.
        """
        budget = self.MAX_WAKES_PER_TICK if limit is None else limit
        resumed = 0
        for wake in self.wakes.due()[:budget]:
            try:
                if self.wakes.take_turn_budget(wake.session_id):
                    await self._resume_wake(wake)
                    resumed += 1
                else:
                    await self._refuse_wake(wake)
            except Exception:
                pass
            finally:
                self.wakes.mark_fired(wake.id)
        return resumed

    async def _refuse_wake(self, wake) -> None:
        """A wake over the session's allowance is consumed WITHOUT opening a turn, since opening
        one is precisely what the budget refused. It is parked where a person will find it,
        because a chain that stops silently is indistinguishable from an agent that finished."""
        reason = (
            f"this session already opened {WAKE_TURNS_PER_HOUR} turns for itself this hour; "
            "the wake was dropped instead of starting another one"
        )
        logger.warning("wake for %s refused: %s", wake.session_id, reason)
        self.unrouted.record(wake.session_id, "-", self._wake_message(wake), reason=reason)
        await self.broadcast_session(wake.session_id, {"type": "wake_refused", "data": {"reason": reason}})

    async def _resume_wake(self, wake) -> None:
        message = self._wake_message(wake)
        # The chain depth travels with the turn: this one counts as one more turn the session
        # opened for itself, so a wake scheduled from inside it is refused once the chain stops
        # making progress.
        await self.deliver_to_session(wake.session_id, message, wake_depth=wake.depth + 1)

    @staticmethod
    def _wake_message(wake) -> str:
        note = f" (note: {wake.note})" if getattr(wake, "note", "") else ""
        if wake.kind == "completion":
            return f"⏰ Wake — the job `{wake.job_id}` you were waiting on has completed{note}. Continue where you left off."
        if wake.kind == "event":
            return f"⏰ Wake — the event `{wake.event_key}` you were waiting on has fired{note}. Continue where you left off."
        return f"⏰ Wake — the timer you set has fired{note}. Continue where you left off."

    async def deliver_to_session(
        self,
        session_id: str,
        message: str,
        *,
        source: Optional[dict[str, Any]] = None,
        wake_depth: Optional[int] = None,
        steer_if_busy: bool = True,
    ) -> Optional[str]:
        """Deliver an out-of-band message to a durable session and drive the turn.

        The agent stays resumable forever, so this works with no live socket. If it is busy,
        mid tool loop, the message is steered into the live turn at its next step and no
        colliding run starts. If it is idle, a fresh background turn runs: the results persist,
        and an approval it needs is parked for the person. Self-wake and
        channel-subscription delivery share it. `source` is the display-only sidecar for
        connector messages, while `message` is the model-facing text.

        It returns the turn's OUTCOME when a turn ran: the last `turn/end` status, or "error" or
        "interrupted" where those paths end without one. It returns None when nothing ran, which
        is when the session was busy and the caller asked to yield. Callers that only care that
        delivery was attempted ignore it.

        `steer_if_busy=False` makes a busy session a no-op instead of steering the message into
        the live turn. That is right for an automatic continuation (a goal round must not be
        spliced into the person's turn) and wrong for a connector message, which belongs in the
        conversation as it happens.

        `wake_depth` is the chain depth this delivery continues, and None means "not a wake",
        which RESETS the chain. That is deliberate for every non-wake delivery: a person typing, a
        channel message and a team feed are all somebody else talking, while the depth exists to
        bound an agent talking only to itself.
        """
        mcp_tools = await self.arm_mcp(session_id)
        engine = self.get_engine(session_id, extra_tools=mcp_tools)
        if engine is None:
            return None
        engine.wake_depth = wake_depth or 0
        if not self.try_mark_running(session_id):
            if steer_if_busy:
                engine.queue_steering(message, source)
            return None
        return (await self._drive_turn(session_id, engine, message, source=source)).status
