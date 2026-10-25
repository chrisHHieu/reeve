"""Subagents: `task` and the tools that talk to a running child."""

from __future__ import annotations

import threading

from harness.tools.task import task_tools

from .base import FeatureContext


class Delegation:
    id = "delegation"

    def setup(self, ctx: FeatureContext) -> None:
        """An agent with subagents can fan broad research out to read-only explorer subagents and
        keep its own context for the actual change.

        `subagent_depth = 0` disables delegation entirely. The tool is not registered, so the model
        never proposes it, the same gate shape `agent.subagents` has always been. `task_tools` still
        refuses a direct call with the depth message, for hosts that register it by hand."""
        inputs, config = ctx.inputs, ctx.config
        if not (ctx.agent.subagents and ctx.ws is not None and config.subagent_depth > 0):
            return
        # The stop flag is the only channel into the child's worker thread: an interrupt sets it and
        # the tool's watcher sees it.
        stop = threading.Event()
        ctx.on_interrupt(stop.set)
        ctx.registry.register_all(
            task_tools(
                workspace=ctx.ws,
                provider=ctx.provider,
                model=ctx.model,
                model_settings=ctx.model_settings,
                stop_flag=stop,
                undo=ctx.undo,
                secrets=ctx.secrets,
                spill=ctx.spill,
                # The agents this session's config declares, on top of the two built-ins (`general`,
                # `explore`).
                config=config,
                caller_agent=ctx.agent.name,
                # A background child's completion lands in the conversation: the wake's note is the
                # finished `<task>` envelope, so nobody has to poll.
                wake_store=inputs.wake_store,
                session_id=inputs.session_id,
                depth_of=lambda: ctx.engine_of().wake_depth,
                # The ancestry depth the depth limit reads, which is walked at call time. None on
                # hosts that own no parent links.
                session_depth=inputs.session_depth,
                # The @-mention bypass: the turn's mentions ride the engine (`run(mentions=…)`), and
                # the tool reads them per call.
                mention_bypass=lambda: bool(getattr(ctx.engine_of(), "turn_mentions", None)),
                # The person's step budget for children (None or 0 is uncapped).
                steps=config.subagent_steps or None,
                approver=inputs.approver,
                # The manager's spawn path, when this engine belongs to a SessionManager (step B
                # of child sessions). With it a fresh child is a real session and `task_id` resumes from that
                # session's store. Without it, the thread harness remains the only path.
                spawner=inputs.subagent_spawner,
                # The live-metadata channel for the parent's running `task` row (step C).
                # The tool emits `tool_metadata` frames through it while the spawned child works.
                subagent_metadata=inputs.subagent_metadata,
                # The spawn ask (P1): when the ruleset says `ask` for this agent type, the tool parks
                # on the broker before creating the child session.
                permission_asker=inputs.permission_asker,
                # May a delegation call pick its own route? Off hides the parameters and the
                # discovery tool entirely.
                model_selection=inputs.model_selection,
            )
        )
