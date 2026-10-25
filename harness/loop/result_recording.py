"""ResultRecordingMixin: turning a finished tool call into history, the log and the event a surface draws."""

from __future__ import annotations

from typing import Any, Optional

from harness.llm import images
from harness.llm.providers import ToolCall
from harness.session.events import Event, EventType
from harness.session.turns import attachment_message
from harness.tools.contract import safe_facts, safe_present_result, safe_render
from harness.tools.design import design_card
from harness.tools.design.drift import drift_text
from harness.tools.todo import TODO_WRITE

from .code_changes import CODE_WRITE_TOOLS
from .message_shapes import delegated_tokens, delegation_ref, log_text, preview, tool_result_message
from .post_edit import lsp_diagnostics_note, syntax_note
from .result_text import _refusal_text, _reports_error

# Result keys the runtime owns and the model, or a `run_code` program, must never read: `_display`
# (user-facing filter metadata), `_attach` (media that travels by a user-role message) and
# `_card_meta` (structured facts for the surface card). It is one definition because two paths need
# the same rule, `_record_result` for a direct call and `_handle_run_code` for a sub-call, and a
# second copy of it (or none) is how a program began receiving results with `_card_meta` doubled.
_SIDECAR_KEYS = ("_display", "_attach", "_card_meta")


def lift_sidecars(result: Any) -> "tuple[Any, Optional[dict], Optional[list], Optional[dict]]":
    """`(clean, display, attach, card_meta)`: the result with the runtime sidecars taken off.

    The cleaned value is what the model reads on a direct call and what a `run_code` program receives
    for a sub-call. Neither has a surface for a card, a filter count or an image attachment, and a
    program that printed one back would spend exactly the context `run_code` exists to save. A result
    that is not a dict passes through with all three as None."""
    if not isinstance(result, dict):
        return result, None, None, None
    display = result.get("_display") or None
    parts = result.get("_attach")
    attach = list(parts) if isinstance(parts, list) and parts else None
    raw_meta = result.get("_card_meta")
    card_meta = raw_meta if isinstance(raw_meta, dict) else None
    clean = {key: value for key, value in result.items() if key not in _SIDECAR_KEYS}
    return clean, display, attach, card_meta


