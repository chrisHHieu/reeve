"""ProgramCallsMixin: `run_code` and `workflow`, the tools that run a program written by the model.

The program runs on its own thread and blocks on every tool call it makes. This loop is what
unblocks it. Each request becomes a real call, a synthetic `ToolCall` with a sub-call id, and goes
through `_authorize` and `_dispatch`, so the approval card, the permission engine, the pipeline
listeners and the audit all see it exactly as they see a call the model made directly. That
equivalence is the whole safety argument for the feature, and nothing here re-implements any of it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Optional

from harness.llm.providers import ToolCall
from harness.session.events import Event, EventType
from harness.tools.batch import StreamGate

from .message_shapes import log_text
from .result_recording import lift_sidecars


def _journalable(value: Any) -> bool:
    """Only a completed task result is worth resuming from. A refused or failed child must run again
    on resume and not be replayed as a failure."""
    if not isinstance(value, dict) or value.get("error"):
        return False
    return not str(value.get("status") or "").startswith("error")


class ProgramCallsMixin:
    async def _handle_workflow(self, tool_call: ToolCall) -> "AsyncIterator[Event]":
        """Run one workflow script written by the model (a prelude and a body) as a program.

        It has the runtime, the gate and the sub-call pipeline of `run_code`. What differs is what
        is in scope: the injected helpers (`agent()`, `parallel()`, `pipeline()`, `phase()`, `log()`
        and `args`). The meta block and the script are validated here, before the child starts, so a
        malformed identity block fails with one sentence and no agent is ever launched for it."""
        from harness.tools.workflow import (
            DEFAULT_CONCURRENCY,
            MAX_SCRIPT_CHARS,
            MAX_TOTAL_AGENTS,
            compose_script,
            validate_meta,
            validate_script,
        )
        from harness.tools.workflow_journal import WorkflowJournal, run_key

        arguments = tool_call.arguments or {}
        script = arguments.get("script")
        # `refused` names which check turned the call away, for the result's facts. The
        # model reads only the sentence.
        if not isinstance(script, str) or not script.strip():
            yield self._record_result(
                tool_call, {"error": "workflow needs a `script` argument", "refused": "script"}, "error"
            )
            return
        if len(script) > MAX_SCRIPT_CHARS:
            yield self._record_result(
                tool_call,
                {
                    "error": (
                        f"the workflow script is {len(script):,} characters; the limit is "
                        f"{MAX_SCRIPT_CHARS:,}"
                    ),
                    "refused": "size",
                },
                "error",
            )
            return
        problem = validate_meta(arguments.get("meta"))
        if problem:
            yield self._record_result(tool_call, {"error": problem, "refused": "meta"}, "error")
            return
        problem = validate_script(script)
        if problem:
            yield self._record_result(tool_call, {"error": problem, "refused": "script"}, "error")
            return
        args = arguments.get("args")
        if args is not None and not isinstance(args, (dict, list, str, int, float, bool)):
            yield self._record_result(
                tool_call, {"error": "workflow args must be JSON data", "refused": "args"}, "error"
            )
            return
        from .workflow_background import run_in_background, wants_background

        if wants_background(self, arguments):
            # A child session runs the approved call, and this turn goes on.
            async for event in run_in_background(self, tool_call):
                yield event
            return
        options = getattr(self, "workflow_options", None) or {}
        prelude, code = compose_script(
            script,
            args=args,
            max_agents=int(options.get("max_agents") or MAX_TOTAL_AGENTS),
            max_tokens=int(options.get("max_tokens") or 0),
            concurrency=int(options.get("concurrency") or DEFAULT_CONCURRENCY),
        )
        # The journal makes a dead fan-out resumable. A `resume` run loads the previous journal for
        # this same script and args and replays what it finds, and any other run starts fresh, so a
        # stale partial journal cannot leak into it.
        meta = arguments.get("meta") if isinstance(arguments.get("meta"), dict) else {}
        journal = WorkflowJournal.for_log(
            # A background run's journal sits beside the parent's log, so a `resume` finds
            # the agents of a run that went before, whichever runner ran it.
            getattr(self, "workflow_journal_log", None) or self.log,
            run_key(name=str(meta.get("name") or ""), script=script, args=args),
        )
        if journal is not None:
            if arguments.get("resume"):
                journal.load()
            else:
                journal.reset()
        async for event in self._handle_run_code(
            tool_call,
            code_override=code,
            prelude=prelude,
            restricted=True,
            program_timeout=float(options.get("timeout") or 0) or None,
            workflow=True,
            journal=journal,
        ):
            yield event

    async def _handle_run_code(
        self,
        tool_call: ToolCall,
        *,
        code_override: Optional[str] = None,
        prelude: Optional[str] = None,
        restricted: bool = False,
        program_timeout: Optional[float] = None,
        workflow: bool = False,
        journal: Optional[Any] = None,
    ) -> "AsyncIterator[Event]":
        """Run one program written by the model, brokering each tool call it makes.

        Sub-call results go in the event log and are not added to the message list. Putting them
        there would spend the context window this exists to save, and the log is where they are
        read."""
        from harness.tools.code_mode import DONE, CodeProgram, tool_signatures
        from harness.tools.workflow_journal import fingerprint

        arguments = tool_call.arguments or {}
        code = code_override if code_override is not None else str(arguments.get("code") or "")
        if not code.strip():
            yield self._record_result(tool_call, {"error": "run_code needs a `code` argument"}, "error")
            return

        # A program naming a tool this session does not hold is refused before it starts and before
        # a person is asked about it (and 0064's rule that a check answers before the
        # card). A workflow script reaches tools through its prelude.
        if not workflow:
            from harness.tools.code_mode import UNAVAILABLE
            from harness.tools.code_names import check_names

            held = set(self.registry.names())
            refusal, unknown = check_names(
                code,
                held - UNAVAILABLE,
                direct_only=held & UNAVAILABLE,
                searchable=bool(self.registry.progressive_discovery),
            )
            if refusal:
                self._log_call(tool_call)
                yield self._record_result(tool_call, {"error": refusal, "unknown_tools": unknown}, "error")
                return

        # The gate has to be here. This branch bypasses the caller's authorize-then-dispatch path,
        # so skipping it would make `run_code` the one exec tool nothing checked. `_authorize`
        # appends its own refusal message for the model.
        allowed = False
        async for item in self._authorize(tool_call):
            if isinstance(item, Event):
                yield item
            else:
                allowed = item
        if not allowed:
            return

        yield Event(EventType.TOOL_STARTED, {"name": tool_call.name, "call_id": tool_call.id})
        self._log_call(tool_call)
        self._audit(tool_call, stage="started")

        program = CodeProgram(
            code,
            names=frozenset(self.registry.names()),
            # The signatures ride the child's private channel and not the prompt: `run_code` names
            # no tools, and `tools.describe` is a local lookup.
            signatures=tool_signatures(self.registry.all_schemas()),
            # Only a workflow's prelude can narrate (`phase`, `log`), and only a workflow carries the
            # trusted prelude and the restricted-builtins body.
            notes=workflow,
            prelude=prelude,
            restricted=restricted,
            timeout=program_timeout,
            cwd=getattr(self, "workspace", None),
        )
        program.start()
        if workflow:
            meta = tool_call.arguments.get("meta")
            self.log.append(
                "workflow/run-start",
                turn=self._turn_no,
                step=self._step_no,
                root_call_id=tool_call.id,
                name=str((meta or {}).get("name") or "") if isinstance(meta, dict) else "",
            )
        deadline = time.monotonic() + program.timeout
        timed_out = False
        index = 0
        replayed_count = 0
        # Dispatch is concurrent: the child routes replies by id, so a program may have several
        # calls in flight at once (the workflow fan-out). Authorization stays serialized, with one
        # approval card at a time and one ordered event stream, while the tools themselves overlap.
        # Events from the per-call tasks funnel through this queue, because only this generator may
        # yield.
        events: "asyncio.Queue[Event]" = asyncio.Queue()
        auth_lock = asyncio.Lock()
        tasks: set[asyncio.Task] = set()
        # Which of the calls in flight may run together: the rule the direct path applies
        # to a batch (`plan_batch`), applied as calls arrive. Reads overlap, a write or shell call
        # runs alone, a conflict on a path waits, and order is kept.
        gate = StreamGate(cap=max(0, int(getattr(self, "max_parallel_tools", 0) or 0)))

        def log_dispatch(sub: ToolCall, *, is_error: bool, content: str, **more: Any) -> None:
            self.log.append(
                "tool/code-dispatch",
                turn=self._turn_no,
                step=self._step_no,
                root_call_id=tool_call.id,
                call_id=sub.id,
                name=sub.name,
                is_error=is_error,
                content=content,
                **more,
            )

        async def run_sub(sub: ToolCall, request: Any, index: int, fp: str, ticket: int) -> None:
            try:
                # A check's refusal skips the card and is answered below as the call's
                # outcome, exactly as the same refusal at dispatch would be.
                refused = self._refused_early(sub)
                allowed = refused is not None
                if refused is None:
                    async with auth_lock:
                        async for item in self._authorize(sub):
                            if isinstance(item, Event):
                                await events.put(item)
                            else:
                                allowed = item
                if not allowed:
                    # Raised inside the program and not ending it: a refused write is something the
                    # model can catch and work around, and killing the whole run would throw away
                    # everything the program had already done correctly.
                    request.answer("this tool call was not permitted", ok=False)
                    await events.put(self._sub_call_finished(sub, "this tool call was not permitted", "denied"))
                    log_dispatch(sub, is_error=True, content="not permitted")
                    return
                if refused is None:
                    await gate.acquire(ticket, self._reserve(sub))
                outcome = refused if refused is not None else await self._dispatch(sub)
                # The program gets the cleaned value, the same one the model and the presenter see on
                # the direct path. Without this lift, `_card_meta` rode back to the program and
                # duplicated a read's whole content a second time (the live report: a 470-line file
                # arrived twice), `_display` leaked user-facing filter metadata the model is not
                # supposed to see, and `_attach` had no surface inside a program at all.
                value, _, _, _ = lift_sidecars(outcome.value)
                request.answer(value, ok=outcome.ok)
                if journal is not None and sub.name == "task" and _journalable(value):
                    # The next `resume` run of this script gets this child's result back without
                    # paying for it again.
                    journal.put(index, fp, value)
                await events.put(self._sub_call_finished(sub, value, outcome.status))
                log_dispatch(sub, is_error=not outcome.ok, content=log_text(value))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a dispatch crash is an answer, not a lost program
                request.answer({"error": str(exc), "error_type": exc.__class__.__name__}, ok=False)
            finally:
                # Started or not, the call leaves the line, or every call behind it waits.
                await asyncio.shield(gate.release(ticket))
                tasks.discard(asyncio.current_task())

        def note_events() -> list[Event]:
            """Workflow narration that arrived since the last drain, as live rows."""
            drained: list[Event] = []
            for note in program.drain_notes():
                text = str(note.get("text") or "")
                if not text:
                    continue
                kind = str(note.get("kind") or "log")
                self.log.append(
                    "workflow/note",
                    turn=self._turn_no,
                    step=self._step_no,
                    root_call_id=tool_call.id,
                    kind=kind,
                    text=text,
                )
                drained.append(
                    Event(
                        EventType.TOOL_OUTPUT_DELTA,
                        {"call_id": tool_call.id, "text": f"▸ {text}\n" if kind == "phase" else f"  {text}\n"},
                    )
                )
            return drained

        done_reading = False
        while True:
            while not events.empty():
                yield events.get_nowait()
            for note_event in note_events():
                yield note_event
            if done_reading:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._cancel.is_set():
                timed_out = not self._cancel.is_set()
                program.stop()
                # The program is dead, and its in-flight `agent()` children must not keep working
                # for a run nobody will read. The host hook stops every child this call spawned,
                # through the interrupt tree Stop pulls.
                cancel_origin = getattr(self, "subagent_cancel_origin", None)
                if cancel_origin is not None:
                    try:
                        cancel_origin(tool_call.id)
                    except Exception:
                        pass
                # Anything in flight is answered into a closed pipe (harmless) and its events are
                # dropped: a timed-out run reports its printed output and not a partial event stream.
                for pending_task in list(tasks):
                    pending_task.cancel()
                done_reading = True
                continue
            # Off the loop: waiting for the next request must not block the loop that serves the
            # approval card the program may be waiting on.
            request = await asyncio.to_thread(program.next_request, min(remaining, 0.25))
            if request is DONE:
                done_reading = True
                continue
            if request is None:
                continue  # the wait expired, not the program
            index += 1
            sub = ToolCall(id=f"{tool_call.id}:code:{index}", name=request.name, arguments=request.arguments)
            # Replay comes first, before any dispatch record exists. A resumed call was approved and
            # audited when it ran, and answering it from the journal is the point of resuming.
            # `continue` skips the proposal row too, because a task that did not run must not appear
            # as one on the live stream.
            fp = fingerprint(sub.arguments)
            cached = journal.get(index, fp) if journal is not None and sub.name == "task" else None
            if cached is not None:
                request.answer(cached, ok=True)
                replayed_count += 1
                log_dispatch(
                    sub, is_error=False, content="replayed from the workflow journal", replayed=True
                )
                yield Event(
                    EventType.TOOL_OUTPUT_DELTA,
                    {"call_id": tool_call.id, "text": f"  replayed agent {index} from the previous run\n"},
                )
                continue
            self.log.append(
                "tool/code-dispatch-start",
                turn=self._turn_no,
                step=self._step_no,
                root_call_id=tool_call.id,
                call_id=sub.id,
                name=sub.name,
                arguments=log_text(sub.arguments),
            )
            yield Event(
                EventType.TOOL_PROPOSED,
                {"name": sub.name, "arguments": sub.arguments, "call_id": sub.id, "in_program": True},
            )
            task = asyncio.create_task(run_sub(sub, request, index, fp, gate.ticket()))
            tasks.add(task)
        # The program said it was done, but the last dispatched calls may still be answering and
        # logging. Their events have to reach the stream before the result.
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
        while not events.empty():
            yield events.get_nowait()
        for note_event in note_events():
            yield note_event

        result = program.result(timed_out=timed_out)
        if journal is not None:
            journal.end("error" if result.get("error") else "ok")
        # A run that died (a timeout, Stop, a dead child) is the case resume exists for. A script
        # that merely raised has nothing to continue from and gets no hint.
        died = (
            timed_out
            or self._cancel.is_set()
            or str(result.get("error") or "").startswith("the program ended without returning a result")
        )
        if workflow and journal is not None and died and result.get("error"):
            result["error"] = (
                str(result["error"])
                + " — call this workflow again with the same script and args plus "
                "resume=true to reuse the agent() calls that already finished"
            )
        if workflow:
            self.log.append(
                "workflow/run-end",
                turn=self._turn_no,
                step=self._step_no,
                root_call_id=tool_call.id,
                status="error" if result.get("error") else "ok",
                agents=result.get("tool_calls"),
                **({"replayed": replayed_count} if replayed_count else {}),
            )
        yield self._record_result(tool_call, result, "error" if result.get("error") else "ok")
