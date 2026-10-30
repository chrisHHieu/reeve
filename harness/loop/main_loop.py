"""MainLoopMixin: the model-and-tools loop of a turn, as the order of its phases."""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Optional

from harness.context import compaction
from harness.llm.providers import AssistantTurn
from harness.llm.providers.blocks import REASONING, TEXT, TOOL_CALL
from harness.llm.providers.errors import EmptyCompletion
from harness.llm.providers.turn_errors import raised_at
from harness.session.events import Event, EventType
from harness.session.turns import ATTACHED_BY, TurnEndReason
from harness.tools.argument_aliases import accepted_names, take_aliases
from harness.tools.todo import TODO_WRITE

from .loop_state import LoopState, StepState
from .message_shapes import assistant_message, sanitize_mangled_calls
from .nudges import TODO_NUDGE_AFTER, TODO_NUDGE_TEXT


class MainLoopMixin:
    async def _loop(self) -> AsyncIterator[Event]:
        """`_loop_body` under a finalizer. However the body ends (a normal return, an exception, a
        cancellation, or the consumer walking away with GeneratorExit) the open turn gets its
        `turn/end`. The log then holds no turn the engine left unclosed, which makes the crash repair
        in `_close_dangling_turn` the rare event and not the ordinary one."""
        body = self._loop_body()
        try:
            async for event in body:
                yield event
        except GeneratorExit:
            self._force_close_turn(TurnEndReason.INTERRUPTED)
            raise
        except asyncio.CancelledError:
            self._force_close_turn(TurnEndReason.INTERRUPTED)
            raise
        except Exception as exc:
            # No notice is written on this path, since the caller reports the crash, so the row is
            # the only place the log says what broke.
            self._force_close_turn(TurnEndReason.ERROR, error="crashed", error_at=raised_at(exc))
            raise
        finally:
            # Unwind the body's own frames even when the consumer vanished mid-yield. `aclose`
            # throws GeneratorExit into it at its suspension point, which runs the tool-execution
            # cleanup in the body's own `finally` blocks.
            await body.aclose()

    async def _loop_body(self) -> AsyncIterator[Event]:
        """The turn as the order of its phases. Each phase is a method below (or a mixin), so a
        policy is one place to read and one place to change. What the phases decide is carried in
        `LoopState.next`: "go" is another model call and "end" means the turn is over."""
        state = self._begin_loop()
        while True:
            # A background child that finished since the last step charges its spend here.
            self._fold_carried_tokens()
            # The check loop is tracked before the rail check, because the last tool
            # result may have been a failing check and the streak it feeds is one of the rails. A new
            # failed check with no edit since advances it, and a pass, an edit, an unknown exit or no
            # check at all resets it.
            self._track_check_streak()
            # A turn runs out in several ways: the iteration cap, a repeat loop that ignored every
            # reminder, or the token budget. All of them end the same way, with one final tool-less
            # call that writes a handoff, so a "continue" resumes from it and the turn does not die
            # as a bare error. The budget is checked here, at a step boundary, and not inside the
            # stream. Stopping in the middle of an answer would throw away the text the person is
            # reading, and one more step is bounded by the number itself.
            if self._rail_stopped(state.iterations):
                async for event in self._end_on_rail(state):
                    yield event
                return
            # The check-loop notice: one model-visible note at the notice threshold,
            # before the next request is built. It is not a stop, since the rail above is the hard
            # bound, and it speaks once per turn like every other nudge.
            self._nudge_check_loop()
            # A repair the last check proves is written down, and one this project made before is
            # recalled when its failure is back. It is never a stop.
            self._track_repairs()
            state.iterations += 1

            async for event in self._compaction_checkpoint():
                yield event
            self._note_tool_changes()
            self._note_revert_change()

            step = self._begin_step()
            try:
                async for event in self._stream_step(step):
                    yield event
            except Exception as exc:  # the provider failed
                async for event in self._recover_failed_step(exc, state, step):
                    yield event
                if state.next == "go":
                    continue
                return
            state.attempts = 0  # the stream finished, so the next model call starts whole
            state.last_empty_signature = None
            # A route that answered is healthy again. Its strike ladder starts over, so a failure
            # days later is skipped for a minute and not for the hours it earned in some earlier
            # outage.
            self.fallback_cooldown.reset(self.model)
            if self._cancel.is_set() and step.turn is None:
                async for event in self._end_interrupted_midstream(step, state):
                    yield event
                return
            turn, message = self._record_step(step)
            # The extension decision comes before the payload. A message that is still cut is not
            # emitted: the live stream keeps growing and the whole text lands in one item when it
            # stops being cut (or the cap is reached), which is also what a reload shows. A bubble
            # for each fragment would be a transcript the next reload contradicts.
            if not turn.tool_calls and self._maybe_continue_truncated(turn):
                continue
            yield self._assistant_event(turn, message)

            if not turn.tool_calls:
                async for event in self._finish_or_nudge(turn, state):
                    yield event
                if state.next == "go":
                    continue
                return

            async for event in self._run_tool_calls(turn, state):
                yield event
            if state.next == "end":
                return

    # -- the phases of one iteration, called by `_loop_body` in this order -------------------

    def _begin_loop(self) -> LoopState:
        """Clear the per-turn state every policy below reads, and return the loop's own."""
        self._turn_tool_calls = 0
        self._turn_saw_todo = False
        self._turn_todo_nudged = False
        # The verification gate's state for the turn: did code change, did anything run after the
        # last change, and has the gate already spoken once.
        self._turn_wrote_code = False
        self._turn_ran_after = False
        self._turn_code_stems = set()
        self._turn_verify_nudged = False
        self._turn_check_nudged = False
        # The unfixed-design note's state: each page's last check this turn, and whether
        # the note has spoken.
        self._turn_design_open = {}
        self._turn_design_nudged = False
        # The check-loop rail's state for the turn: the consecutive failed checks with no
        # file change since, the transcript index of the last check it counted, and whether the
        # notice has spoken.
        self._check_streak = 0
        self._check_streak_index = -1
        self._check_streak_outcome = None
        self._check_loop_noticed = False
        # A recalled repair is told once a turn for each failure, and a lesson is asked for once a
        # turn.
        self._turn_recalled = set()
        self._turn_lesson_asked = False
        # The token budget's counter. Provider traffic is counted per turn and not per session,
        # because a long conversation is many turns and each is meant to stand on its own.
        self._turn_tokens = 0
        # Output-token continuation: how many times this turn has been extended after the provider
        # cut an answer, and the assistant message being extended. Both belong to the turn, because a
        # cut answer is a fact about this answer and the next turn starts whole.
        self._truncation_continues = 0
        self._continued_message: Optional[dict[str, Any]] = None

        # Repetition is scoped to the turn. A person asking the same thing twice is a different
        # situation from the agent going in circles inside one answer.
        self.repeats.reset()
        self._malformed_args_streak = 0
        self._malformed_args_exhausted = False
        self._subagents_spawned = 0
        self._children_changed = {}
        return LoopState()

    def _rail_stopped(self, iterations: int) -> bool:
        """Whether a rail ends the turn here: the iteration cap, a repeat loop that ignored every
        reminder, a check loop, malformed arguments the model would not fix, or the token budget.
        It is checked at a step boundary, so the answer being read is never cut mid-stream."""
        return (
            self._iteration_cap_reached(iterations)
            or self.repeats.exhausted
            or self._check_loop_stopped()
            or self._malformed_args_exhausted
            or self._token_budget_spent()
        )

    async def _end_on_rail(self, state: LoopState) -> AsyncIterator[Event]:
        """End the turn the way every rail does: one final tool-less call writes a handoff, so a
        "continue" resumes from it and the turn does not die as a bare error."""
        stop_reason = self._turn_stop_reason(state.iterations)
        async for event in self._write_checkpoint():
            yield event
        self._log_turn_end(stop_reason)
        yield Event(
            EventType.TURN_END,
            {
                "status": stop_reason.value,
                "iterations": state.iterations,
                # The spend the budget measures, reported for the reason the iteration count is: a
                # surface should be able to say what the turn cost and not only that a counter
                # stopped it.
                "tokens": self._turn_tokens,
                **({"token_budget": self.token_budget} if self.token_budget else {}),
                **self._stuck_on(),
            },
        )

    async def _compaction_checkpoint(self) -> AsyncIterator[Event]:
        """Between tool turns, and before a new turn's first call: compact when it is due, else do
        the free cache-cold prune. There is deliberately no "wrap up" warning to the model."""
        notice = None
        why = self._compaction_why()
        if why:
            # The COMPACTING signal comes before the summarizer call, which takes seconds, so
            # surfaces can show progress and not a silent stall, and why it runs.
            yield Event(EventType.COMPACTING, {"why": why, "tokens": self._context_signal()})
            notice = await self._compact_now()
        else:
            # The cache-cold pass (`_opportunistic_prune`): aged tool results fold for free, and only
            # when the provider's prefix cache is already unusable, either idle past its TTL or
            # because a staged revert changed the view. Under real pressure the compaction path above
            # already runs the cheap lever, so this never races it. With the cache warm, aged
            # re-runnable output leaves in batches that repay the cache they cost (`_working_trim`).
            if not self._opportunistic_prune():
                self._working_trim()
        if notice:
            # The numbers ride the persisted notice too, and not only the event: a transcript
            # reloaded tomorrow should still answer "how much did that compaction buy" without
            # working it out again from token counters. They ride the event so a surface can say what
            # compaction bought and not only that it happened. The sentence is the model's, and
            # `reason` and `saved_pct` are the reader's.
            self._append_notice("compacted", notice.text, **notice.numbers)
            yield Event(EventType.COMPACTED, {"text": notice.text, **notice.numbers})

    def _begin_step(self) -> StepState:
        self._step_no += 1
        self.log.append("step/start", turn=self._turn_no, step=self._step_no)
        step = StepState()
        self._last_request_at = step.dispatched_at
        self._last_request_model = self.model
        return step

    async def _stream_step(self, step: StepState) -> AsyncIterator[Event]:
        """Make the model call and yield its deltas. A failure, an empty completion included,
        propagates to the loop, which hands it to `_recover_failed_step`."""
        async for chunk in self._astream():
            if step.first_token_at is None and (chunk.reasoning_delta or chunk.text_delta):
                step.first_token_at = time.time()
            # One fold, three events. The assembler owns which block a delta extends, and this loop
            # owns only what a surface is told and what the interrupt path can still finalize.
            for delta in step.blocks.push(chunk):
                if delta.kind == REASONING:
                    step.streamed_reasoning.append(delta.text)
                    yield Event(EventType.REASONING_DELTA, delta.as_dict())
                elif delta.kind == TEXT:
                    step.streamed.append(delta.text)
                    yield Event(EventType.ASSISTANT_DELTA, delta.as_dict())
                elif delta.kind == TOOL_CALL:
                    # For display only. The executed call comes from `turn`, whole and parsed: a
                    # surface may show a call being written, but nothing runs against a half-typed
                    # argument string.
                    yield Event(EventType.TOOL_CALL_DELTA, delta.as_dict())
            if chunk.turn is not None:
                step.turn = chunk.turn
        # A request that succeeded and said nothing is raised here, inside the `try`, so it travels
        # the retry path the transport failures already use and does not grow a second one beside it.
        # The give-up branch then reports it like any other model failure, and the turn does not end
        # "completed" with an empty message nobody can see.
        if step.turn is not None and step.turn.is_empty and not self._cancel.is_set():
            raise EmptyCompletion(
                "the model returned an empty response"
                + (f" (served by {step.turn.upstream})" if step.turn.upstream else ""),
                signature=(self.model, step.turn.upstream, step.turn.finish_reason),
            )

    async def _end_interrupted_midstream(self, step: StepState, state: LoopState) -> AsyncIterator[Event]:
        # Stopped mid-stream: keep exactly what the person watched arrive.
        if step.streamed or step.streamed_reasoning:
            self._log_partial_assistant(step.streamed, step.streamed_reasoning)
            self.messages.append(assistant_message(step.partial_turn()))
        self._append_notice("interrupted")
        self.log.append("step/end", turn=self._turn_no, step=self._step_no)
        self._log_turn_end(TurnEndReason.INTERRUPTED)
        yield Event(EventType.INTERRUPTED, {"iterations": state.iterations, **self._changes_payload()})
        state.next = "end"

    def _record_step(self, step: StepState) -> tuple[AssistantTurn, dict[str, Any]]:
        """Account for the finished call, log it, and append the assistant message, folded into the
        message it extends when the call continued a cut answer. Returns `(turn, message)`."""
        turn = step.turn
        if turn is None:
            turn = AssistantTurn()
        if turn.usage is not None:
            # The trigger signal: the prompt-side total that actually occupied the window on this
            # round trip (an estimate when it was never reported). It is stamped before the assistant
            # message is appended, so everything from here on, this turn's text and its tool results,
            # counts as the delta `_context_signal` adds.
            cleared = int((turn.context_cleared or {}).get("input_tokens") or 0)
            self._record_context_measure(turn.usage.context_tokens, cleared=cleared)
            self._fold_cache_probe(turn.usage)
            # The token budget's counter, accumulated where the provider's own numbers land. `total`
            # counts cache reads too (see `TokenUsage.total`), because the expensive loop is the one
            # that would hide from a count of fresh input only.
            self._turn_tokens += turn.usage.total
        else:
            # A provider that reports no usage would leave the budget a ceiling that silently does
            # not exist, the one thing this codebase refuses to ship. So the counter falls back to
            # the chars/4 estimator the window signal uses when nothing was measured. It is biased,
            # but a biased rail still stops a runaway and a missing rail stops nothing.
            self._turn_tokens += compaction.estimate_tokens(self._outbound_messages())

        # A step extended after the output limit has its fragment folded into the message it
        # continues, and the instruction that asked for it is dropped. History then holds one whole
        # assistant message and no synthetic instruction outlives the turn. The merged message
        # replaces both the append and the payload below, since the fragment is not a message of its
        # own.
        folded_message = (
            self._fold_continuation(turn) if (self._continued_message is not None and turn is not None) else None
        )
        self._turn_truncated = turn.finish_reason == "length"
        sanitize_mangled_calls(turn)
        # Another harness's parameter names, taken for this one's before the call enters the
        # transcript; `_log_call` records what was sent.
        for call in turn.tool_calls:
            sent = take_aliases(call.name, call.arguments, accepted_names(self.registry.get(call.name)))
            if sent:
                self._renamed_arguments[call.id] = sent
        timing = {
            "dispatched": step.dispatched_at,
            **({} if step.first_token_at is None else {"first_token": step.first_token_at}),
            "completed": time.time(),
        }
        self.log.append(
            "assistant/message",
            turn=self._turn_no,
            step=self._step_no,
            model=self.model,
            upstream=turn.upstream,
            text=turn.text or "",
            reasoning=turn.reasoning or None,
            finish_reason=turn.finish_reason,
            usage=turn.usage.as_dict() if turn.usage is not None else None,
            timing=timing,
            tool_calls=[{"call_id": call.id, "name": call.name} for call in turn.tool_calls] or None,
            # What the provider said of this request's prefix against the conversation's previous
            # one, so a cache miss is told apart: the harness changed it, or the provider
            # dropped an entry the request matched.
            cache_miss=turn.cache_miss,
            # What the provider cleared from this request before the model read it: the
            # model saw less than was sent, and `usage` counts what it saw.
            context_cleared=turn.context_cleared,
        )
        if turn.thinking_dropped:
            # The request went without some of the model's own thinking. The model saw
            # less than the transcript holds, so the log says how much and why.
            self.log.append(
                "thinking/dropped", turn=self._turn_no, step=self._step_no, model=self.model, **turn.thinking_dropped
            )
        if folded_message is not None:
            return turn, folded_message
        message = assistant_message(
            turn, model=self.model, context_breakdown=self._last_breakdown, timing=dict(timing)
        )
        self.messages.append(message)
        return turn, message

    def _assistant_event(self, turn: AssistantTurn, message: dict[str, Any]) -> Event:
        """The event a surface renders for the answer. It never reaches a provider."""
        payload: dict[str, Any] = {
            "text": message.get("content") or "",
            "tool_calls": [call.name for call in turn.tool_calls],
        }
        if message.get("reasoning"):
            payload["reasoning"] = message["reasoning"]
        if message.get("usage"):
            # The merged usage of every round trip this message took (a folded extension sums its
            # fragments), so the turn total the GUI accumulates still adds up to what the provider
            # reported.
            payload["usage"] = dict(message["usage"])
        # Where this session compacts, for the surface's context gauge. The catalogue window alone
        # read "381k of 1M · 38%" in a session whose cap (auto: 400k) made it compact at 380k, and the
        # owner took the 1M for the compaction point. It is for the surface only: this payload never
        # reaches a provider.
        payload["compact_at"] = self._history_trigger_tokens()
        return Event(EventType.ASSISTANT_MESSAGE, payload)

    async def _run_tool_calls(self, turn: AssistantTurn, state: LoopState) -> AsyncIterator[Event]:
        """Run the step's tool calls, then the nudge that rides after them. It sets `state.next` to
        "end" when the person stopped the turn meanwhile."""
        state.next = "go"
        # The calls of one message were written together, from one view. The read guard judges each
        # against that view and not against what an earlier call of the same message did to it.
        observed = getattr(self, "observed", None)
        if observed is not None:
            observed.calls_written()
        try:
            async for event in self._handle_tool_calls(turn.tool_calls):
                yield event
        finally:
            if observed is not None:
                observed.settle_view()
        # A fold one of those calls applied is told the way a compaction the loop ran
        # itself is: a saved notice with its numbers, and the event.
        folded = self._take_fold_notice()
        if folded is not None:
            self._append_notice("compacted", folded.text, **folded.numbers)
            yield Event(EventType.COMPACTED, {"text": folded.text, **folded.numbers})

        self._nudge_todo(turn)
        self._maybe_fold_hint()
        self._maybe_context_ledger()  # off unless configured

        self.log.append("step/end", turn=self._turn_no, step=self._step_no)
        yield Event(EventType.ITERATION_END, {"iteration": state.iterations})

        if self._cancel.is_set():
            self._append_notice("interrupted")
            yield Event(EventType.INTERRUPTED, {"iterations": state.iterations, **self._changes_payload()})
            state.next = "end"
            return
        if self._steering:
            self._inject_steering()

    def _nudge_todo(self, turn: AssistantTurn) -> None:
        """The todo nudge: a run making call after call with no task list in play gets one quiet
        system note before the next request. The prompt's request alone sits too far upstream to
        survive every model's habits, and the reminder lands at the moment the run has proven itself
        non-trivial. It is never repeated, never given after the agent has written a list, and never
        given where the capability does not exist.

        "The capability" is the tool, read from the registry at this moment. It used to be
        `self.todo`, the list store, which every managed engine has, a subagent's too, whose ruleset
        removes `todo_write`. In the real store (2026-10-04) 36 nudges fired inside child sessions,
        36 of 36 in children that were not offered the tool. None could act on it, and 33 then wrote
        about a to-do list in their reasoning."""
        self._turn_tool_calls += len(turn.tool_calls)
        if any(call.name == TODO_WRITE for call in turn.tool_calls):
            self._turn_saw_todo = True
        if (
            self.todo is not None
            and self.registry.get(TODO_WRITE) is not None
            and "todo_nudge" not in self.ablated
            and not self._turn_todo_nudged
            and not self._turn_saw_todo
            and self._turn_tool_calls >= TODO_NUDGE_AFTER
        ):
            self._turn_todo_nudged = True
            self.messages.append(
                {"role": "user", "content": TODO_NUDGE_TEXT, ATTACHED_BY: "todo_nudge", "ts": time.time()}
            )
            self.log.append(
                "todo/nudge", turn=self._turn_no, step=self._step_no, tool_calls=self._turn_tool_calls
            )
