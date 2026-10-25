"""PermissionFlowMixin: the permission flow for one tool call.

A call is judged by the policy (`_decide`), may be put to the person (`_authorize`), and the
person's answer is applied (`_settle_approval`). A refusal never runs the call: the model is given the
reason as the tool's result.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, AsyncIterator, Optional

from harness.llm.providers import ToolCall
from harness.safety.permissions import Decision
from harness.session.events import Event, EventType
from harness.tools.contract import ToolPresentation

from .reach import extend_reach, reach_of
from .approvals import (
    EDIT_EMPTY_MESSAGE,
    EDIT_NOT_RUN_MESSAGE,
    UNCERTAIN_RETRY_MESSAGE,
    UNCERTAIN_SKIP_MESSAGE,
    USER_DENY_MESSAGE,
    USER_STOP_MESSAGE,
    ApprovalOutcome,
    PermissionRequest,
)


class PermissionFlowMixin:
    # -- the verdict, for the model's call and for a person's edit of it -----------------------

    def _decide(self, tool_call: ToolCall, metadata: Any) -> "tuple[Decision, str]":
        """The policy verdict for one call, and the provenance note its card shows.

        It is a function and not inline code because an edit is a new call: the person's edited
        arguments are judged by this same engine, from scratch, and a second copy of the floors would
        be exactly where the two judgements drift apart."""
        decision = self.permissions.evaluate(tool_call.name, tool_call.arguments, metadata)
        # Running something the agent downloaded this session is the classic fetch-then-execute
        # chain, and there is no quiet legitimate version of it. So it goes to a person,
        # over any command allowlist that would otherwise wave it through: a `python` prefix rule
        # must not vouch for a script pulled off the internet a moment ago. A hard deny is left
        # alone, because this floor only tightens an allow and never loosens a block. Files the agent
        # wrote are not floored, since "write this script and run it" is ordinary work. They travel
        # as a fact on the card for the person to weigh.
        provenance_note = self._provenance(tool_call)
        full_access = bool(getattr(self.permissions, "full_access", False))
        # Full access asks nobody: the download still travels as a fact on the call's
        # card, but it no longer parks the call.
        if (
            not full_access
            and self._downloaded_target(tool_call) is not None
            and (decision.needs_user or decision.allowed)
        ):
            decision = replace(
                decision,
                allowed=False,
                needs_user=True,
                human_only=True,
                reason=f"this file was downloaded by the agent this session — {provenance_note}",
            )
        # This call was already under way when the session stopped (item 2.2). Whether it took
        # effect cannot be known from here: the engine's only evidence is a missing result, and a
        # crash in the middle of a call leaves exactly that behind too. So it goes to a person,
        # human-only and over every allowlist, because no policy can answer a question about the
        # past. Like the provenance floor above it only tightens: a hard deny stays where it is.
        if tool_call.id in self._uncertain_calls and (decision.needs_user or decision.allowed) and full_access:
            # Full access asks nobody, and it must not repeat a call that may already have taken
            # effect either (a sent message would go twice). So the call is not run and the model
            # is told why: it can check, and a call it makes again is a new call that runs.
            decision = replace(decision, allowed=False, needs_user=False, reason=UNCERTAIN_RETRY_MESSAGE)
        elif tool_call.id in self._uncertain_calls and (decision.needs_user or decision.allowed):
            decision = replace(
                decision,
                allowed=False,
                needs_user=True,
                human_only=True,
                reason=(
                    "this was already under way when the session stopped, so it may "
                    "already have happened — run it again?"
                ),
            )
        return decision, provenance_note

    def _apply_grant(self, outcome: ApprovalOutcome, tool_call: ToolCall) -> None:
        """Remember a session grant, which is what the "always" outcomes mean."""
        if outcome is ApprovalOutcome.ALWAYS_TOOL:
            self.permissions.allow_tool_for_session(tool_call.name)
        elif outcome is ApprovalOutcome.ALWAYS_COMMAND:
            self.permissions.allow_command_for_session(str(tool_call.arguments.get("command", "")))
        elif outcome is ApprovalOutcome.ALWAYS_DOMAIN:
            self.permissions.allow_domain_for_session(str(tool_call.arguments.get("url", "")))

    def _apply_edit(self, tool_call: ToolCall, arguments: dict[str, Any]) -> None:
        """Run the call with the person's arguments, and make every record say so.

        History is rewritten for this call id. Leaving the model's original arguments in the
        assistant message would show the model a call it did not make, and a later `resume` would
        replay the original against a result the edited call produced. The session log keeps both
        sides: `tool/edited` records the model's arguments beside the person's, and the `tool/call`
        written at dispatch records what actually ran."""
        original = tool_call.arguments
        tool_call.arguments = dict(arguments)
        payload = json.dumps(arguments)
        for message in self.messages:
            if message.get("role") != "assistant":
                continue
            for entry in message.get("tool_calls") or []:
                if entry.get("id") == tool_call.id:
                    entry.setdefault("function", {})["arguments"] = payload
        self.log.append(
            "tool/edited",
            turn=self._turn_no,
            step=self._step_no,
            call_id=tool_call.id,
            name=tool_call.name,
            original=original,
            arguments=arguments,
        )

    # -- the flow ----------------------------------------------------------------------------

    async def _authorize(
        self, tool_call: ToolCall, call_shown: Optional[ToolPresentation] = None
    ) -> "AsyncIterator[Event | bool]":
        """The permission flow for one call. The caller emits TOOL_PROPOSED. This yields its events
        and then True or False (allowed) last. A denied or unknown call has its tool-error message
        appended here.

        `call_shown` is the call's presentation, already computed by the caller for the propose
        event. It is a parameter and not a lookup here so that the card a person sees while a call
        is pending and the one they see when it asks for approval are the same object, and so a
        tool's `present_call` runs once per call and not once per surface that draws it."""
        spec = self.registry.get(tool_call.name)
        metadata = self._metadata_for(spec, tool_call)

        # The pre_tool_use hook: a person's own rule runs before any policy path, so `deny` is final
        # and no card is raised for a call their script refused. An `allow` is not a permission,
        # since everything below still runs, and a failing hook is logged and ignored because a hook
        # cannot break a turn.
        hook = await self._run_hooks(
            "pre_tool_use", tool=tool_call.name, arguments=tool_call.arguments, call_id=tool_call.id
        )
        if hook is not None:
            if hook.context:
                self.messages.append(self._hook_message("pre_tool_use", hook.context))
            if hook.denied:
                reason = hook.attributed(f"a hook refused {tool_call.name}")
                self._append_denial(tool_call, reason)
                yield Event(EventType.TOOL_FINISHED, {"name": tool_call.name, "status": "denied", "reason": reason})
                self._audit(tool_call, stage="finished", status="denied", reason=reason)
                yield False
                return
            for error in hook.errors:
                self._append_notice("info", f"a pre_tool_use hook failed: {error}")

        decision, provenance_note = self._decide(tool_call, metadata)
        if hook is not None and hook.asked and decision.allowed and not getattr(self.permissions, "full_access", False):
            # A hook may put a person in front of a call the mode would allow (a plugin's
            # `permissionDecision: "ask"`) and never the reverse: a denial stays a denial. Full
            # access asks nobody, so there a hook's ask runs the call; its deny still
            # refuses it, above.
            decision = replace(
                decision,
                allowed=False,
                needs_user=True,
                reason=hook.attributed("a hook asks you to approve this call"),
            )
        allowed, reason = decision.allowed, decision.reason

        if allowed and decision.rule:
            # A task's standing rule allowed this call. The exact rule is audited (every
            # auto-allowed call cites its rule) and remembered, so the tool card can say "allowed by
            # standing rule".
            self._standing_notes[tool_call.id] = decision.rule
            self._audit(tool_call, stage="auto_allowed", status="allowed", reason=reason)

        if not allowed and decision.needs_user:
            card = self._approval_card(tool_call, decision, metadata, provenance_note, call_shown)
            yield Event(EventType.PERMISSION_REQUIRED, card)
            self._audit(tool_call, stage="approval_requested", reason=decision.reason, call_id=tool_call.id)
            request = PermissionRequest(
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
                metadata=metadata,
                reason=decision.reason,
                tool_call_id=tool_call.id,
                presentation=call_shown.as_dict() if call_shown else None,
                card=card,
            )
            outcome = await self._interruptible(self.approver(request), interrupted=ApprovalOutcome.DENY)
            allowed, reason = self._settle_approval(tool_call, metadata, request, outcome, reason)

        if not allowed:
            if spec is None:
                reason = f"unknown tool: {tool_call.name}"
            self._append_denial(tool_call, reason)
            yield Event(EventType.TOOL_FINISHED, {"name": tool_call.name, "status": "denied", "reason": reason})
            self._audit(tool_call, stage="finished", status="denied", reason=reason)
            yield False
            return

        if spec is None:
            self._append_denial(tool_call, f"unknown tool: {tool_call.name}")
            yield Event(EventType.TOOL_FINISHED, {"name": tool_call.name, "status": "error", "reason": "unknown tool"})
            yield False
            return

        extend_reach(self, tool_call)
        yield True

    def _approval_card(
        self,
        tool_call: ToolCall,
        decision: Decision,
        metadata: Any,
        provenance_note: str,
        call_shown: Optional[ToolPresentation],
    ) -> dict[str, Any]:
        """What a person reads about what is about to happen, which is the point of the approval
        card. A tool with no contract has no presentation, and the surface then falls back to `name`
        and `arguments`."""
        from harness.safety.permissions import standing_rule_candidate

        return {
            "name": tool_call.name,
            "arguments": tool_call.arguments,
            "reason": decision.reason,
            # Which axis refused (D4): "boundary" means outside what this session can reach and no
            # click can change that, and "consent" means a person's call.
            "axis": decision.axis,
            # True when no mode, grant or allowlist can clear this ask: a floor that puts a person in
            # front of the call (a file that runs later, authority that outlives the session, a
            # download from this session). Full access does not cover it, and the card says so
            # instead of leaving the person to wonder why the mode that "runs everything without
            # asking" asked.
            "human_only": decision.human_only,
            # The call had started before the process that ran it ended, and whether it took
            # effect cannot be known (item 2.2): the card asks whether to run it AGAIN,
            # with checking first as the answer most people give.
            "uncertain": tool_call.id in self._uncertain_calls,
            "category": getattr(metadata, "category", ""),
            # The fact a person could not get from the command text alone.
            "provenance": provenance_note,
            # The exact target a standing rule could pin, or None when the call is not eligible (no
            # declared target argument, or exec risk). Surfaces use it to offer "Allow every time" on
            # approval cards of automation runs only.
            "standing_target": standing_rule_candidate(
                tool_call.name, tool_call.arguments, metadata, self.permissions.risk_overrides
            ),
            # The folders this call reaches outside the session, which approving adds to it:
            # the card says so, since the answer outlives this one call.
            "reach": reach_of(self, tool_call),
            **({"presentation": call_shown.as_dict()} if call_shown else {}),
            **(self.approval_extras(tool_call.name, tool_call.arguments) if self.approval_extras else {}),
        }

    def _settle_approval(
        self,
        tool_call: ToolCall,
        metadata: Any,
        request: PermissionRequest,
        outcome: ApprovalOutcome,
        reason: str,
    ) -> tuple[bool, str]:
        """Apply the person's answer to a call put to them: `(allowed, reason)`."""
        if outcome is ApprovalOutcome.MODIFY and not self._cancel.is_set():
            # The person edited the call before running it. The edit is a new call, so it is
            # validated and checked again from scratch, and nothing runs on the original check.
            # Pressing Run is the consent for the arguments in the editor, so an ordinary ask
            # passes. What an edit can never clear is a floor (`human_only`), a fact someone has to
            # see, such as Reeve's own settings, a file that executes later, or a download made this
            # session.
            edited = dict(request.edited_arguments or {})
            if not edited:
                allowed, reason = False, EDIT_EMPTY_MESSAGE
            else:
                probe = ToolCall(id=tool_call.id, name=tool_call.name, arguments=edited)
                verdict, _note = self._decide(probe, metadata)
                if (not verdict.needs_user and not verdict.allowed) or verdict.human_only:
                    allowed, reason = False, f"{EDIT_NOT_RUN_MESSAGE} {verdict.reason}"
                else:
                    allowed, reason = True, "approved by user (edited)"
                    self._apply_edit(tool_call, probe.arguments)
            self._audit(
                tool_call,
                stage="approval_resolved",
                call_id=tool_call.id,
                status="approved" if allowed else "denied",
                approval="modify",
                reason=reason,
            )
            return allowed, reason
        if outcome is ApprovalOutcome.DENY:
            # Stop lands here too, because `_interruptible` resolves a pending card as DENY. Both
            # refuse the action and neither runs it. They differ only in what the model is told, and
            # that difference is the point.
            if self._cancel.is_set():
                reason = USER_STOP_MESSAGE
            elif tool_call.id in self._uncertain_calls:
                # Not "the user refused this action": the user refused to repeat it, which says
                # nothing about whether it already happened.
                reason = UNCERTAIN_SKIP_MESSAGE
            elif request.denial_message:
                # An approver that rejected in its own words (a broker's reject strings) gets them
                # through untouched. This is checked after stop and uncertain, so an interrupt or a
                # replay question still says what it actually was.
                reason = request.denial_message
            else:
                reason = USER_DENY_MESSAGE
            self._audit(
                tool_call,
                stage="approval_resolved",
                call_id=tool_call.id,
                status="denied",
                approval=outcome.value,
                reason=reason,
            )
            return False, reason
        self._apply_grant(outcome, tool_call)
        reason = "approved by user"
        self._audit(
            tool_call,
            stage="approval_resolved",
            call_id=tool_call.id,
            status="approved",
            approval=outcome.value,
            reason=reason,
        )
        return True, reason
