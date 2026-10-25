"""DispatchMixin: running one authorized tool call through the pipeline.

The pipeline wraps the call in its listeners, and a worker thread runs the tool body. A tool whose
output arrives over time publishes it with `output.emit()`, which reaches `_collect_tool_output`
through the context variable installed in `_dispatch`. The collector runs on the worker thread and
the drain runs on the turn's own, which yields `tool_output_delta` events. `_dispatch_streaming` is
the one place the two meet.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Optional

from harness.llm.providers import ToolCall
from harness.session.events import Event, EventType
from harness.tools import output
from harness.tools.pipeline import INTERRUPTED, ToolExecution, ToolResult

from .message_shapes import preview

_OUTPUT_POLL_SECONDS = 0.05


class DispatchMixin:
    def _collect_tool_output(self, call_id: str, text: str) -> None:
        with self._output_lock:
            self._output_chunks.append((call_id, text))

    def _drain_tool_output(self) -> list[Event]:
        with self._output_lock:
            chunks = list(self._output_chunks)
            self._output_chunks.clear()
        return [
            Event(EventType.TOOL_OUTPUT_DELTA, {"call_id": call_id, "text": text})
            for call_id, text in chunks
        ]

    async def _dispatch_streaming(self, tool_call: ToolCall) -> AsyncIterator[Any]:
        """Dispatch one call, yielding its live output as events and its result last.

        It has the contract of `_authorize`, and the caller tells the two apart with
        `isinstance(item, Event)`. The output is published from a thread, so there is no await to
        hang the forwarding on, and the wait for the call is broken into ticks that drain whatever
        arrived. 50 ms is under the eye's threshold for "streaming" and costs one wake-up per tick on
        a call that is blocking anyway.

        Only the sequential path streams. A parallel segment's calls run together, and interleaving
        their lines into one stream would attribute one command's output to another. Shell calls are
        exclusive by reservation, so the case with output to stream is sequential anyway."""
        task = asyncio.ensure_future(self._dispatch(tool_call))
        while True:
            done, _ = await asyncio.wait({task}, timeout=_OUTPUT_POLL_SECONDS)
            for event in self._drain_tool_output():
                yield event
            if done:
                break
        # This raises if the call did, exactly as awaiting `_dispatch` would.
        yield task.result()

    async def _dispatch(self, tool_call: ToolCall) -> ToolResult:
        """One approved call, through the pipeline. The hop to a thread is the terminal of the
        `execute` chain, so a listener registered there wraps the call itself.

        Stop resolves the await and does not stop the worker. `to_thread` runs the tool body on an
        executor thread and Python cannot cancel a thread. The turn stops waiting and the call keeps
        running to its own deadline, so the executor drains by itself and the person is not held
        hostage meanwhile. `interrupt_hooks` is the narrower and stronger tool: it kills the actual
        process, for the one case that owns one.

        Nothing here bounds a call. This docstring used to promise that "every tool with blocking
        work carries one of its own", which was true of shell, terminal, MCP, grep, git and the
        connectors and false of `tools/files.py`, whose reads block with no deadline and no contract.
        The budget now comes from `tools/deadline.py`, an `on_execute` listener that applies a ceiling
        to every call and defers to a tighter `ToolContract` budget where a tool declared one, so the
        guarantee is structural and not something everyone has to remember."""

        async def run() -> ToolResult:
            # The capture wraps the `ensure_future` and not just the call: `to_thread` copies the
            # context at the hop, so a context variable set inside this block is what the worker
            # thread sees when the tool emits (tools/output.py).
            with output.capture(tool_call.id, self._collect_tool_output):
                work = asyncio.ensure_future(asyncio.to_thread(self._execute_sync, tool_call))
            stop = asyncio.ensure_future(self._cancel.wait())
            try:
                done, _ = await asyncio.wait({work, stop}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                stop.cancel()
            # Decided on the flag and not on who won the race. A future can finish for reasons that
            # are not a stop, and reporting one of those as "the user stopped" would invent consent
            # that was never given.
            if work not in done and self._cancel.is_set():
                # Detach and do not cancel: cancelling the wrapper future would not stop the thread,
                # and an exception nobody retrieves would surface later as an unhandled-task warning
                # with no owner.
                work.add_done_callback(lambda future: future.exception())
                return ToolResult(
                    value={"error": "tool call interrupted", "reason": "user stop"}, status=INTERRUPTED
                )
            # `await` and not `.result()`: when the race ended for some other reason the tool is
            # still running, and its answer is still the right one.
            value, status = await work
            return ToolResult(value=value, status=status)

        # Timed here and nowhere else, since this is the only point that brackets the call itself. A
        # `ts` delta would charge the tool for the approval it waited on and, when calls run
        # concurrently, for its siblings too, so the number people read as "how slow is this tool"
        # would be mostly other things.
        started_at = time.time()
        try:
            result = await self.pipeline.dispatch(self._execution(tool_call), run)
        finally:
            self._call_timing[tool_call.id] = {"started": started_at, "ended": time.time()}
        # A design page just written is opened and measured before its result is recorded
        # (`design_checks.py`). After the timing above, so the write's own time is the
        # write's and not the browser's.
        await self._check_written_designs(tool_call, result.status)
        # The post_tool_use and post_tool_use_failure hooks: the person's script sees what happened
        # and may add context ("formatted the file", "the linter found two"). It cannot deny anything
        # because the call is over, and a failing hook is logged and ignored like every other hook.
        event = "post_tool_use" if result.status == "ok" else "post_tool_use_failure"
        hook = await self._run_hooks(
            event,
            tool=tool_call.name,
            arguments=tool_call.arguments,
            status=result.status,
            call_id=tool_call.id,
        )
        if hook is not None:
            if hook.context:
                self.messages.append(self._hook_message(event, hook.context))
            for error in hook.errors:
                self._append_notice("info", f"a {event} hook failed: {error}")
        return result

    def _execution(self, tool_call: ToolCall) -> ToolExecution:
        return ToolExecution(
            name=tool_call.name,
            arguments=tool_call.arguments,
            call_id=tool_call.id,
            contract=self.registry.contract(tool_call.name),
        )

    def _refused_early(self, tool_call: ToolCall) -> Optional[ToolResult]:
        """The BLOCKED result a pipeline check gives this call before anyone is asked to approve it,
        or None.

        A check refuses whatever a person decides, so asking first bought nothing: a person allowed
        an overwrite of a file the model had never read, and the read-before-edit gate then refused it
        anyway (found live, 2026-09-26). The check is asked again at dispatch, where a file that
        changed while the card waited is caught."""
        return self.pipeline.refusal(self._execution(tool_call))

    def _execute_sync(self, tool_call: ToolCall) -> tuple[Any, str]:
        """Execute one authorized call. It runs in a worker thread."""
        try:
            return self.registry.execute(tool_call.name, tool_call.arguments), "ok"
        except Exception as exc:
            return {"error": str(exc), "error_type": type(exc).__name__}, "error"

    def _sub_call_finished(self, sub: ToolCall, value: Any, status: str) -> Event:
        """Tell the surface that a program's sub-call is over.

        `_record_result` cannot be used here, which is why this exists: it appends a tool message to
        `self.messages`, and a sub-call result in the message list would spend exactly the context
        window `run_code` exists to save. This is the event and nothing else.

        Without it a sub-call was announced (TOOL_PROPOSED) and never answered, so every row a
        program opened spun forever, and the turn itself read as still running because a surface
        infers that from its unfinished steps. A reload "fixed" it only because sub-calls are absent
        from the message list the replay is built from, so the phantom rows did not come back."""
        return Event(
            EventType.TOOL_FINISHED,
            {
                "name": sub.name,
                "status": status,
                "call_id": sub.id,
                "result_preview": preview(value),
                "in_program": True,
            },
        )
