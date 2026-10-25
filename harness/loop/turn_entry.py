"""TurnEntryMixin: the three ways a turn starts, `run`, `retry` and `resume`."""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Optional

from harness.config.roots import RootDir
from harness.llm.providers import ToolCall
from harness.session.events import Event, EventType
from harness.session.turns import TurnEndReason, is_user_turn

from .message_shapes import log_text


class TurnEntryMixin:
    async def run(
        self,
        user_input: "str | list",
        *,
        source: Optional[dict[str, Any]] = None,
        display: Optional[str] = None,
        mentions: Optional[list[str]] = None,
        attached: Optional[list[dict[str, Any]]] = None,
        command: Optional[dict[str, str]] = None,
    ) -> AsyncIterator[Event]:
        """Take one user message and run the turn it opens.

        `user_input` is a string, or OpenAI content parts (text and image_url) for attachments.
        `source` (a MessageSource dict) is a display-only sidecar for connector messages. It rides on
        the saved user message and the TURN_START event and is stripped before the message reaches a
        provider (see `_outbound_messages`), while `content` stays the framed text. `display` is the
        same split for a skill the person forces: their literal "/skill …" line
        for the transcript, with `content` carrying the model-facing framing. `ts` (unix seconds,
        stamped on every appended message) is a sidecar of the same kind.

        `mentions` are the @agent names the person invoked this turn. They are not a sidecar: the
        model-facing framing already carries the synthetic instruction (the server appended it), and
        the names exist so the `task` tool can skip the spawn ask. They are turn-scoped state on the
        engine, read by the tool per call, and set after `_begin_turn_controls` below, which clears
        the previous turn's set.

        `attached` is one row per attachment as the transcript draws it (kind, name, size, the path
        of the kept copy). Another display-only sidecar: the model reads the attachments
        in `content`, and a reopened conversation draws their cards from the rows.

        `command` is the slash command the message was sent with (name, what was typed after it, a
        line about it), the same kind of sidecar: `content` is the expanded template the
        model reads, and the transcript draws the command."""
        message: dict[str, Any] = {"role": "user", "content": user_input, "ts": time.time()}
        if source is not None:
            message["source"] = source
        if display is not None:
            message["_display"] = display
        if attached:
            message["_attached"] = attached
        if command:
            message["_command"] = command
        self.messages.append(message)
        # The live-context channel follows the same rule at the same moment: it is read once per
        # turn and written into the message, so the request that carries it and every request after
        # it are byte appends and not rewrites (see `_stamp_live_context`). The AGENTS.md files of
        # directories read since the last turn are found during the turn and added here, so the
        # request stays an append. The context-awareness line is added when the reading has moved
        # into a new band (see `_stamp_context_budget`).
        self._stamp_live_context(message)
        self._stamp_nested_agents(message)
        self._stamp_context_budget(message)
        self._stamp_fold_hint(message)
        self._begin_turn_controls()
        self._turn_stop_hooked = False
        self.turn_mentions = [str(name) for name in (mentions or []) if str(name).strip()]
        # Which goal round this is, for the `goal` tool's completion authority. It is read after the
        # message is on the thread and after `_begin_turn_controls` cleared it.
        self.goal_round = self._goal_round_of_last_turn()
        # Who opened it: no source means a person.
        self.person_turn = source is None
        if self.session_facts is not None:
            self.session_facts.begin_turn()
        data: dict[str, Any] = {"input": user_input}
        if source is not None:
            data["source"] = source
        if display is not None:
            data["display"] = display
        if attached:
            data["attached"] = attached
        if command:
            data["command"] = command
        self._turn_no += 1
        self._step_no = 0
        self.log.append("turn/start", turn=self._turn_no)
        self.log.append(
            "user/message", turn=self._turn_no, content=log_text(user_input), source=source, display=display
        )
        stamp, self._budget_stamp = getattr(self, "_budget_stamp", None), None
        if stamp:
            self.log.append("context/budget", turn=self._turn_no, **stamp)
        fold_stamp, self._fold_stamp = self._fold_stamp, None
        if fold_stamp:
            self.log.append("context/budget", turn=self._turn_no, **fold_stamp)
        try:
            yield Event(EventType.TURN_START, data)
            refusal = await self._run_entry_hooks(user_input)
            if refusal is not None:
                yield refusal
                return
            async for event in self._enter_loop():
                yield event
        finally:
            # An abandoned turn at any yield above, including the TURN_START one, before the loop
            # even exists, still ends the turn. `_force_close_turn` does nothing when the loop has
            # already closed it with its own, more specific reason.
            self._force_close_turn(TurnEndReason.INTERRUPTED)

    async def _run_entry_hooks(self, user_input: "str | list") -> Optional[Event]:
        """Run `session_start` (once per engine) and `user_prompt_submit`. Their context rides as a
        logged steering message (model-visible means logged), and a `deny` refuses the turn outright,
        before any provider call. It returns the error event when the turn was refused, else None."""
        if not self._session_started:
            self._session_started = True
            started = await self._run_hooks("session_start", model=self.model)
            if started is not None:
                if started.context:
                    self.messages.append(self._hook_message("session_start", started.context))
                if started.errors:
                    self._append_notice("info", "a session_start hook failed: " + "; ".join(started.errors))
        submitted = await self._run_hooks(
            "user_prompt_submit",
            text=user_input if isinstance(user_input, str) else "[attachments]",
            mentions=list(self.turn_mentions) or None,
        )
        if submitted is not None:
            if submitted.context:
                self.messages.append(self._hook_message("user_prompt_submit", submitted.context))
            if submitted.denied:
                reason = submitted.reason or "a hook refused this message"
                self._append_notice("error", reason)
                self.log.append("step/end", turn=self._turn_no, step=self._step_no)
                self._log_turn_end(TurnEndReason.ERROR, error="hook_denied")
                return Event(EventType.ERROR, {"error": reason, "error_type": "HookDenied"})
            for error in submitted.errors:
                self._append_notice("info", f"a user_prompt_submit hook failed: {error}")
        # A workspace's hooks that are not running (an untrusted declaration) say so once. Silence
        # would look like a hook that never fired.
        runner = self._hook_runner()
        if runner is not None and runner.note and not self._hooks_noted:
            self._hooks_noted = True
            self._append_notice("info", runner.note)
        return None

    def _make_scratch(self) -> None:
        """Make the session's scratch folders, now that a turn will use them.

        A session is built with its scratch folder named and not created: the GUI builds one the
        moment a conversation opens, and creating the folder there left one behind for every
        conversation opened and never sent (879 in one person's scratch area, 2026-10-18). Every
        way a turn starts (run, retry, resume; the server, `reeve exec`, an eval) passes here."""
        if getattr(getattr(self, "world", None), "is_remote", False):
            return
        for root in getattr(self.permissions, "roots", None) or []:
            if isinstance(root, RootDir) and root.writable and root.label == "scratch":
                try:
                    root.path.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass  # the turn says so when a tool cannot write there

    async def _enter_loop(self) -> AsyncIterator[Event]:
        self._make_scratch()
        loop = self._loop()
        try:
            async for event in loop:
                yield event
        finally:
            # The consumer may walk away mid-turn (the socket gone, the task cancelled). The loop
            # generator is closed here, so its finalizer writes the turn's `turn/end` right away and
            # not whenever garbage collection finalizes the frame.
            await loop.aclose()

    async def retry(self) -> AsyncIterator[Event]:
        """Run the model loop again after a provider error. There is no new user message, because
        the failed turn's input is already the tail of history. It is guarded on the tail being an
        error notice, so a stray retry frame cannot answer a completed turn again. Trailing
        model_switch notices do not break the guard: switching models and then retrying is the
        intended way to recover (hit by the owner, 2026-07-23)."""
        if not self._tail_is_retriable_error():
            return
        async for event in self._run_again(""):
            yield event

    async def _run_again(self, label: str) -> AsyncIterator[Event]:
        """Run the model loop on the history as it stands, with no new message: the tail is
        already the turn's input. A retried or picked-up goal round keeps its round binding,
        since it is the same turn, so the `goal` tool's completion authority is the same one."""
        self._begin_turn_controls()
        self.goal_round = self._goal_round_of_last_turn()
        try:
            yield Event(EventType.TURN_START, {"input": label})
            async for event in self._enter_loop():
                yield event
        finally:
            self._force_close_turn(TurnEndReason.INTERRUPTED)

    async def resume(self) -> AsyncIterator[Event]:
        """Continue a turn that was suspended at a prompt and saved, which is how a restart (or the
        eviction of an engine) is survived. The unanswered tool calls of the trailing assistant
        message are processed again. The prompt callbacks find the already-resolved inbox item and
        return without asking again, and answered calls are skipped, so nothing executes twice. Then
        the model loop runs to finish the turn."""
        pending = self._unanswered_trailing_tool_calls()
        if not pending:
            return
        self._begin_turn_controls()
        self.goal_round = self._goal_round_of_last_turn()
        # An unanswered call is not the same as a call that never ran (item 2.2). Anything that left
        # a durable intent note got as far as starting, and repeating it is not ours to decide:
        # `_authorize` turns these into a person's call. This is set after `_begin_turn_controls`,
        # which clears the set.
        if self.intents is not None:
            self._uncertain_calls = {call.id for call in pending if call.id and self.intents.pending(call.id)}
        try:
            yield Event(EventType.TURN_START, {"input": "(resumed)"})
            async for event in self._handle_tool_calls(pending):
                yield event
            yield Event(EventType.ITERATION_END, {"iteration": 0})
            if not self._cancel.is_set():
                async for event in self._enter_loop():
                    yield event
        finally:
            self._force_close_turn(TurnEndReason.INTERRUPTED)

    async def pick_up(self, continuation: str, source: dict[str, Any], note: str = "") -> AsyncIterator[Event]:
        """Continue a turn the last process did not finish, from what the saved thread
        shows. `note` says what the restart stopped, for the model.

        - Calls with no result: `resume`, which never repeats one that had already started. The
          note follows the calls' results at the next step.
        - A message nobody answered (the person's, or one the engine sent): it is still the turn's
          input, so the loop runs on it where it stands. It is neither taken out (the transcript
          is append-only, and a message taken out and put back is stored twice) nor joined by a
          continuation (two messages in a row from one side are refused by some providers); the
          stopped commands are on the restart line, and the running-jobs line no longer lists them.
        - Otherwise the turn stopped between steps, and `continuation` (with the note) opens the
          next one; its `source` is what a surface draws instead of a bubble.
        """
        if self._unanswered_trailing_tool_calls():
            if note:
                self.queue_steering(note)
            self._append_notice("restart", "Reeve restarted · picked up where it left off", **{
                key: value for key, value in source.items() if key != "synthetic"
            })
            async for event in self.resume():
                yield event
            return
        tail = next((m for m in reversed(self.messages) if m.get("role") != "notice"), None)
        if tail is not None and tail.get("role") == "user":
            self._append_notice("restart", "Reeve restarted · picked up where it left off", **{
                key: value for key, value in source.items() if key != "synthetic"
            })
            async for event in self._run_again("(picked up)"):
                yield event
            return
        async for event in self.run(continuation + (f"\n{note}" if note else ""), source=source):
            yield event

    def _unanswered_trailing_tool_calls(self) -> list[ToolCall]:
        """The tool calls of the last assistant message that have no tool result yet, which is the
        prompt we suspended on and any after it, rebuilt from the saved thread."""
        answered = {message.get("tool_call_id") for message in self.messages if message.get("role") == "tool"}
        for message in reversed(self.messages):
            if is_user_turn(message):
                return []
            if message.get("role") == "assistant" and message.get("tool_calls"):
                pending: list[ToolCall] = []
                for call in message["tool_calls"]:
                    if call.get("id") in answered:
                        continue
                    function = call.get("function") or {}
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except Exception:
                        arguments = {}
                    pending.append(ToolCall(id=call.get("id"), name=function.get("name"), arguments=arguments))
                return pending
        return []
