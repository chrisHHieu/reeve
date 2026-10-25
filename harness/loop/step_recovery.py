"""StepRecoveryMixin: what the turn loop does when a model call fails.

One place for the whole policy, in the order it applies: a context overflow routes into compaction;
a transient failure gets the retries its class is worth (providers/errors.py); a failure another
route can answer moves to the next route in the fallback chain; everything else ends the
turn with a notice that says what happened. Moved out of `TurnEngine._loop_body`, unchanged.
"""
from __future__ import annotations

import asyncio
from .limits import BACKOFF_SECONDS
from .message_shapes import assistant_message
from harness.context import compaction
from harness.llm.providers.errors import EmptyCompletion, RETRY_AFTER_MAX_WAIT_SECONDS, classify_stream_error, error_chain, friendly_model_error, retry_after_seconds, retry_budget
from harness.llm.providers.turn_errors import raised_at, turn_error_class
from harness.session.events import Event, EventType
from harness.session.turns import TurnEndReason
from typing import AsyncIterator

from harness.llm.providers.fallback import cooldown_seconds, fallback_eligible, next_route
from .loop_state import LoopState, StepState


class StepRecoveryMixin:
    async def _recover_failed_step(
        self, exc: Exception, state: LoopState, step: StepState
    ) -> AsyncIterator[Event]:
        """Handle `exc`, yielding the events it earns. Sets `state.next`: "go" when the call is to be
        made again (a retry, a fallback route, a compaction), "end" when the turn is over."""
        state.next = "end"
        # A raw context-overflow 400 (compaction mispredicted, e.g. the estimate
        # path) routes into the compaction policy instead of surfacing. The retry
        # is progress-guarded: each pass moves the boundary forward or gives up,
        # so a model that keeps overflowing still terminates in the error path.
        if compaction.is_context_overflow(exc) and not self._cancel.is_set():
            yield Event(EventType.COMPACTING, {})
            notice = await self._compact_now(force=True)
            if notice:
                self._append_notice("compacted", notice.text, **notice.numbers)
                yield Event(
                    EventType.COMPACTED, {"text": notice.text, **notice.numbers}
                )
                state.next = "go"
                return
        # A transient failure gets the attempts its CLASS is worth (the taxonomy
        # in providers/errors.py): a rate limit is the provider asking for
        # patience and gets more; a permanent failure gets zero and surfaces at
        # once. The partial is deliberately NOT written to history first: the
        # retry re-sends the same request, so an extra assistant message would
        # both break the cached prefix and hand the model its own truncated
        # sentence to continue from. Nothing is lost — the user already watched
        # it arrive, and it is written below if we give up.
        budget = retry_budget(exc)
        if isinstance(exc, EmptyCompletion):
            signature = getattr(exc, "signature", None)
            if signature is not None and signature == state.last_empty_signature:
                # Two IDENTICAL empties in a row: the same route served the same
                # nothing twice, so another attempt buys another reroute, not an
                # answer (the deterministic-empty rule — measured here
                # on `openrouter:z-ai/glm-5.3-flash`, whose Z.AI upstream returned
                # empty on 2 of 2 attempts while its siblings answered).
                budget = state.attempts
            state.last_empty_signature = signature
        requested = retry_after_seconds(exc)
        # A provider that names a wait longer than a turn will sit through is
        # telling us a retry is not available on any timescale worth holding
        # the user's answer for. Retrying early anyway is the one thing the
        # header exists to prevent — and it burns the whole budget (rate_limit
        # gets four attempts) doing it. So say the number and end the turn as
        # a retriable error: the Retry affordance is already there, and the
        # user can also switch models and keep working.
        abandoned = (
            requested is not None
            and requested > RETRY_AFTER_MAX_WAIT_SECONDS
            and budget > 0
            and not self._cancel.is_set()
        )
        if not abandoned and state.attempts < budget and not self._cancel.is_set():
            state.attempts += 1
            delay = requested or BACKOFF_SECONDS[
                min(state.attempts - 1, len(BACKOFF_SECONDS) - 1)
            ]
            reason = classify_stream_error(exc)
            # The session's backend is the fault: the retry goes out on a new
            # routing key, or it reaches the same backend again.
            rerouted = reason == "routing" and self._reroute()
            self.log.append(
                "llm/retry",
                turn=self._turn_no,
                step=self._step_no,
                attempt=state.attempts,
                of=budget,
                reason=reason,
                delay=delay,
                error=error_chain(exc),
                **({"rerouted": True} if rerouted else {}),
            )
            yield Event(
                EventType.RETRYING,
                {
                    "attempt": state.attempts,
                    "of": budget,
                    "delay": delay,
                    "error": str(exc),
                    # The surface rendered deltas for an answer that is being
                    # thrown away; without this it would concatenate the discarded
                    # fragment onto the real one.
                    "discard_partial": bool(step.streamed or step.streamed_reasoning),
                },
            )
            try:
                await asyncio.wait_for(self._cancel.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass  # waited it out, which is the normal path
            if self._cancel.is_set():
                self._append_notice("interrupted")
                self.log.append("step/end", turn=self._turn_no, step=self._step_no)
                self._log_turn_end(TurnEndReason.INTERRUPTED)
                yield Event(
                    EventType.INTERRUPTED,
                    {"iterations": state.iterations, **self._changes_payload()},
                )
                state.next = "end"
                return
            # Re-running one model call is the same step again, not another one.
            # Charging it to `max_iterations` would let a flaky connection eat a
            # turn's whole budget without the agent having done any work.
            state.iterations -= 1
            state.next = "go"
            return
        # A route that just failed in a way another model can answer gets one try
        # on the next route in the chain: cool the failed route,
        # switch (which resets the tokenizer signal, re-applies the thinking
        # patch and appends the persisted `model_switch` notice), and re-send.
        # The partial is dropped from history exactly like a retry — but
        # recorded, so the log still answers what was streaming when the route
        # died. The chain is finite and cooling routes are skipped, so
        # exhaustion falls through to the error path below.
        if (
            self.model_fallback
            and not self._cancel.is_set()
            and fallback_eligible(exc)
        ):
            route = next_route(
                self.model, self.model_fallback, self.fallback_cooldown
            )
            if route is not None:
                previous = self.model
                cooled = self.fallback_cooldown.cool(
                    previous, cooldown_seconds(exc)
                )
                self.log.append(
                    "llm/fallback",
                    turn=self._turn_no,
                    step=self._step_no,
                    **{"from": previous, "to": route},
                    reason=classify_stream_error(exc),
                    cooldown=round(cooled, 1),
                    error=error_chain(exc),
                )
                notice = self.switch_model(route)
                yield Event(
                    EventType.FALLBACK,
                    {
                        "from": previous,
                        "to": route,
                        "reason": classify_stream_error(exc),
                        "error": str(exc),
                        "cooldown": round(cooled, 1),
                        "notice": notice,
                    },
                )
                state.attempts = 0
                state.last_empty_signature = None
                # Re-sending one model call is the same step again, not another
                # one — the same refund the retry path takes.
                state.iterations -= 1
                state.next = "go"
                return
        # Same contract as the stop path below: the partial the user watched
        # arrive survives the failure.
        if step.streamed or step.streamed_reasoning:
            self._log_partial_assistant(step.streamed, step.streamed_reasoning)
            self.messages.append(assistant_message(step.partial_turn()))
        friendly = friendly_model_error(self.model, exc)
        from harness.llm import media_support
        from harness.llm.providers.errors import media_rejected

        if friendly is None and media_support.has_media(self.messages) and media_rejected(exc):
            self._media_refused = {*getattr(self, "_media_refused", ()), self.model}
            friendly = (
                f"{self.model} did not accept the recording over this connection "
                f"({str(exc)[:160]}). From now on it is named instead of sent — "
                "send your message again, or switch to a model that takes audio or video."
            )
        notice_text = friendly or str(exc)
        if abandoned:
            notice_text = (
                f"{self.model} asked to wait {requested:.0f}s before the next "
                "request — longer than this turn will sit. Nothing was lost: "
                "send your message again once that window clears, or switch "
                "models to keep working."
            )
            self.log.append(
                "llm/retry",
                turn=self._turn_no,
                step=self._step_no,
                attempt=state.attempts,
                of=budget,
                reason=classify_stream_error(exc),
                delay=requested,
                abandoned=True,
                retry_after=requested,
                error=str(exc)[:500],
            )
        payload = {
            "error": notice_text,
            "error_type": type(exc).__name__,
        }
        if friendly or abandoned:
            payload["raw"] = str(exc)
        if abandoned:
            payload["retry_after"] = requested
        self._append_notice(
            "error",
            notice_text,
            **({"retry_after": requested} if abandoned else {}),
        )
        self.log.append("step/end", turn=self._turn_no, step=self._step_no)
        kind = turn_error_class(exc)
        self._log_turn_end(TurnEndReason.ERROR, error=kind, error_at=raised_at(exc) if kind == "code" else "")
        payload.update(self._changes_payload())
        yield Event(EventType.ERROR, payload)
        state.next = "end"
        return