class ResultRecordingMixin:
    def _record_result(self, tool_call: ToolCall, result: Any, status: str) -> Event:
        self._step += 1
        # A call that actually ran ends any streak of refusals: the denial breaker in
        # `_append_denial` counts consecutive denials only.
        self._denial_streak = 0
        if status == "ok":
            # Only successful calls: a write that raised left nothing on disk to run.
            self._agent_files.record(tool_call.name, tool_call.arguments, result, step=self._step)
            # The same rule for the same reason: a proposal that failed changed nothing and must not
            # appear on the receipt (item 4.1).
            if self.changes is not None:
                self.changes.confirm(tool_call.id)
        result, display, attach, card_meta = lift_sidecars(result)
        # A design page this call wrote was checked before it got here (`design_checks.py`). The
        # screenshots go to the model only when there is something to fix, and pass the same
        # admission as any other image below.
        designs = self._take_design_reports(tool_call.id) or []
        self._note_design_standing(designs)
        # A reviewed design whose theme tokens this write moved (step 5).
        drifts = self._take_design_drift(tool_call.id)
        shots = [part for report in designs if report.issues for part in report.attach_parts()]
        if shots:
            attach = (attach or []) + shots
        if attach is not None and any(
            isinstance(part, dict) and part.get("type") == "image_url" for part in attach
        ):
            # The same route-aware admission user attachments get (harness/llm/images.py).
            # `read_image` caps the file at 4 MB, but the route's own byte and pixel ceiling is what
            # the provider enforces, and a 400 here would land in the middle of a turn. The routed
            # adapter declares its own limits. The image is resized when the build can, and replaced
            # by a text placeholder when it cannot, so the model is told an image existed and why it
            # cannot see it.
            attach, _ = images.fit_attach_parts(attach, model=self.model, provider=self.provider)
        # A mutation's before-image cannot come from the tool, because `write_file` never reads what
        # it is about to overwrite. The turn's change log has it, keyed by this call, so it is merged
        # in here and the presenter stays pure. It is read-only and never raises (`ChangeLog`).
        if self.changes is not None:
            hunks = self.changes.hunks(tool_call.id)
            if hunks:
                card_meta = {**(card_meta or {}), "diffs": hunks}
        # Both projections see the same value, and see it after `_display` is lifted off. `render`
        # feeds the model, the one reader `_display` must never reach, and giving the two projections
        # different inputs would be a trap for whoever writes the next contract.
        contract = self.registry.contract(tool_call.name)
        rendered = safe_render(contract, tool_call.name, tool_call.arguments, result)
        if rendered is None:
            # A failure with no renderer of its own reads like every other failure.
            rendered = _refusal_text(result)
        # What happened, as data, for the log row below, taken from the value the tool
        # returned before any note or bound touches the text.
        facts = safe_facts(contract, tool_call.name, tool_call.arguments, result)
        # The language servers hear what changed on disk before the syntax note is built. pyright
        # does not watch for a new `pyrightconfig.json`, and without this a config the model just
        # wrote never takes effect in its own session.
        self._notify_lsp_writes(tool_call, status)
        rendered, result = self._with_diagnostics(
            tool_call,
            status,
            rendered,
            result,
            design="\n\n".join([report.text() for report in designs] + ([drift_text(drifts)] if drifts else [])) or None,
        )
        rendered, result = self._enforce_turn_budget(tool_call, rendered, result, facts)
        # Observed against what the model is about to read, not the raw value: two calls that showed
        # the model different text did different work, whatever the value underneath looked like.
        reminder = self.repeats.observe(
            tool_call.name, tool_call.arguments, rendered if rendered is not None else result, status=status
        )
        self._note_tool_outcome(tool_call.name, tool_call.arguments, status)
        # Delegated work is spend too. The parent's own traffic barely moves while a child reads its
        # way through a codebase, and without this fold the token budget would be blind to the
        # fan-out shape it most needs to bound. A child reports its total, and nothing else is
        # invented here.
        delegated = delegated_tokens(result)
        self._turn_tokens += delegated
        timing = self._call_timing.pop(tool_call.id, None)
        # Whether the call did what was asked. A tool that returned its own refusal (`{"error":
        # …}`: an MCP server's error, a goal that would not close, a message to a workflow run)
        # dispatched fine: the log counted it a failure while its row read "ok" on screen (one in
        # three failures, counted 2026-10-06). The log, the message and the event say it alike.
        failed = status != "ok" or _reports_error(result)
        self.log.append(
            "tool/result",
            turn=self._turn_no,
            step=self._step_no,
            call_id=tool_call.id,
            name=tool_call.name,
            content=log_text(rendered if rendered is not None else result),
            # `status` is how the dispatch ended. `is_error` is whether the call did what was asked,
            # which a tool that returned its own refusal did not.
            is_error=failed,
            status=status,
            timing=timing,
            # What the tool declared about this result, and what the bound did to it. These are the
            # fields a count reads, so nothing has to parse `content`.
            facts=facts or None,
            # Set when the result carries model-visible content a string result cannot (images). The
            # user-role message tagged with this call id is covered here.
            attachment=bool(attach) or None,
            # Delegation lineage: when a tool result names the child run it came back from, that run
            # id rides the log beside the call that spawned it. `delegation.run_id` joined to this
            # event's `call_id` and `turn` answers "which run produced this", which could not be
            # answered before because a foreground child had no identity anywhere.
            delegation=delegation_ref(result),
            # The child's spend as charged to this turn above: every token `turn/end` reports is
            # attributable to a logged call (scripts/harness_kpis.py checks).
            tokens=delegated or None,
        )
        if designs:
            self._log_design_checks(tool_call, designs)
        if drifts:
            self._log_design_drift(tool_call, drifts)
        if tool_call.name == TODO_WRITE and status == "ok" and isinstance(result, dict):
            # The plan a surface renders gets its own record and is not re-derived from the message
            # list the model is fed. `todo/write` is a log-only event and never one of the three
            # model-visible surface types. It is recorded from the tool's return value, the
            # normalized list the agent itself is working from and not what it typed, and only for a
            # call that succeeded, because this log holds what was observed and never what was
            # inferred.
            todos = result.get("todos")
            if isinstance(todos, list):
                self.log.append(
                    "todo/write",
                    turn=self._turn_no,
                    step=self._step_no,
                    call_id=tool_call.id,
                    todos=todos,
                )
        message = tool_result_message(tool_call, result, rendered, reminder, timing=timing)
        # A reopened session draws the row as the live one was drawn.
        if failed:
            message["_failed"] = True
        # A call that reached execution had readable arguments, and that ends any streak of
        # malformed calls (the breaker itself is in `_append_denial`, where the provider's `_raw`
        # refusals land).
        self._malformed_args_streak = 0
        if display:
            message["_display"] = display
        if designs:
            # The row under the write, on a reload as live.
            message["_design_check"] = [design_card(report) for report in designs]
        self.messages.append(message)
        if attach:
            # Tagged, so the eight places that use a user message as a landmark do not read a
            # picture as a person taking a turn (harness/session/turns.py). While a batch runs it
            # queues, so the whole tool-result run stays contiguous (DeepSeek's rule), and outside
            # one it lands at once.
            attachment = attachment_message(tool_call.id, attach)
            if self._attach_queue is not None:
                self._attach_queue.append(attachment)
            else:
                self.messages.append(attachment)
        self._audit_filtered(tool_call, display)
        self._audit(tool_call, stage="finished", status=status, result=result, result_preview=preview(result))
        self._note_ingestion(tool_call, status)
        rule = self._standing_notes.pop(tool_call.id, "")
        # What a person reads about what just happened. `result_preview` stays as the fallback every
        # tool not yet migrated still renders through.
        shown = safe_present_result(contract, tool_call.name, tool_call.arguments, result, card_meta)
        short = preview(result)
        # A preview that was cut is the first 300 characters of the result's JSON with its
        # newlines escaped: a live row drew that fragment as the call's output, cut mid-value and
        # unreadable, until the session was reloaded and the row got the stored text
        # (owner-hit 2026-10-13). When the preview is cut, the row is sent the text the model
        # was given, which is the bounded one and the one a reload shows.
        whole = message.get("content") if short.endswith("...") else None
        if shown:
            # The card rides the message as a sidecar, like `_display`, so a reopened session draws
            # the row the live one drew. Nothing else carries it: the model-facing text has the
            # numbers baked in a different shape, and the session log is a diagnostic record and not
            # a replay source. Without this the feature would exist only until the next reload.
            message["_presentation"] = shown.as_dict()
        looks = self._call_looks(tool_call, shown)
        if looks:
            # A reopened session folds the same run of looks into "Explored".
            message["_looks"] = True
        return Event(
            EventType.TOOL_FINISHED,
            {
                "name": tool_call.name,
                "status": status,
                # `status` stays how the dispatch ended (readers match on "ok"); `is_error` is
                # whether the call did what was asked, the same fact the message's `_failed` keeps.
                **({"is_error": True} if failed else {}),
                # The id the undo endpoint is keyed by. A surface cannot offer to take back a step it
                # has no way to name.
                "call_id": tool_call.id,
                "result_preview": short,
                **({"result_text": whole} if isinstance(whole, str) and whole else {}),
                **({"presentation": shown.as_dict()} if shown else {}),
                **({"looks": True} if looks else {}),
                # The call's own wall time. The message has carried it all along, and a row that
                # shows "Ran for 12s" needs it on the event, since re-deriving it from receipt
                # stamps charges the tool for the approval it waited on.
                **self._duration_field(timing),
                # Only a call that succeeded: a failed write changed nothing, and undoing it would
                # restore identical content while implying something happened.
                **(
                    {"undoable": True}
                    if status == "ok" and self.undo is not None and self.undo.undoable(tool_call.id)
                    else {}
                ),
                **({"display": display} if display else {}),
                **({"design_check": message["_design_check"]} if designs else {}),
                # Model-visible media a string result cannot carry (an image `read_image` fetched, an
                # MCP screenshot). It rides the same event the row settles on, so the live view draws
                # the thumbnail the reloaded one already draws. Otherwise a screenshot appeared only
                # after reopening the session.
                **({"attachments": attach} if attach else {}),
                **({"standing_rule": rule} if rule else {}),
            },
        )

    @staticmethod
    def _duration_field(timing: Any) -> dict[str, int]:
        if (
            isinstance(timing, dict)
            and isinstance(timing.get("started"), (int, float))
            and isinstance(timing.get("ended"), (int, float))
            and timing["ended"] >= timing["started"]
        ):
            return {"duration_ms": int((timing["ended"] - timing["started"]) * 1000)}
        return {}

    def _with_diagnostics(
        self, tool_call: ToolCall, status: str, rendered: Any, result: Any, design: Optional[str] = None
    ) -> tuple[Any, Any]:
        """Add the post-edit notes to what the model reads.

        A file the model just wrote that no longer parses is the "that change is broken" case that
        can be shown for free (`harness/tools/syntax.py`), and a language server adds the type-level
        half when one is configured. The notes are appended to the result the model reads
        and never rewrite it, so each write tool's declared contract still holds. A dict result keeps
        its shape and gains one key for each kind of note."""
        notes = {
            "syntax": syntax_note(
                status,
                tool_call.name,
                tool_call.arguments or {},
                workspace_root=self.permissions.workspace_root,
                write_tools=CODE_WRITE_TOOLS,
            ),
            "diagnostics": lsp_diagnostics_note(
                getattr(self, "lsp_manager", None),
                status,
                tool_call.name,
                tool_call.arguments or {},
                workspace_root=self.permissions.workspace_root,
                write_tools=CODE_WRITE_TOOLS,
                # What the note has already said, per file, for this engine's life: an error is
                # reported once and not after every edit (post_edit.py).
                reported=self.__dict__.setdefault("_lsp_reported", {}),
            ),
            # What the design check measured on a design page this call wrote.
            "design": design,
        }
        if not any(notes.values()):
            return rendered, result
        joined = "\n\n".join(note for note in notes.values() if note)
        if isinstance(rendered, str):
            return f"{rendered}\n\n{joined}", result
        if isinstance(result, str):
            return rendered, f"{result}\n\n{joined}"
        if isinstance(result, dict):
            return rendered, {**result, **{key: note for key, note in notes.items() if note}}
        return joined, result

    def _audit_filtered(self, tool_call: ToolCall, display: Optional[dict]) -> None:
        """The trace outside the model's view that the person can see: the rule class and a count,
        never content."""
        hidden = int((display or {}).get("hidden_by_filters") or 0)
        stripped = int((display or {}).get("hidden_fields") or 0)
        if not (hidden or stripped):
            return
        parts = []
        if hidden:
            parts.append(f"{hidden} result(s) hidden")
        if stripped:
            parts.append(f"{stripped} field value(s) stripped")
        self._audit(tool_call, stage="filtered", status="hidden", reason=" · ".join(parts) + " by privacy filters")
