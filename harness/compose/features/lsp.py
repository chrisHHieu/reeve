"""Language servers: the `lsp` tool, and the pool the post-edit hook tells about changed files."""

from __future__ import annotations

from .base import FeatureContext


class Lsp:
    id = "lsp"

    def setup(self, ctx: FeatureContext) -> None:
        # Language servers (`lsp`): definition, references, hover, symbols and diagnostics answered by
        # a real LSP process. They are registered for every local workspace, because semantic
        # navigation is a read capability and not a persona trait, and `[lsp] enabled = false`
        # removes them entirely. A remote session gets nothing: the servers are subprocesses of this
        # machine, and its workspace is a POSIX path on another host.
        if ctx.ws is None:
            return
        from harness.integrations.lsp.servers import lsp_enabled

        if not lsp_enabled(ctx.config):
            return
        from harness.integrations.lsp import default_manager, lsp_tools

        # It is kept on the engine so the write tools' post-edit hook can tell live servers a file
        # changed on disk. pyright does not notice a new `pyrightconfig.json` by itself, and a stale
        # config means stale diagnostics for the rest of the session.
        manager = ctx.inputs.lsp_manager or default_manager()
        ctx.provide("lsp_manager", manager)
        ctx.registry.register_all(lsp_tools(str(ctx.ws), roots=ctx.root_list or None, manager=manager))
