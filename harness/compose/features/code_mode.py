"""Programs over the agent's own tools: `run_code`, and `workflow` where subagents exist."""

from __future__ import annotations

from .base import FeatureContext


class CodeMode:
    id = "code_mode"

    def setup(self, ctx: FeatureContext) -> None:
        """`run_code` names no tools: what a program may call is the registry at the moment
        it runs, and a program naming a tool that is not there is refused before it starts
        (`tools/code_names.py`). `workflow` rides the same runtime and is offered only where
        delegation exists at all, since `subagent_depth = 0` removes `task` and a helper with no
        subagents to start would fail every script; so this feature runs after `delegation`."""
        if "code_mode" not in ctx.agent.capabilities:
            return
        from harness.tools.code_mode import run_code_tool

        ctx.registry.register(run_code_tool())
        if ctx.registry.get("task") is not None:
            from harness.tools.workflow import workflow_tool

            ctx.registry.register(workflow_tool())
