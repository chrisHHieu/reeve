"""Planning: `propose_plan` (the plan-mode exit door) and `update_plan` (the working plan file)."""

from __future__ import annotations

from harness.tools.plan import propose_plan_tool, update_plan_tool

from .base import FeatureContext


class Planning:
    id = "planning"

    def setup(self, ctx: FeatureContext) -> None:
        # The plan overlay can flip mid-session, and the engine rejects the call under a bare
        # read-only preset.
        ctx.registry.register(propose_plan_tool())
        # The working plan file rides with `propose_plan`, but only where the session has a place to
        # write it, so the tool is never advertised just to refuse.
        if ctx.plan_path is not None:
            ctx.registry.register(update_plan_tool())
