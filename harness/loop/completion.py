"""CompletionMixin: the turn's answer had no tool calls; is it finished?

The gates a finish passes in the order they run: a steering message the person sent mid-turn is
delivered first; then the verification gate (code changed and nothing ran since), then the red-check
gate (the last check failed), then the user's `stop` hook. Each speaks once per turn: a gate that can
loop is worse than one that can be ignored once. Past them the turn ends, and the typed reason says
whether it was a verified finish (`completed`) or something weaker. Moved out of
`TurnEngine._loop_body`, unchanged.
"""
from __future__ import annotations

import time
from .nudges import VERIFY_NUDGE_TEXT
from harness.llm.providers import AssistantTurn
from harness.session.events import Event, EventType
from harness.session.turns import ATTACHED_BY, TurnEndReason
from typing import Any, AsyncIterator

from .loop_state import LoopState


class CompletionMixin:
    async def _finish_or_nudge(self, turn: AssistantTurn, state: LoopState) -> AsyncIterator[Event]:
        """Yield what finishing the turn emits, or nothing when a gate sends the model around again.
        Sets `state.next`: "go" to make another model call, "end" when the turn is over."""
        state.next = "end"
        if self._steering:
            self._inject_steering()
            state.next = "go"
            return
        # The verification gate: code changed this turn and nothing has run since
        # the last change. One note, then the model decides — the gate speaks once
        # and a second completion attempt goes through (a gate that can loop is
        # worse than one that can be ignored once).
        if (
            self._turn_wrote_code
            and not self._turn_ran_after
            and not self._turn_verify_nudged
            and not self._cancel.is_set()
            and "verify_nudge" not in self.ablated
        ):
            self._turn_verify_nudged = True
            self.messages.append(
                {
                    "role": "user",
                    "content": VERIFY_NUDGE_TEXT,
                    ATTACHED_BY: "verify_nudge",
                    "ts": time.time(),
                }
            )
            self.log.append(
                "verify/nudge", turn=self._turn_no, step=self._step_no, reason="no_run",
            )
            state.next = "go"
            return
        # The red-check gate: a failed last check earns one note.
        if self._nudge_failed_check():
            state.next = "go"
            return
        # A design page whose last check this turn still found something: one note.
        if self._nudge_unfixed_design():
            state.next = "go"
            return
        # LIFECYCLE HOOK (stop): the user's script gets a say before the turn
        # ends — "run the tests first" is the canonical one. A deny injects the
        # reason as a steering message and goes around once; the guard is the
        # same shape the verification gate uses, because a hook that can loop is
        # worse than one that can be ignored once.
        if not self._turn_stop_hooked:
            self._turn_stop_hooked = True
            stopping = await self._run_hooks(
                "stop",
                wrote_code=self._turn_wrote_code,
                unverified=self._turn_wrote_code and not self._turn_ran_after,
            )
            if stopping is not None:
                if stopping.context:
                    self.messages.append(
                        self._hook_message("stop", stopping.context)
                    )
                if stopping.denied:
                    self.messages.append(
                        self._hook_message(
                            "stop",
                            stopping.reason
                            or "a hook says this turn is not finished",
                        )
                    )
                    state.next = "go"
                    return
                for error in stopping.errors:
                    self._append_notice("info", f"a stop hook failed: {error}")
        # Typed turn-end reasons (P1): `length` means the answer was CUT and an
        # unverified finish means code changed with nothing run to prove it —
        # different facts from a finished one either way. The log names the
        # reason and the payload carries `truncated`/`unverified` for the
        # automation/GUI layers, without the "completed" status anything
        # downstream already matches on changing meaning.
        reason = self._completion_reason()
        # The answer step closes like any other: the completion path used to
        # return without a step/end, so every turn's final step dangled
        # (24 starts vs 21 ends in the first session anyone audited).
        self.log.append("step/end", turn=self._turn_no, step=self._step_no)
        self._log_turn_end(reason)
        end_payload: dict[str, Any] = {
            "status": "completed",
            "iterations": state.iterations,
            **self._changes_payload(),
        }
        if self._turn_truncated:
            end_payload["truncated"] = True
        if reason is TurnEndReason.COMPLETED_UNVERIFIED:
            # `truncated`'s twin: the same distinction for the automation and GUI
            # layers, without changing the "completed" status they match on. The
            # status says the turn finished; the flag says nothing proved the code
            # it changed still works.
            end_payload["unverified"] = True
        if reason is TurnEndReason.COMPLETED_CHECK_FAILED:
            # The last check RAN and FAILED, and nothing changed since —
            # a different fact from `unverified` (never ran one), carried as its
            # own flag so surfaces do not have to parse the reason string.
            end_payload["check_failed"] = True
        yield Event(
            EventType.TURN_END,
            end_payload,
        )
