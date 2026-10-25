"""TurnLimitsMixin: what ends a turn early, and what is done about it.

A turn can run out of steps, tokens or patience (a repeated call, a failing check), or be cut by the
provider at the output-token limit. The engine names which, asks the model for a handoff, and asks
for the rest of a cut answer.
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator, Optional

from harness.llm.providers import AssistantTurn
from harness.session.events import Event, EventType
from harness.session.turns import ATTACHED_BY, TurnEndReason

from .limits import TRUNCATION_CONTINUES
from .message_shapes import assistant_message
from .nudges import CHECKPOINT_PROMPT, TRUNCATION_CONTINUE_TEXT
from .output_guard import repetition_dominated


class TurnLimitsMixin:
    def _iteration_cap_reached(self, iterations: int) -> bool:
        """True only when an agent (or the global fallback) set a positive step cap and this turn has
        reached it. `None` or 0 means no cap: the turn runs until the model stops, the person aborts,
        or the repeat guard trips."""
        cap = self.max_iterations
        return cap is not None and cap > 0 and iterations >= cap

    def _turn_stop_reason(self, iterations: int) -> TurnEndReason:
        """Which rail ended a turn that ran out of room.

        Only one can be named, so the order is that of the `or` chain where the turn is stopped:
        the iteration cap, then a repeat loop, then the check loop, then the budget. They
        are not exclusive, since a runaway trips all of them in the end, and naming the first one is
        honest and not lossy: whichever it is, the checkpoint carries what a reader needs (what was
        done and what remains)."""
        if self._iteration_cap_reached(iterations):
            return TurnEndReason.MAX_ITERATIONS
        if self.repeats.exhausted:
            return TurnEndReason.REPETITION_STOPPED
        if self._check_loop_stopped():
            return TurnEndReason.NO_PROGRESS
        return TurnEndReason.TOKEN_BUDGET

    # -- tokens ------------------------------------------------------------------------------

    def carry_delegated(self, run_id: str, tokens: int) -> None:
        """A background child's spend, for the parent turn that receives its result (O7). The
        manager calls it when it injects the completion, and `_fold_carried_tokens` folds it in at
        the next step. It is a list append, so a call from another thread is safe."""
        if tokens > 0:
            self._carried_tokens.append((str(run_id), int(tokens)))

    def _fold_carried_tokens(self) -> None:
        """Charge carried background spend to this turn, one logged row per child, since the
        `tokens` in every `turn/end` total must be attributable."""
        carried, self._carried_tokens = self._carried_tokens, []
        for run_id, tokens in carried:
            self._turn_tokens += tokens
            self.log.append(
                "delegation/background",
                turn=self._turn_no,
                step=self._step_no,
                run_id=run_id,
                tokens=tokens,
            )

    def _token_budget_spent(self) -> bool:
        """Whether this turn has spent its token budget. 0 means no ceiling."""
        return self.token_budget > 0 and self._turn_tokens >= self.token_budget

    # -- the handoff -------------------------------------------------------------------------

    async def _write_checkpoint(self) -> AsyncIterator[Event]:
        """The graceful stop's final call: one tool-less call, with no schema advertised so a tool
        call cannot even be typed, asking the model for whatever the caller needs from this turn.

        A session asks for a handoff (`CHECKPOINT_PROMPT`: done, remaining, next action), so the next
        "continue" resumes from it. A subagent overrides `cap_prompt` to ask for its report instead,
        because a child that answers with a handoff gives its parent a status update where the
        findings should be (owner report, 2026-09-16). A failure here is silent: the caller's
        TURN_END payload still says what happened, and the prompt that was added is removed so no
        dangling instruction outlives the turn."""
        if self._cancel.is_set():
            return
        self.messages.append(
            {
                "role": "user",
                "content": self.cap_prompt or CHECKPOINT_PROMPT,
                ATTACHED_BY: "checkpoint",
                "ts": time.time(),
            }
        )
        self.log.append("turn/checkpoint", turn=self._turn_no, step=self._step_no)
        streamed: list[str] = []
        final_turn: Optional[AssistantTurn] = None
        try:
            async for chunk in self._astream(with_tools=False):
                if chunk.text_delta:
                    streamed.append(chunk.text_delta)
                    yield Event(
                        EventType.ASSISTANT_DELTA, {"index": 0, "kind": "text", "text": chunk.text_delta}
                    )
                if chunk.turn is not None:
                    final_turn = chunk.turn
        except Exception:
            self.messages.pop()  # the prompt must not outlive a failed handoff
            return
        text = "".join(streamed).strip()
        if not text and final_turn is not None:
            # A provider that answers in one final chunk carries the text on the turn and not as
            # deltas, as the scripted provider in tests does.
            text = (final_turn.text or "").strip()
        if not text:
            self.messages.pop()
            return
        self.messages.append({"role": "assistant", "content": text, "ts": time.time()})
        self.log.append(
            "assistant/message",
            turn=self._turn_no,
            step=self._step_no,
            model=self.model,
            text=text,
            finish_reason=(final_turn.finish_reason if final_turn else "stop"),
            checkpoint=True,
        )
        yield Event(EventType.ASSISTANT_MESSAGE, {"text": text, "tool_calls": [], "checkpoint": True})

    # -- a message cut at the output limit ---------------------------------------------------

    def _maybe_continue_truncated(self, turn: Any) -> bool:
        """Ask for the rest of an answer the provider cut at the output-token limit. True when the
        loop should go around again for the same assistant message.

        Only a text answer is extended. A cut tool call is a different failure (its arguments never
        parsed, and `_mangled_tool` answers it with the diagnosis), and a complete tool call beside a
        cut text is a step with work to do, which the normal path already handles.

        The instruction is logged before it is sent (model-visible means logged, 0001) and tagged so
        it cannot outlive the turn: `_fold_continuation` removes it and merges the fragment into the
        message it continues. It is bounded by `TRUNCATION_CONTINUES`, and past that the honest
        report is a truncated turn and not a fourth request."""
        if self._cancel.is_set() or self._continued_message is not None:
            return False
        if (turn.finish_reason or "") != "length":
            return False
        if not (turn.text or "").strip():
            return False
        if repetition_dominated(turn.text or ""):
            # Extending a loop gives a longer loop. The honest end is the truncated answer already
            # streamed, and not a request that buys more of the same. Nothing is sent, so there is no
            # model-visible message to log, and the row records why the extension did not happen.
            self.log.append(
                "turn/continuation",
                turn=self._turn_no,
                step=self._step_no,
                attempt=self._truncation_continues,
                skipped="repetition_dominated",
                chars=len(turn.text or ""),
            )
            return False
        if self._truncation_continues >= TRUNCATION_CONTINUES:
            return False
        last = self.messages[-1] if self.messages else None
        if not last or last.get("role") != "assistant":
            return False  # only the message just appended is ever extended
        self._continued_message = last
        self._truncation_continues += 1
        self._attach_note(TRUNCATION_CONTINUE_TEXT, "truncation-continue")
        self.log.append(
            "steering/message",
            turn=self._turn_no,
            step=self._step_no,
            text=TRUNCATION_CONTINUE_TEXT,
            source="truncation-continue",
        )
        return True

    def _fold_continuation(self, turn: Any) -> Optional[dict[str, Any]]:
        """Merge a continuation fragment into the message it continues, and return that message.

        History keeps one assistant message with whole text. The instruction that asked for the
        fragment is deleted, so nothing synthetic outlives the turn. The usage of every round trip in
        the extension is summed and not replaced, so the message's own token accounting still adds up.
        Reasoning is joined the same way, and a fragment carrying parsed tool calls adds them to the
        same message, because the calls and their results must sit in one assistant message or a
        provider sees tool results for a call it never received.

        The fragment's arrival is logged as `turn/continuation`, the pair of the `steering/message`
        written when the extension was requested. It returns None only when there was nothing to
        fold, which would be a caller bug and not a wire case."""
        target, self._continued_message = self._continued_message, None
        if target is None:
            return None
        before = len(target.get("content") or "")
        target["content"] = (target.get("content") or "") + (turn.text or "")
        if turn.reasoning:
            target["reasoning"] = (target.get("reasoning") or "") + turn.reasoning
        if turn.tool_calls:
            added = assistant_message(turn).get("tool_calls") or []
            target["tool_calls"] = [*(target.get("tool_calls") or []), *added]
        if turn.usage is not None:
            merged = dict(target.get("usage") or {})
            for key, value in turn.usage.as_dict().items():
                merged[key] = int(merged.get(key) or 0) + int(value or 0)
            merged["model"] = merged.get("model") or self.model
            target["usage"] = merged
        for index in range(len(self.messages) - 1, -1, -1):
            if self.messages[index].get(ATTACHED_BY) == "truncation-continue":
                del self.messages[index]
                break
        self.log.append(
            "turn/continuation",
            turn=self._turn_no,
            step=self._step_no,
            attempt=self._truncation_continues,
            added=len(target.get("content") or "") - before,
        )
        return target
