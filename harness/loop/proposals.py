"""ProposalsMixin: the tools that put a question to the person and wait for the answer.

Each handler checks that the request makes sense, emits the event a surface draws as a card, waits
on the injected requester, and records the outcome as the tool's result. They share the last step
(`_store_proposal_result` and `_close_proposal`) and the wait (`_await_reply`).
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from harness.llm.providers import ToolCall
from harness.safety.permissions import Mode
from harness.session.events import Event, EventType
from harness.tools import toolchain
from harness.tools.plan_file import write_plan

from .message_shapes import preview, tool_result_message

_NO_RESPONSE = "no response"
_INTERRUPTED = "interrupted by user"
_FALLBACK_CHECK = (
    "Continue without it: use a fallback check if you have one, and say in "
    "your report which checks were degraded."
)


class ProposalsMixin:
    # -- the parts every handler shares ------------------------------------------------------

    async def _await_reply(self, pending, *, interrupted: dict, nobody: dict) -> dict:
        """The requester's answer. Stop gives `interrupted`, and an empty answer gives `nobody`."""
        return await self._interruptible(pending, interrupted=interrupted) or nobody

    def _store_proposal_result(self, tool_call: ToolCall, result: dict, status: str) -> None:
        self.messages.append(tool_result_message(tool_call, result))
        self._log_tool_result(tool_call, result, status)

    def _close_proposal(self, tool_call: ToolCall, result: dict, status: str) -> Event:
        shown = preview(result)
        self._audit(tool_call, stage="finished", status=status, result=result, result_preview=shown)
        return Event(
            EventType.TOOL_FINISHED,
            {"name": tool_call.name, "status": status, "result_preview": shown},
        )

    # -- plans -------------------------------------------------------------------------------

    async def _handle_plan_proposal(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        """Show the plan for review, wait for the person's decision from outside the turn, and
        apply it.

        Proposing works in every write-capable arrangement (decision 0005): approval is the
        consent. In full access the consent was given in advance, so the plan is kept
        and adopted at once, with no card.
        Only a bare read-only preset refuses, for its own reason and not as a gate. It is a
        read-only conversation with no way to execute, so the right next step is prose (turn the plan
        overlay on, or switch presets, to have the change carried out).

        When the approver names a mode the session adopts it, and a READ_ONLY choice lands on
        WORKSPACE_WRITE because approving a plan is the moment execution begins. Approval always
        clears the plan overlay (`set_plan(False, reason="plan_approval")`), since the rehearsal is
        over either way. Rejection changes nothing and returns the person's feedback so the agent can
        revise."""
        args = tool_call.arguments or {}
        plan = str(args.get("plan", ""))
        mode = self.permissions.mode
        if mode is Mode.READ_ONLY and not self.permissions.plan:
            result: dict[str, Any] = {
                "approved": False,
                "error": (
                    "this session is read-only, so describe the proposed changes in chat "
                    "instead — switch the preset or turn on plan mode to have them done"
                ),
            }
        elif self.permissions.full_access:
            # Full access waits on nobody: a card here stopped a session the owner had
            # left running (2026-10-15). But full access is consent to run tools, not to a plan the
            # person never saw: told "approved, carry it out now", a model asked "what next?"
            # laid out a plan and started it (2026-10-18). So the plan is kept as the working plan
            # and not approved, and the model is told to do what the person asked. The plan
            # overlay is the person asking to review, and `full_access` is false while it is on.
            self._audit(tool_call, stage="plan_proposed")
            result = self._keep_unasked_plan(plan)
        elif self.plan_approver is None:
            result = {"approved": False, "error": "plan approval isn't available here"}
        else:
            location = {"path": str(self.plan_path)} if self.plan_path is not None else {}
            yield Event(EventType.PLAN_PROPOSED, {"plan": plan, **location})
            self._audit(tool_call, stage="plan_proposed")
            result = await self._await_reply(
                self.plan_approver(dict(args), tool_call.id),
                interrupted={"approved": False, "error": _INTERRUPTED},
                nobody={"approved": False, "error": _NO_RESPONSE},
            )
        if result.get("approved"):
            result = self._adopt_approved_plan(plan, result, mode)
        status = "ok" if result.get("approved") or result.get("not_asked") else "denied"
        self._store_proposal_result(tool_call, result, status)
        yield self._close_proposal(tool_call, result, status)

    def _keep_unasked_plan(self, plan: str) -> dict:
        """A plan proposed in full access: shown, kept in the plan file as the working plan, and
        not approved. Nothing changes mode, and compaction does not carry it as approved."""
        if plan.strip() and self.plan_path is not None:
            try:
                write_plan(self.plan_path, plan)
            except OSError:
                pass  # the plan is still in the call the model just made
        return {
            "not_asked": "full access",
            "note": (
                "Full access: nobody is asked to approve plans, and the person has not seen or "
                "approved this one. Do what the person asked: if they gave you this work, carry "
                "the plan out; if they asked a question or to talk it over, answer and stop: they "
                "will say when to go ahead."
            ),
        }

    def _adopt_approved_plan(self, plan: str, result: dict, mode: Mode) -> dict:
        """Apply an approved plan: choose the mode, end the plan overlay, and keep the plan."""
        chosen = result.get("mode")
        if chosen:
            try:
                mode = Mode(str(chosen))
            except ValueError:
                mode = Mode.WORKSPACE_WRITE
        if mode is Mode.READ_ONLY:
            # Approving a plan is where execution begins, and a read-only preset cannot carry it.
            mode = Mode.WORKSPACE_WRITE
        self.set_mode(mode, reason="plan_approval")
        # The overlay ends whatever happens next: approval closes the rehearsal, and an approved
        # plan is one that now has standing.
        self.set_plan(False, reason="plan_approval")
        # Compaction must carry the plan into its block, though the tool call that stated it is
        # clipped to 200 characters in the summarized span (see `approved_plan` on the engine). The
        # file copy is rewritten too, so the approved text and the working document agree.
        if plan.strip():
            self.approved_plan = plan
            if self.plan_path is not None:
                try:
                    write_plan(self.plan_path, plan)
                except OSError:
                    pass  # the approved text still rides the compaction block
        return {**result, "mode": mode.value, "note": "plan approved — implement it now"}

    async def _handle_update_plan(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        """Write the session's working plan file (the `update_plan` tool).

        The engine owns this and it is not a permissioned write. The read-only guard is a boundary
        and denies only, and carving a write exception for one path would weaken the floor the whole
        preset rests on. The file goes in the session's own `.reeve/plans/` area, which is inside
        a root so `read_file` can read it back without an out-of-root prompt, and never into the
        person's tracked files.

        A bare read-only preset is refused for the reason `propose_plan` is: a read-only
        conversation with no planning workflow should describe, not file. The plan overlay is the
        exception, because writing the working plan is what `/plan` is for. A session with no plan
        path (the CLI, a rootless one) gets a readable refusal so the model can put the plan in its
        answer."""
        args = tool_call.arguments or {}
        content = str(args.get("content", "") or "")
        if self.plan_path is None:
            result: dict[str, Any] = {
                "updated": False,
                "error": "plan files aren't available in this session — put the plan in "
                "your answer instead",
            }
        elif self.permissions.mode is Mode.READ_ONLY and not self.permissions.plan:
            result = {"updated": False, "error": "this session is read-only, so describe the plan in chat instead"}
        elif not content.strip():
            result = {"updated": False, "error": "content must not be empty — send the full plan each call"}
        else:
            try:
                write_plan(self.plan_path, content)
            except OSError as exc:
                result = {"updated": False, "error": f"could not write the plan file: {exc}"}
            else:
                result = {"updated": True, "path": str(self.plan_path), "chars": len(content)}
                yield Event(EventType.PLAN_UPDATED, {"path": str(self.plan_path), "chars": len(content)})
        status = "ok" if result.get("updated") else "error"
        self._store_proposal_result(tool_call, result, status)
        self.log.append(
            "plan/update",
            turn=self._turn_no,
            step=self._step_no,
            call_id=tool_call.id,
            path=str(self.plan_path) if self.plan_path is not None else None,
            chars=len(content),
            updated=bool(result.get("updated")),
        )
        yield self._close_proposal(tool_call, result, status)

    # -- tools -------------------------------------------------------------------------------

    async def _handle_tool_request(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        """Ask the person to install a missing tool, wait for the answer and hand the outcome back.

        Declining is a normal outcome and not an error. The result tells the agent to fall back and
        disclose the gap, because a security report that quietly loses a check is worse than one
        that says which checks it could not run."""
        args = tool_call.arguments or {}
        name = str(args.get("name", "")).strip()
        reason = str(args.get("reason", ""))

        if self.tool_requester is None or not name:
            result: dict[str, Any] = {
                "installed": False,
                "error": "tool requests aren't available here",
                "guidance": _FALLBACK_CHECK,
            }
        else:
            info = toolchain.describe(name)
            if info is None:
                # A tool outside the pinned catalog gets no card at all. Agents were routing
                # ordinary brew and pip installs through the install card, which could only fail
                # after approval (hit by the owner, 2026-08-20). The agent has a shell with its own
                # approval flow, so it is sent there and not to the person.
                catalog = ", ".join(sorted(toolchain.MANAGED))
                result = {
                    "installed": False,
                    "error": f"'{name}' is not in the pinned tool catalog ({catalog}).",
                    "guidance": (
                        "Install it yourself with the shell (brew/pip/…, subject to the "
                        "normal command approval), or continue without it and say in your "
                        "report which checks were degraded."
                    ),
                }
            elif getattr(self.permissions, "full_access", False):
                # Full access asks nobody: the pinned build is installed with no card,
                # its digest verified as on the card's Install.
                self._audit(tool_call, stage="tool_requested", reason=reason)
                try:
                    path = await asyncio.to_thread(toolchain.install, name)
                except Exception as exc:  # noqa: BLE001 - the agent reads the reason as it is
                    result = {"installed": False, "error": str(exc), "guidance": _FALLBACK_CHECK}
                else:
                    result = {
                        "installed": True,
                        "path": path,
                        "version": info.get("version", ""),
                        "note": "installed without asking: the session is in full access",
                    }
            else:
                # The prompt says up front that this can be installed (a pinned build for this
                # platform exists). A card offering Install for a tool we cannot fetch would turn
                # the person's approval into a certain error.
                yield Event(
                    EventType.TOOL_REQUESTED,
                    {
                        "name": name,
                        "reason": reason,
                        "installable": True,
                        "version": info.get("version", ""),
                        "summary": info.get("summary", ""),
                        "source": info.get("source", ""),
                    },
                )
                self._audit(tool_call, stage="tool_requested", reason=reason)
                result = await self._await_reply(
                    self.tool_requester(dict(args), tool_call.id),
                    interrupted={"installed": False, "error": _INTERRUPTED},
                    nobody={"installed": False, "error": _NO_RESPONSE},
                )
                if not result.get("installed"):
                    # The card also says "or install it yourself and continue". Someone who
                    # installed the tool mid-prompt and pressed Continue has provided it and not
                    # declined it, so their copy is looked for before this counts as a refusal.
                    found = toolchain.resolve(name)
                    if found:
                        result = {
                            "installed": True,
                            "path": found,
                            "note": (
                                "the user provided their own copy instead of the managed "
                                "install — use it from this path"
                            ),
                        }
                if not result.get("installed"):
                    result.setdefault("guidance", _FALLBACK_CHECK)

        status = "ok" if result.get("installed") else "denied"
        self._store_proposal_result(tool_call, result, status)
        yield self._close_proposal(tool_call, result, status)

    # -- questions ---------------------------------------------------------------------------

    async def _handle_ask_user(self, tool_call: ToolCall) -> AsyncIterator[Event]:
        """Put a question to the person and return the answer as the tool result. It is answered
        inline in the live session, or from the inbox when the session runs unattended."""
        args = tool_call.arguments or {}
        question = str(args.get("question", "")).strip()
        # In the grouped form `questions` alone is a valid call and the singular field may be empty.
        # The asker validates the entries, so this only decides whether anything is asked.
        if not question:
            for entry in args.get("questions") or []:
                if isinstance(entry, dict) and str(entry.get("question", "")).strip():
                    question = str(entry["question"]).strip()
                    break
        if self.question_asker is None or not question:
            result: dict[str, Any] = {
                "answer": "",
                "error": "no question was asked" if not question else "asking isn't available here",
            }
        else:
            # The asker knows the mode (attended: a live inline prompt; unattended: the inbox), so
            # it shows the question and the engine only waits.
            self._audit(tool_call, stage="question_requested", reason=question)
            result = await self._await_reply(
                self.question_asker(dict(args), tool_call.id),
                interrupted={"answer": "", "error": _INTERRUPTED},
                nobody={"answer": "", "error": _NO_RESPONSE},
            )
        status = "ok" if (result.get("answer") or result.get("answers")) else "denied"
        self._store_proposal_result(tool_call, result, status)
        yield self._close_proposal(tool_call, result, status)
