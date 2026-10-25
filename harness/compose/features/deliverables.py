"""Deliverables: `present` and `show_visual`."""

from __future__ import annotations

from harness.tools.present import present_tools
from harness.tools.visual import visual_tools

from .base import FeatureContext


class Deliverables:
    id = "deliverables"

    def setup(self, ctx: FeatureContext) -> None:
        # `present`: the model declares the files the person should open, and the GUI stacks a
        # delivery row under the turn from the card it leaves behind. Every local session with a
        # workspace has deliverables, and each gets the same declaration. A remote session gets
        # nothing, since the tool resolves this machine's paths and would validate the controller's
        # disk while the deliverable lives on the other host (`file_writers` refuses for the same
        # reason).
        if ctx.ws is None or ctx.remote:
            return
        ctx.registry.register_all(present_tools(str(ctx.ws), roots=ctx.root_list or None))
        # `show_visual`: a chart, table, figures, comparison, calculator or form drawn
        # under the answer. Local for the same reason as `present`: a block may read a file here.
        # The person's Settings > Visuals choice says when it is offered, or that it is not.
        ctx.registry.register_all(
            visual_tools(str(ctx.ws), roots=ctx.root_list or None, mode=ctx.inputs.visuals)
        )
