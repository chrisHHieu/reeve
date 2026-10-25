"""InboxHandlersMixin: the prompt handlers of a run that has no live socket.

A background turn, a self-wake or a durable resume cannot ask over a WebSocket, so each of its
questions is parked as an item and the agent waits on it; the person answers it when they open
the conversation. After a restart the item already exists and is resolved, so waiting returns
at once.
"""

from __future__ import annotations

from typing import Any, Optional

from harness.tools.ask import answer_result, question_item_fields

from .parked import approval_body, parse_resolution


class InboxHandlersMixin:
    async def _park(self, session_id: str, item) -> str:
        """Save the session, so the pending tool call is on disk beside its parked item, and
        wait for the answer."""
        if item.state == "pending":
            self.persist_session(session_id)
        return await self.inbox.wait(item.id)

    def inbox_question_asker(self, session_id: str, agent: str):
        """The `ask_user` handler of a run with no socket: the question is parked and the run
        suspends until a person answers it in the conversation."""

        async def ask(args: dict[str, Any], tool_call_id: Optional[str] = None) -> dict[str, Any]:
            fields = question_item_fields(args)
            if fields is None:
                return {"answer": "", "error": "no question"}
            item = self.inbox.add_question(
                session_id,
                tool_call_id=tool_call_id,
                **fields,
            )
            if item.state != "pending":  # a resume re-raised a prompt that was already answered
                return answer_result(item.questions, item.resolution)
            return answer_result(item.questions, await self._park(session_id, item))

        return ask

    def inbox_approver(self, session_id: str, agent: str):
        """The approver of a run with no socket."""

        async def approve(request):
            item = self.inbox.add_approval(
                session_id,
                f"Run `{request.tool_name}`?",
                body=approval_body(request),
                tool_call_id=getattr(request, "tool_call_id", None),
                data=self.approval_prompt_data(session_id, request),
            )
            return self.approval_outcome(await self._park(session_id, item), request, session_id)

        return approve

    def inbox_plan_approver(self, session_id: str, agent: str):
        async def approve(args, tool_call_id=None):
            item = self.inbox.add_plan(
                session_id,
                "Approve the plan?",
                body=str(args.get("plan", "")),
                tool_call_id=tool_call_id,
            )
            reply = parse_resolution(await self._park(session_id, item))
            if not reply.get("approved"):
                return {"approved": False, "feedback": reply.get("feedback") or "the user rejected the plan"}
            return {"approved": True, "mode": reply.get("mode") or "interactive"}

        return approve

    def persist_session(self, session_id: str) -> None:
        """Save the cached engine's thread, so a prompt's pending tool call survives a crash."""
        engine = self._engines.get(session_id)
        if engine is not None:
            self.save(session_id, engine)

    async def resolve_inbox(self, item_id: str, resolution: str) -> bool:
        """Resolve an inbox item from any surface (REST, a Slack button, a channel reply).

        If the asking agent is still suspended, its await takes the answer. Otherwise the
        process restarted, or the engine was evicted, while it was blocked: rebuild the engine
        from the saved thread and continue the turn."""
        item = self.inbox.get(item_id)
        resolved = self.inbox.resolve(item_id, resolution)
        if not resolved or item is None:
            return resolved
        if not self.is_running(item.session_id):
            await self._durable_resume(item)
        return resolved

    async def _durable_resume(self, item) -> None:
        if not getattr(item, "tool_call_id", None):
            return  # a legacy item: nothing to reconstruct, so it is left as it is
        mcp_tools = await self.arm_mcp(item.session_id)
        engine = self.get_engine(item.session_id, extra_tools=mcp_tools)
        if engine is None or not hasattr(engine, "resume"):
            return
        self.mark_running(item.session_id)
        try:
            async for _event in engine.resume():
                pass
            self.save(item.session_id, engine)
        finally:
            self.mark_idle(item.session_id)
