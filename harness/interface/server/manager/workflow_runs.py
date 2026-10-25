"""A workflow that runs in the background, as a child session.

A `workflow` call used to hold the parent's turn for the whole script: up to
`workflow_timeout` (10 min), the conversation frozen, its 24 agents bounded by one tool
call's clock. Elsewhere dynamic workflows run beside the session instead. Here a
background run is a CHILD SESSION whose one turn executes the call the parent already
approved, so it is everything a background `task` is, for free: the parent's tool call
returns at once, the child's `agent()` children are real sessions under it, their asks
reach the parent's dock, Stop walks the tree, the background clock bounds it, its spend
reaches the parent's budget, and its result is injected into the parent when it ends.

The child runs no model. Its provider (`RunnerProvider`) answers the first request with
the `workflow` call and the second with the call's own result text, so the turn costs no
tokens and its transcript reads as what happened: the request, the call, the result.

The runner's agent (`RunnerAgent`) is built here and never registered: no `task` call can
name it (the type is unknown to `resolve_agents`), and a `task_id` that names a runner is
refused for the same reason. That matters, because the runner's ruleset allows
`workflow` without asking — the decision was the parent's turn's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from harness.llm.providers.base import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools.task_agents import ALLOW, DENY, MODE_SUBAGENT, AgentInfo
from harness.tools.task import render_output
from harness.tools.workflow import WORKFLOW_RUNNER, WORKFLOW_STARTED

#: How long a background script may run when the background clock is off: the bound the
#: config clamp gives `workflow_timeout` itself.
UNCLOCKED_SCRIPT_SECONDS = 3600.0


class RunnerProvider(ProviderClient):
    """The runner's "model": the approved call, then its result, and nothing else."""

    def __init__(self, arguments: dict[str, Any]) -> None:
        self.arguments = dict(arguments)

    def complete(self, *, model: str, messages: list[dict[str, Any]], tools=None, **settings: Any) -> AssistantTurn:
        result = next(
            (
                str(message.get("content") or "")
                for message in reversed(messages)
                if message.get("role") == "tool"
            ),
            None,
        )
        if result is None:
            return AssistantTurn(
                tool_calls=[ToolCall(id="workflow", name="workflow", arguments=self.arguments)],
                finish_reason="tool_calls",
            )
        return AssistantTurn(text=result, finish_reason="stop")

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities()


@dataclass
class RunnerAgent(AgentInfo):
    """The runner's definition, carrying the three things `_run_child_turn` asks a
    definition for: the provider it runs on, the engine knobs it sets after the build, and
    how its result reads to the parent (its children's files and spend, not its own)."""

    provider_client: Optional[ProviderClient] = None
    prepare_engine: Optional[Callable[[Any], None]] = None
    settle_result: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None


