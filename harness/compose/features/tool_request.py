"""`request_tool`: asking for a missing command-line tool instead of dropping the check."""

from __future__ import annotations

from harness.tools.toolreq import request_tool_tool

from .base import FeatureContext


class ToolRequest:
    id = "tool_request"
    # Off until `[features] add` names it. Measured on the session store (2026-10-24): 0 calls in 860
    # sessions, and no session ran gitleaks, trivy or osv-scanner at all, while its schema was ~295
    # tokens of every request. A missing scanner can still be installed through the shell, which asks
    # like any command.
    opt_in = True

    def setup(self, ctx: FeatureContext) -> None:
        # Anything with a shell can hit a missing command-line tool (a scanner, aws, kubectl). It is
        # given a way to ask and not to silently drop the check that needed it.
        if ctx.executor is not None:
            ctx.registry.register(request_tool_tool())
