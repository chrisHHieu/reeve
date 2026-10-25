"""The questions the engine puts to the person, as one session socket answers them.

Approval, question, folder, plan and tool-install prompts are all parked as
inbox items and awaited there, so each survives a dropped socket (it is redelivered on
reconnect) and can be answered again after a restart. A session with a window shows its prompt
in that window (`visibility` inline); the agent stays blocked until the item is resolved.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from fastapi import WebSocket

from harness.integrations.inbox import VIS_INLINE
from harness.loop import ApprovalOutcome
from harness.tools import toolchain

from ..manager.parked import approval_body, parse_resolution


class SessionPrompts:
    def __init__(self, manager, ws: WebSocket, session_id: str, agent: str) -> None:
        self.manager = manager
        self.ws = ws
        self.session_id = session_id
        self.agent = agent

    # -- where a prompt goes and how it waits ------------------------------------------

    def placement(self) -> dict[str, str]:
        return {"visibility": VIS_INLINE}

    async def _raise(self, item, *, inline: Optional[Callable[[], Awaitable[None]]] = None) -> None:
        """Make a freshly parked item visible: save the session so the pending call is on
        disk, then hand it to `inline`. An item that was already pending (a durable resume
        re-raising it) is left alone."""
        if item.state != "pending":
            return
        self.manager.persist_session(self.session_id)
        if inline is not None:
            await inline()

    async def _answer(self, item, *, inline: Optional[Callable[[], Awaitable[None]]] = None) -> str:
        await self._raise(item, inline=inline)
        return await self.manager.inbox.wait(item.id)

    async def _structured(self, item) -> dict[str, Any]:
        return parse_resolution(await self._answer(item))

    def resolve_pending(self, resolution: str) -> None:
        """A live reply settles the session's single pending prompt (the agent blocks on one
        at a time). Reconnects and the Inbox resolve by item id over REST instead."""
        pending = self.manager.inbox.pending(self.session_id)
        if pending:
            self.manager.inbox.resolve(pending[0].id, resolution)

    # -- tool approval -----------------------------------------------------------------

    async def approver(self, request) -> ApprovalOutcome:
        # The engine has already emitted PERMISSION_REQUIRED for the live inline card. Parking
        # the item lets the answer also arrive from the Inbox, a reconnect or after a restart.
        item = self.manager.inbox.add_approval(
            self.session_id,
            f"Run `{request.tool_name}`?",
            body=approval_body(request),
            **self.placement(),
            # Set when the run belongs to an automation, so the card can offer the
            # task-persistent "Allow every time". Empty elsewhere.
            data=self.manager.approval_prompt_data(self.session_id, request),
            tool_call_id=getattr(request, "tool_call_id", None),
        )
        resolution = await self._answer(item)
        # Both vocabularies arrive here: the live card says once / always_tool /
        # always_command / always_task / deny; the Inbox and channels say allow / always / deny.
        return self.manager.approval_outcome(resolution, request, self.session_id)

    # -- ask_user ----------------------------------------------------------------------

    async def question_asker(self, args: dict, tool_call_id=None) -> dict:
        from harness.tools.ask import answer_result, question_item_fields

        fields = question_item_fields(args)
        if fields is None:  # the engine checks too
            return {"answer": "", "error": "no question"}
        item = self.manager.inbox.add_question(
            self.session_id, **self.placement(), tool_call_id=tool_call_id, **fields
        )

        async def show_inline() -> None:
            # The engine does not emit this event for ask_user; the socket does, and only when
            # the session is attended.
            await self.ws.send_json(
                {
                    "type": "question_requested",
                    "data": {
                        "question": item.title,
                        "options": item.options,
                        "allow_text": item.allow_text,
                        "multi": item.multi,
                        "header": item.header,
                        "questions": item.questions,
                    },
                }
            )

        return answer_result(item.questions, await self._answer(item, inline=show_inline))

    # -- installing a tool -------------------------------------------------------------

    async def tool_requester(self, args: dict, tool_call_id=None) -> dict:
        """Ask before installing a managed tool, then install the PINNED build if approved.

        Declining is an ordinary outcome: the agent is told to fall back and say what it could
        not check. An approval consents to installing that one artifact, with its digest
        verified, and never to fetching whatever a prompt asked for.
        """
        name = str(args.get("name", "")).strip()
        info = toolchain.describe(name)
        if not info:
            # A tool outside the pinned catalog never gets an install card that could only end
            # in "no pinned build" after the person approved. The shell has its own approval.
            return {
                "installed": False,
                "error": (
                    f"'{name}' is not in the pinned tool catalog ({', '.join(sorted(toolchain.MANAGED))}). "
                    "Install it yourself with the shell (brew/pip/…, subject to the normal command "
                    "approval), or proceed without it and note the gap."
                ),
            }
        item = self.manager.inbox.add_tool_request(
            self.session_id,
            f"Install {name}?" if name else "Install a tool?",
            body=str(args.get("reason", "")),
            **self.placement(),
            data={
                "tool": name,
                "installable": True,
                "version": info.get("version", ""),
                "summary": info.get("summary", ""),
                "url": info.get("url", ""),
                "source": info.get("source", ""),
            },
            tool_call_id=tool_call_id,
        )
        if not (await self._structured(item)).get("approved"):
            return {"installed": False, "reason": "the user declined to install it"}
        try:
            path = await asyncio.to_thread(toolchain.install, name)
        except Exception as exc:  # noqa: BLE001 - the agent reads the reason as it is
            return {"installed": False, "error": str(exc)}
        return {"installed": True, "path": path, "version": info["version"]}

    # -- plans ---------------------------------------------------------------------------

    async def plan_approver(self, args: dict, tool_call_id=None) -> dict:
        # The engine has already emitted PLAN_PROPOSED.
        item = self.manager.inbox.add_plan(
            self.session_id,
            "Approve the plan?",
            body=str(args.get("plan", "")),
            **self.placement(),
            tool_call_id=tool_call_id,
        )
        reply = await self._structured(item)  # {approved, mode, feedback}
        if not reply.get("approved"):
            return {"approved": False, "feedback": reply.get("feedback") or "the user rejected the plan"}
        # A mode is named only when the person picked one. Otherwise the session keeps its own,
        # so approving a plan inside a bypass session does not quietly drop it to ask-per-action.
        chosen = reply.get("mode")
        return {"approved": True, "mode": chosen} if chosen else {"approved": True}