class WorkflowRunsMixin:
    async def spawn_workflow(
        self,
        *,
        parent_id: str,
        call_id: str,
        arguments: dict[str, Any],
        on_metadata: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        """Start an APPROVED workflow call as a background child of `parent_id`.

        Returns the `task` tool's running shape (`{task_id, status: "running", report}`),
        or `{"error": …}` when the spawn is refused (the parent's fan-out or concurrency
        cap). `call_id` is the parent's `workflow` call: Stop and a dead run find the
        runner by it, like any child a program spawned.
        """
        meta = arguments.get("meta") if isinstance(arguments.get("meta"), dict) else {}
        name = str(meta.get("name") or "workflow")
        runner = self._workflow_runner(parent_id, {k: v for k, v in arguments.items() if k != "background"})
        out = await self.spawn_subagent(
            parent_id=parent_id,
            description=f"{name} workflow",
            prompt=f"Run the {name} workflow the conversation started.",
            agent_name=WORKFLOW_RUNNER,
            agent_info=runner,
            background=True,
            origin=call_id,
            on_metadata=on_metadata,
        )
        if out.get("status") == "running" and out.get("task_id"):
            # The workflow's own notice: what arrives, under which id, and that the
            # script's progress is beside the conversation (the `task` one speaks of a task).
            out = {
                **out,
                "report": render_output(
                    session_id=str(out["task_id"]),
                    state="running",
                    summary=f"The {name} workflow is running in the background",
                    text=WORKFLOW_STARTED,
                ),
            }
        return out

    def _delegator_engine(self, parent_id: Optional[str], parent_engine: Optional[Any]) -> Optional[Any]:
        """The engine whose tools, settings and thinking level a child inherits.

        A child copies its parent's registry. A background workflow's `agent()` children have
        the RUNNER as their parent, and the runner's ruleset leaves it two tools, `workflow`
        and `task`, so every agent of a background run came up without a file or shell tool
        (a real run, 2026-10-13: three reviewers met "read_file is not a registered tool" 2,
        17 and 10 times, one gave up "NOT INSPECTED", one guessed through `lsp` over 102
        messages). The work is the session that ran the workflow's to delegate, so that
        session's engine is the source, as it is for a foreground run, whose agents the
        parent's own `task` spawns. With that engine gone, None: the fixed fallback set,
        which has the file tools.
        """
        record = self.session_store.load(parent_id) if parent_id else None
        if record is None or getattr(record, "agent", "") != WORKFLOW_RUNNER:
            return parent_engine
        return self._engines.get(record.parent_session or "")

    def _workflow_runner(self, parent_id: str, arguments: dict[str, Any]) -> RunnerAgent:
        minutes = self._background_timeout_minutes(parent_id)
        parent_log = self.session_log(parent_id)

        def prepare(engine: Any) -> None:
            # A background script is bounded by the background clock (which stops the whole
            # tree), not by the foreground call's `workflow_timeout`. Inside the runner the
            # script runs in the call itself — it IS the background run — which is what a
            # child engine does anyway: only a root session is given the background door.
            options = dict(getattr(engine, "workflow_options", None) or {})
            options["timeout"] = minutes * 60 if minutes > 0 else UNCLOCKED_SCRIPT_SECONDS
            engine.workflow_options = options
            # The call it runs must be a tool it holds, whatever the parent's registry
            # carried (a spawn with no live parent builds the fixed fallback set).
            if engine.registry.get("workflow") is None:
                from harness.tools.workflow import workflow_tool

                engine.registry.register(workflow_tool())
            # The journal stays with the parent: a `resume` of this script finds the agents
            # a previous run (foreground or another runner) already paid for.
            engine.workflow_journal_log = parent_log

        return RunnerAgent(
            name=WORKFLOW_RUNNER,
            description="Runs one approved workflow script in the background.",
            mode=MODE_SUBAGENT,
            # Everything denied but the two tools a script is: the call itself and the
            # `task` its agent() helpers make. `allow` never reaches an approver — the
            # parent's turn authorized this call, and where a workflow asks, it asked there.
            permission=[("*", "*", DENY), ("workflow", "*", ALLOW), ("task", "*", ALLOW)],
            hidden=True,
            native=True,
            inject_agents=False,
            provider_client=RunnerProvider(arguments),
            prepare_engine=prepare,
            settle_result=self._settle_workflow_result,
        )

    def _settle_workflow_result(self, runner_id: str, result: dict[str, Any]) -> dict[str, Any]:
        """The runner's result as the parent should read it: what its agents changed and
        what they spent. The runner itself edits nothing and costs nothing; its children
        do both, in their own sessions."""
        from harness.orchestration.child_changes import changed_files
        from .session_roots import _usage_total

        transcripts: list[dict[str, Any]] = []
        tokens = 0
        for child_id in self.children_of(runner_id):
            record = self.session_store.load(child_id)
            if record is None:
                continue
            tokens += _usage_total(list(record.messages))
            transcripts.extend(record.messages)
        # One fold over every agent's receipts: a file two agents touched is one entry
        # with both counts, its kind the first agent's (`changed_files`' own rule).
        return {**result, "changed_files": changed_files(transcripts), "tokens": tokens}


__all__ = ["RunnerAgent", "RunnerProvider", "WorkflowRunsMixin"]
