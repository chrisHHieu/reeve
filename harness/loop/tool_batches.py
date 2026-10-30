"""ToolBatchMixin: running the tool calls of one assistant turn.

All the calls are screened and authorized first, one after another, because approval prompts are
interactive. The ones cleared then run in ordered segments: calls that cannot conflict overlap, and
everything else runs one at a time in the order the model wrote it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from aisuite.agents import ToolMetadata

from harness.llm.providers import ToolCall
from harness.safety.permissions import read_paths, write_paths
from harness.safety.risk import RiskClass, classify
from harness.session.events import Event, EventType
from harness.tools.batch import Reservation, plan_batch
from harness.tools.discovery import TOOL_CALL, rewrite_bridge_call
from harness.tools.contract import safe_present_call
from harness.tools.presentation import LOOKING_KINDS
from harness.tools.pipeline import BLOCKED, ToolResult

from .code_changes import CODE_WRITE_TOOLS
from .message_shapes import is_mangled, tool_error_message

logger = logging.getLogger(__name__)

# How many written paths one call reports to the language servers. A patch can touch many files,
# and the servers only need to hear about the ones a person would look at next.
_LSP_NOTIFY_MAX_FILES = 20

# Tools the engine answers itself. Each is either interactive, where the person's answer outside the
# turn is the consent and so skips the permission path, or `run_code` and `workflow`.
#
# `run_code` is here for one reason: its sub-calls have to travel this generator, because
# authorization yields events (the approval card among them) and a tool body on a worker thread
# cannot put an event on the turn's stream. It is not here for the reason the interactive ones are.
# `run_code` is EXEC and authorizes itself first, inside its handler. Sitting in this group without
# doing so ran a program in plan mode, which its own test caught.
_OWN_HANDLERS = {
    "request_tool": "_handle_tool_request",
    "propose_plan": "_handle_plan_proposal",
    "update_plan": "_handle_update_plan",
    "ask_user": "_handle_ask_user",
    "run_code": "_handle_run_code",
    "workflow": "_handle_workflow",
}

_MANGLED_TRUNCATED = (
    "your tool-call arguments were cut off by the output-token limit before they finished "
    "streaming — the tool never received them. Produce the same content in smaller pieces: "
    "several calls that each write or append a section, keeping each call's content well under "
    "the limit. Do not retry the identical oversized call."
)
_MANGLED_UNPARSED = (
    "your tool-call arguments did not parse as a JSON object, so the tool received nothing. "
    "`_raw` is not a parameter — it is the unparsed text of the failed call. Re-issue the call "
    "using the tool's declared parameters."
)


class ToolBatchMixin:
    # -- the batch ---------------------------------------------------------------------------

    async def _handle_tool_calls(self, tool_calls: list[ToolCall]) -> AsyncIterator[Event]:
        """Run one assistant turn's tool calls: authorize all of them first, then execute."""
        # One batch has one output budget (`STEP_OUTPUT_BUDGET`). What earlier steps spent is
        # history by now, which compaction bounds.
        self._step_output_chars = 0
        cleared: list[ToolCall] = []
        for tool_call in tool_calls:
            async for item in self._screen_call(tool_call):
                if isinstance(item, Event):
                    yield item
                elif item:
                    cleared.append(tool_call)
        async for event in self._execute_cleared(cleared):
            yield event

    async def _screen_call(self, tool_call: ToolCall) -> AsyncIterator[Any]:
        """Everything a call goes through before it may run. It yields events, and its last item is
        a bool: True when the call is cleared to execute, False when it was answered here."""
        if self._cancel.is_set():
            # Stopped: every call still left gets an answer, so none is orphaned.
            yield self._interrupted_tool(tool_call)
            yield False
            return
        # Progressive discovery: a `tool_call` bridge call is unwrapped before anything
        # observes it, so the propose event, the approval card, the audit and dispatch all see the
        # tool the model actually chose. The assistant message was serialized from the model's own
        # turn before this ran, so history keeps what the model called while execution sees the real
        # tool.
        #
        # It is noted before the unwrap renames the call, because a bridged call is exactly the
        # legitimate way to reach a deferred tool, and the direct-call refusal below must not see it.
        # It did, from progressive discovery's first day: every `tool_call` was refused as "behind progressive
        # discovery", and the pinning test passed because it asserted "hi" in a refusal text that
        # contains "this" (found by the live DeepSeek A/B, 2026-09-24).
        bridged = tool_call.name == TOOL_CALL
        bridge_error = rewrite_bridge_call(self.registry, tool_call)
        if bridge_error is not None:
            self._log_unstarted(tool_call)
            yield self._record_result(tool_call, {"error": bridge_error}, "error")
            yield False
            return
        if bridged:
            # A tool reached through the bridge is one the session uses. It loads, so the next call
            # is by its own name.
            self.registry.load(tool_call.name)
        # What a person reads about what is about to happen is computed once and handed to both
        # consumers, the propose event (a running row needs the command and its working directory
        # before anything has run) and the approval card below. Two computations would be two things
        # to keep in step, and free to drift as soon as a presenter reads more than its arguments.
        shown = safe_present_call(self.registry.contract(tool_call.name), tool_call.name, tool_call.arguments)
        # An overwrite's card shows the real diff from the start, and not the whole file as added,
        # and then the smaller real diff once it ran (GUI UX audit U3).
        diffs = self.changes.preview(tool_call.name, tool_call.arguments) if self.changes is not None else []
        if diffs and shown is not None and (shown.card or {}).get("card") == "diff":
            shown = dataclasses.replace(shown, card={**shown.card, "diffs": diffs})
        yield Event(
            EventType.TOOL_PROPOSED,
            {
                "name": tool_call.name,
                "arguments": tool_call.arguments,
                # The id the matching TOOL_FINISHED carries. Without it a surface can pair the two
                # only by tool name, and read-only calls run concurrently, so four `query_database`
                # calls in flight would land their results on each other's rows.
                "call_id": tool_call.id,
                **({"presentation": shown.as_dict()} if shown else {}),
                **({"looks": True} if self._call_looks(tool_call, shown) else {}),
            },
        )
        self._audit(tool_call, stage="proposed")
        if is_mangled(tool_call):
            # The arguments never parsed as JSON (a `{"_raw": …}` fallback from the provider).
            # Executing would give a bare parameter error that the model misreads, seen in the field
            # as an endless "wrong parameter" loop. It is answered with the actual diagnosis.
            yield self._mangled_tool(tool_call)
            yield False
            return
        refusal = None if bridged else self._direct_call_refusal(tool_call.name)
        if refusal is not None:
            # The code-only rule, enforced. A tool result and not an exception keeps the
            # call/result pairing the provider requires, and tells the model how to reach the tool
            # it wanted.
            self._log_unstarted(tool_call)
            yield self._record_result(tool_call, {"error": refusal}, "error")
            yield False
            return
        own = _OWN_HANDLERS.get(tool_call.name)
        if own is not None:
            async for event in getattr(self, own)(tool_call):
                yield event
            yield False
            return
        # Arguments that cannot bind to the tool fail here, before a person is asked to approve a
        # call that was going to raise, and with the tool's real parameter list in place of a Python
        # TypeError (`ToolRegistry.argument_problem`). This comes after the handlers above, which
        # deal with their own arguments.
        problem = self.registry.argument_problem(tool_call.name, tool_call.arguments)
        if problem is not None:
            self._log_unstarted(tool_call)
            yield self._record_result(tool_call, {"error": problem}, "error")
            yield False
            return
        # A call a pipeline check refuses is refused whatever a person says, so it is answered
        # before the hooks, the policy and the approval card.
        refused = self._refused_early(tool_call)
        if refused is not None:
            self._log_unstarted(tool_call)
            yield self._record_result(tool_call, refused.value, refused.status)
            yield False
            return
        allowed = False
        async for item in self._authorize(tool_call, shown):
            if isinstance(item, Event):
                yield item
            else:
                allowed = item
        yield allowed

    async def _execute_cleared(self, cleared: list[ToolCall]) -> AsyncIterator[Event]:
        # The batch runs in ordered segments (`tools/batch.py`). The old shape ran every
        # parallel-safe call to completion and then everything else, which permuted a batch the
        # model had written as a sequence. `[write_file(a), read_file(a)]`, the ordinary "write it
        # and read it back" pair, ran as read-then-write, so the model was handed the file's old
        # contents and the only honest reading was "my write did not land". Segments keep the order,
        # let non-conflicting low-risk calls overlap, and close a run when a call reserves a path
        # another call in the run touches (see `Reservation`).
        #
        # Attachments are queued for the whole batch and added after the last result, so the
        # tool-result run stays contiguous, which providers require (see `order_tool_responses`).
        # The `finally` flushes even when the consumer walks away mid-batch, so a durable resume
        # never finds a result whose attachment was lost.
        self._attach_queue = []
        try:
            for segment in plan_batch(cleared, reserve=self._reserve):
                if segment.parallel and len(segment.calls) > 1:
                    async for event in self._run_parallel(segment.calls):
                        yield event
                else:
                    async for event in self._run_in_order(segment.calls):
                        yield event
        finally:
            queued, self._attach_queue = self._attach_queue, None
            if queued:
                self.messages.extend(queued)

    async def _run_parallel(self, calls: list[ToolCall]) -> AsyncIterator[Event]:
        # A bounded pool: chunks of at most `max_parallel_tools` overlap. Chunking changes only how
        # many run at once. The segment's order, its result order and the call-before-result
        # invariant are untouched.
        for chunk in self._parallel_chunks(calls):
            for tool_call in chunk:
                yield Event(EventType.TOOL_STARTED, {"name": tool_call.name, "call_id": tool_call.id})
                self._log_call(tool_call, parallel=True)
                self._audit(tool_call, stage="started")
            outcomes = await asyncio.gather(*[self._dispatch(call) for call in chunk])
            for tool_call, outcome in zip(chunk, outcomes):
                yield self._record_result(tool_call, outcome.value, outcome.status)

    async def _run_in_order(self, calls: list[ToolCall]) -> AsyncIterator[Event]:
        for tool_call in calls:
            if self._cancel.is_set():
                yield self._interrupted_tool(tool_call)
                continue
            yield Event(EventType.TOOL_STARTED, {"name": tool_call.name, "call_id": tool_call.id})
            self._log_call(tool_call)
            self._audit(tool_call, stage="started")
            # Streamed and not awaited: a command's output reaches the surface while it runs and
            # not only when it ends (`_dispatch_streaming`). The last value it yields is the result,
            # and events are told apart by isinstance, the convention `_authorize` uses.
            outcome: Optional[ToolResult] = None
            async for item in self._dispatch_streaming(tool_call):
                if isinstance(item, Event):
                    yield item
                else:
                    outcome = item
            if outcome is not None:
                yield self._record_result(tool_call, outcome.value, outcome.status)

    # -- calls that do not run ---------------------------------------------------------------

    def _log_unstarted(self, tool_call: ToolCall) -> None:
        """`tool/call` for a call answered before it ran: refused, denied or stopped.

        `tool/call` was written only when a call started, so a refused call's arguments were in
        every later request (the assistant message keeps them) and in no log row.
        Found measuring the refused overwrite: the 44 read-guard refusals since 2026-09-25 named their file only in
        the refusal's sentence. A call whose arguments never parsed is the exception, since its raw
        text is not kept in the history (`_mangled_tool`)."""
        if not (isinstance(tool_call.arguments, dict) and "_raw" in tool_call.arguments):
            self._log_call(tool_call)

    def _mangled_tool(self, tool_call: ToolCall) -> Event:
        """Answer a tool call whose arguments never parsed, with the real diagnosis.

        There are two causes with two different cures, and the model can pick the right one only if
        the error says which happened. Truncation (`finish_reason == "length"`) means the same
        content in smaller pieces. Plain bad JSON means re-send with the declared parameters. Either
        way the raw text is not replayed into history: a stored `{"_raw": …}` call reads as a worked
        example and teaches the model to emit `_raw` on purpose (observed 2026-08-15), besides
        re-sending the junk tokens every turn."""
        reason = _MANGLED_TRUNCATED if self._turn_truncated else _MANGLED_UNPARSED
        self._append_denial(tool_call, reason)
        self._audit(tool_call, stage="finished", status="error", reason=reason)
        return Event(
            EventType.TOOL_FINISHED, {"name": tool_call.name, "status": "error", "reason": reason}
        )

    def _append_denial(self, tool_call: ToolCall, reason: str) -> None:
        """Record a call that did not run, and let the repeat guard see it.

        A denial never reaches `_record_result`, since there is no result. Without this the guard
        would be blind to the loop a person is most likely to sit and watch: a model proposing over
        and over the exact thing the permission engine keeps refusing. A Stop from the person is not
        routed here, because interrupting is not the agent failing to make progress."""
        self._log_unstarted(tool_call)
        message = tool_error_message(tool_call, reason)
        reminder = self.repeats.observe(tool_call.name, tool_call.arguments, reason, status=BLOCKED)
        if reminder:
            message["content"] = f"{message['content']}\n\n{reminder}"
        # The consecutive-denial breaker: a model that keeps re-proposing refusals gets one hard-stop
        # sentence in the deny result after three in a row (adopted 2026-09-23). It is guidance only,
        # since a floor never grants, and one approval resets the streak (`_record_result`).
        self._denial_streak = int(getattr(self, "_denial_streak", 0)) + 1
        if self._denial_streak >= 3:
            message["content"] = (
                f"{message['content']}\n\n"
                "Stop retrying this action — three consecutive calls have been refused, "
                "and repeating it will not be approved. Ask the user how to proceed, or "
                "choose a materially different approach."
            )
        # The malformed-arguments breaker (adopted 2026-09-23): a model that cannot produce readable
        # JSON is refused by `_mangled_tool` every time, with different junk each attempt, so the
        # repeat guard never sees a loop. Three in a row end the turn through the same
        # checkpoint-on-cap path other caps use.
        if isinstance(tool_call.arguments, dict) and "_raw" in tool_call.arguments:
            self._malformed_args_streak = int(getattr(self, "_malformed_args_streak", 0)) + 1
            if self._malformed_args_streak >= 3:
                self._malformed_args_exhausted = True
                message["content"] = (
                    f"{message['content']}\n\n"
                    "Stop: three consecutive tool calls arrived with malformed arguments, "
                    "so none of them could run. Re-issue the call with valid JSON using the "
                    "tool's declared parameters, or say plainly what is blocked."
                )
        self.messages.append(message)
        self._log_tool_result(tool_call, message["content"], "denied")

    def _interrupted_tool(self, tool_call: ToolCall) -> Event:
        """The answer for a call that will not run because the person stopped the turn: a
        tool-error result in the history (hosted chat templates reject orphaned tool_calls, and a
        durable resume would prompt for it again) and the finished event for the tool card."""
        self._log_unstarted(tool_call)
        self.messages.append(tool_error_message(tool_call, "interrupted by user"))
        self._log_tool_result(tool_call, "interrupted by user", "interrupted")
        self._audit(tool_call, stage="finished", status="interrupted", reason="user stop")
        return Event(
            EventType.TOOL_FINISHED,
            {"name": tool_call.name, "status": "interrupted", "reason": "stopped"},
        )

    def _direct_call_refusal(self, name: str) -> Optional[str]:
        """Why a call the model made directly to `name` is refused, or None when it is allowed.

        Under progressive discovery the deferred tools are off the wire and reachable through the
        `tool_call` bridge: the announced surface is the callable one, with the bridge named
        as the way through. Sub-calls of a program are unaffected, since `_handle_run_code`
        authorizes those."""
        if name in self.registry.directly_callable():
            return None
        # A built-in deferred by name runs when called by that name. The
        # prompt and the model's habits name `terminal_open` or `git_diff`, and refusing the right
        # name cost a `tool_search` then `tool_call` detour on each use (audit W5). Its arguments are
        # checked like any call's, and an MCP tool keeps the bridge below.
        if name in self.registry.named_deferred:
            return None
        # Progressive discovery refuses only a registered tool that is deferred. An unknown name is
        # not this rule's business: it falls through to ordinary dispatch, which gives the
        # unknown-tool error the model already gets.
        if self.registry.progressive_discovery and self.registry.get(name) is not None:
            return (
                f"`{name}` is not loaded yet — it is behind progressive discovery. Call "
                f'tool_describe(name="{name}") to load it (then call `{name}` directly), '
                f'or run it once with tool_call(name="{name}", arguments={{...}}).'
            )
        return None

    # -- what a call is, and whether it may overlap ------------------------------------------

    def _metadata_for(self, spec: Optional[Any], tool_call: ToolCall) -> Optional[Any]:
        """The tool's policy for this call. A tool whose arguments change its own risk profile (one
        spawning tool, several postures) declares `__tool_policy__` and the engine asks it per
        call, and every other tool answers from static metadata.

        The answer is normalised to a `ToolMetadata`. `task._policy` documents its shape as a plain
        dict of fields, while every reader here (`_parallel_safe`, the authorizer, the change
        receipt) reads attributes. Without the conversion a dict silently read as "no metadata",
        which is how `task` lost its low-risk admission to the parallel set and every delegated spawn
        became a barrier, so two spawns in one message ran their children one after the other."""
        if spec is None:
            return None
        value = spec.policy(tool_call.arguments or {}) if spec.policy is not None else spec.metadata
        return ToolMetadata(**value) if isinstance(value, dict) else value

    def _downloaded_target(self, tool_call: ToolCall) -> Optional[Any]:
        """A file this call would run that the agent downloaded this session, or None. Fetching and
        then executing has no quiet legitimate form, so it reaches a person over any command
        allowlist."""
        match = self._agent_files.match(tool_call.name, tool_call.arguments, step=self._step)
        return match if match is not None and match.downloaded else None

    def _provenance(self, tool_call: ToolCall) -> str:
        """One line naming a file this call would run that the agent itself created, or "".
        It uses a fixed vocabulary and never file contents or text an outsider wrote, so the
        no-untrusted-content rule holds."""
        match = self._agent_files.match(tool_call.name, tool_call.arguments, step=self._step)
        return match.render() if match else ""

    def _notify_lsp_writes(self, tool_call: ToolCall, status: str) -> None:
        """Tell the live language servers about files a successful write just changed.

        pyright does not watch the workspace itself. Measured on 2026-09-20, a `pyrightconfig.json`
        created after the server started was never noticed, so every third-party import stayed
        unresolved for the rest of the session while the CLI with the same config reported zero
        errors. Only the client can say what changed, and the geometry it needs is already here
        (`write_paths`, the boundary the permission engine scopes with). It is best effort: a server
        that is not running, or a notification that fails, must never affect the write it describes."""
        if status != "ok" or tool_call.name not in CODE_WRITE_TOOLS:
            return
        manager = getattr(self, "lsp_manager", None)
        if manager is None:
            return
        paths, located = write_paths(tool_call.name, tool_call.arguments or {})
        if not located:
            return
        root = Path(str(self.permissions.workspace_root))
        targets: list[Path] = []
        for raw in paths[:_LSP_NOTIFY_MAX_FILES]:
            if not raw:
                continue
            candidate = Path(raw)
            targets.append(candidate if candidate.is_absolute() else root / candidate)
        if not targets:
            return
        try:
            manager.notify_files_changed(targets)
        except Exception:  # a notification must never fail the write it describes
            logger.debug("lsp: could not notify changed files", exc_info=True)

    def _parallel_chunks(self, calls: list[ToolCall]) -> list[list[ToolCall]]:
        """Split one parallel segment into runs of at most `max_parallel_tools`. Zero means one
        unbounded run. The split depends only on the cap and the segment, so a batch is chunked the
        same way however many calls it has, which keeps the ordering tests meaningful."""
        cap = self.max_parallel_tools
        if cap <= 0 or len(calls) <= cap:
            return [list(calls)]
        return [list(calls[i : i + cap]) for i in range(0, len(calls), cap)]

    def _parallel_safe(self, tool_call: ToolCall) -> bool:
        """May this call overlap others?

        A tool that declares its capabilities answers for itself (`ToolCapabilities`, with the
        per-call override applied). For the rest the engine derives it: tools whose metadata says
        low risk (reads, searches, git queries) run concurrently, and writes, the shell and anything
        unannotated stay strictly ordered."""
        contract = self.registry.contract(tool_call.name)
        declared = getattr(contract, "capabilities", None)
        if declared is not None:
            return declared.for_call(tool_call.arguments).parallel_safe()
        metadata = self._metadata_for(self.registry.get(tool_call.name), tool_call)
        return getattr(metadata, "risk_level", "") == "low" and not getattr(
            metadata, "requires_approval", False
        )

    def _reserve(self, tool_call: ToolCall) -> Optional[Reservation]:
        """What this call would touch, or None when it must not overlap anything.

        It asks two questions in order, and only the second is new. The first is the metadata filter
        the engine already had: a call that is not parallel-safe is a barrier. The second is whether
        the call writes, and where, which nobody was asking.

        It is asked through `risk.classify`, the classifier the permission engine and the change
        receipt use, and not through the built-in `WRITE_TOOLS` name set. `classify` judges by a
        built-in name table and by `requires_approval` on the tool's metadata, which together cover
        far more than four names: every connector-catalog write, every MCP tool that declares
        approval, and every built-in the table knows. The hole being closed is a mutating tool this
        codebase did not write, admitted to a parallel run with four reads because its `risk_level`
        said "low" and nobody asked whether it writes.

        A writer of unknown reach cannot say where it writes, so it reserves `located=False` and
        conflicts with everything in its segment. It ends up alone, which is the right cost for a
        mutation nobody can scope. For the built-ins the metadata filter has usually barred them
        already, and the guarantee should not depend on two independent rules happening to agree."""
        if not self._parallel_safe(tool_call):
            return None
        name = tool_call.name
        arguments = tool_call.arguments or {}
        if self._call_is_consequential(tool_call):
            paths, located = write_paths(name, arguments)
            return Reservation(tuple(paths), write=True, located=located)
        paths, located = read_paths(name, arguments)
        return Reservation(tuple(paths), write=False, located=located)

    def runs_alone(self, name: str, arguments: Optional[dict] = None) -> bool:
        """Whether a call with these arguments never overlaps another: a barrier, or a write that
        cannot say where it writes (`plan_batch` and `StreamGate` both keep such a call alone). The
        memory door's check of a whole directory relies on it: any change made during the call is
        that call's."""
        reservation = self._reserve(ToolCall(id="", name=name, arguments=dict(arguments or {})))
        return reservation is None or (reservation.write and not reservation.located)

    def _call_is_consequential(self, tool_call: ToolCall) -> bool:
        spec = self.registry.get(tool_call.name)
        risk = classify(
            tool_call.name, self._metadata_for(spec, tool_call), self.permissions.risk_overrides
        )
        return risk is not RiskClass.READ

    def _call_looks(self, tool_call: ToolCall, shown: Any) -> bool:
        """Whether this call only looks: its presenter says it reads or searches, or it is a shell
        command that only reads inside the roots. A surface folds a run of these into one
        "Explored" line; it used to decide that from a list of tool names of its own, which kept three
        removed git tools and never held `git status` through the shell or a connector's read (ADR
        0204). Both halves count: "no side effects" alone would fold a plan, a question and a
        delegation, and "reads or searches" alone would fold a web search, whose query leaves the
        machine and whose sources are a row of their own."""
        try:
            if getattr(shown, "kind", None) in LOOKING_KINDS and not self._call_is_consequential(tool_call):
                return True
            return bool(self.permissions.reads_in_roots(tool_call.name, tool_call.arguments or {}))
        except Exception:  # a surface hint, and it must not cost the call
            return False
