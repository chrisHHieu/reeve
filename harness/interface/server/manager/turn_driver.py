"""TurnDriverMixin: running one turn on an engine that has already been claimed."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from harness.loop import TurnEngine
from harness.loop.goal_ledger import capture_round
from harness.session.events import CHECKPOINT_EVENTS, STEP_EVENTS

logger = logging.getLogger("harness.manager")


@dataclass
class TurnResult:
    """One driven turn, internal to the manager.

    `status` is the outcome `deliver_to_session` has always returned: the last `turn/end`
    status, or "error" or "interrupted" where those paths end without one. `error` is the failure
    text a spawned child hands back to its parent, the same reason a background turn parks in the
    dead-letter store.
    """

    status: Optional[str] = None
    error: str = ""


class TurnDriverMixin:
    async def _drive_turn(
        self,
        session_id: str,
        engine: TurnEngine,
        message: str,
        *,
        source: Optional[dict[str, Any]] = None,
        dead_letter: bool = True,
        on_event: Optional[Callable[[str, dict[str, Any]], None]] = None,
        events: Optional[AsyncIterator[Any]] = None,
    ) -> TurnResult:
        """Run one turn on a CLAIMED engine and return its outcome.

        This is the canonical turn driver. `deliver_to_session` (self-wake, channel delivery,
        durable resume) and `spawn_subagent` share it, so checkpoints, the conversation store and
        the event broadcasts apply to a child exactly as they do to a person's turn. The caller
        owns the `try_mark_running` claim and every decision before the turn (the wake depth,
        steering); this method owns the event loop, the saves and the `mark_idle`.

        `dead_letter=False` is for a spawned child. Its failure is RETURNED to the parent as
        `status="error: …"`, and parking it in the dead-letter store as well would show the person
        a message that already has an owner.

        `on_event(type, data)` is a best-effort observer of each event. `spawn_subagent` uses it to
        turn the child's tool proposals into the parent's live `tool_metadata` frames. It is for
        display only, so an observer that raises is swallowed and never fails the turn it watches.

        `events` drives another of the engine's entries instead of a new message (`pick_up`, a
        turn the last process did not finish), with every save and broadcast of an
        ordinary turn.
        """
        result = TurnResult()
        try:
            stream = events if events is not None else engine.run(message, source=source)
            async for event in stream:
                kind = event.type.value
                if kind == "turn_end":
                    result.status = str((event.data or {}).get("status") or "completed")
                elif kind in ("error", "interrupted"):
                    # These two end the turn without a `turn_end`, and the goal driver needs the
                    # outcome to decide whether to continue.
                    result.status = kind
                # Every socket viewing the session gets the event, so a background turn (a channel
                # delivery, a self-wake, a durable resume) is seen live and not only on reselect.
                await self.broadcast_session(session_id, {"type": kind, "data": event.data})
                if on_event is not None:
                    try:
                        on_event(kind, event.data)
                    except Exception:
                        pass
                # Nobody is watching a background turn to read an inline error, so a dead model or
                # a failing tool would vanish. It is logged and parked in the dead-letter store.
                if kind == "error":
                    reason = str((event.data or {}).get("error") or "unknown error")
                    result.error = reason
                    logger.warning("background turn failed for %s: %s", session_id, reason)
                    if dead_letter:
                        self.unrouted.record(session_id, "-", message, reason=reason)
                # The same checkpoints the socket's turn takes. This path once saved only after
                # the loop, so a self-wake or a scheduled run killed by a restart lost every step
                # it had taken, and these are the runs nobody is watching.
                if event.type in CHECKPOINT_EVENTS:
                    self.save(session_id, engine)
                if event.type in STEP_EVENTS:
                    self.note_metrics_progress(session_id)
            self.save(session_id, engine)
        except Exception as exc:  # an unexpected raise out of the turn must not be swallowed
            logger.warning("background turn crashed for %s: %s", session_id, exc)
            result.status = "error"
            result.error = str(exc)
            if dead_letter:
                self.unrouted.record(session_id, "-", message, reason=str(exc))
            await self.broadcast_session(session_id, {"type": "error", "data": {"error": str(exc)}})
        finally:
            if (source or {}).get("kind") == "goal_round":
                # The goal ledger's facts are read before the idle edge lets another turn reset
                # the engine's spend counter and its change receipt.
                try:
                    self._goal_round_facts[session_id] = capture_round(engine)
                except Exception:
                    pass
            self.mark_idle(session_id)
            await self.broadcast_session(session_id, {"type": "turn_done", "data": {}})
        return result
