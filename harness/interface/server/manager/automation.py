"""AutomationMixin: building and approving the engine of a scheduled task's run."""

from __future__ import annotations

from pathlib import Path

from harness.compose import build_engine
from harness.loop import ApprovalOutcome, TurnEngine
from harness.modes.agents import get_agent
from harness.safety.permissions import WRITE_TOOLS, Mode

from .parked import approval_body


class AutomationMixin:
    def _scheduled_approver(self, task, session_id: str):
        name_allowed = task.name_allowed_tools()

        async def approver(request):
            # Nobody is watching a scheduled run. Writes of the deliverable (scoped to the task's workspace) and tools
            # the task allows BY NAME (legacy entries) are allowed. A rule bound to a target
            # never reaches here, because the permission engine matched it already.
            if request.tool_name in WRITE_TOOLS or request.tool_name in name_allowed:
                return ApprovalOutcome.ONCE
            # Anything else is parked and suspends the run, so an automation with no grants
            # still works and simply asks. The item carries the task binding, so its card can
            # offer "Allow every time".
            item = self.inbox.add_approval(
                session_id,
                f"Run `{request.tool_name}`?",
                body=approval_body(request),
                tool_call_id=getattr(request, "tool_call_id", None),
                data=self.approval_prompt_data(session_id, request),
            )
            if item.state == "pending":
                self.persist_session(session_id)
            return self.approval_outcome(await self.inbox.wait(item.id), request, session_id)

        return approver

    def _seed_task_permissions(self, engine: TurnEngine, task) -> None:
        """Apply a task's standing allowances to an engine. Rules bound to a target feed the
        permission engine's matcher (connector tools included, since the target binding is the
        safety), and the older name-only entries keep their session-allowlist behaviour."""
        engine.permissions.task_rules = task.standing_rules()
        for tool in task.name_allowed_tools():
            engine.permissions.allow_tool_for_session(tool)

    def _build_task_engine(self, task, *, session_id: str) -> TurnEngine:
        Path(task.workspace).mkdir(parents=True, exist_ok=True)
        engine = build_engine(
            agent=get_agent(task.agent),
            workspace=task.workspace,
            model=task.model or self.model,
            # The presets the composer offers, stored on the task. None means the unattended
            # default: workspace-write with the inbox approver.
            mode=Mode(task.mode) if task.mode else Mode.WORKSPACE_WRITE,
            approver=self._scheduled_approver(task, session_id),
            provider=self.provider,
            # The language-server pool every session shares, so a scheduled run does not start a
            # second pyright for a workspace that already has one.
            lsp_manager=self.lsp,
            # A callable and not a snapshot, so editing the instructions in Settings reaches
            # conversations that are already open.
            user_rules=lambda: self.memory_settings.prompt_rules(),
            secrets=self.secrets,
            # No scheduling tools inside a scheduled run. Its job is to DO the task, and
            # instructions that mention timing ("every day at 5:32pm…") would otherwise tempt it
            # to create another automation instead.
            task_store=None,
            # A scheduled run with MCP servers pays the same fixed tool payload as an
            # interactive one, and being long, unattended and tool-heavy it is the shape the
            # discovery setting exists for.
            tool_discovery=self.tool_discovery(),
            visuals=self.visuals(),
            session_id=session_id,
            audit_sink=self.audit_store.append,
            # A scheduled run respects the same per-session connection hierarchy as a live one:
            # only the tools of the connectors the persona has switched on are exposed.
            connector_filter=self.effective_connectors(session_id),
            skill_filter=lambda sid=session_id, w=task.workspace, a=task.agent: (
                self.effective_skill_names(sid, w, agent=a)
            ),
        )
        self._seed_task_permissions(engine, task)
        return engine
