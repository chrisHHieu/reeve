"""`ask_user`: the question tool the engine intercepts and puts to the person."""

from __future__ import annotations

from harness.tools.ask import ask_user_tool

from .base import FeatureContext


class AskUser:
    id = "ask_user"

    def setup(self, ctx: FeatureContext) -> None:
        # Only where something can answer: a host that passes no question asker would park the call
        # on nobody.
        if ctx.inputs.question_asker is not None:
            ctx.registry.register(ask_user_tool())
