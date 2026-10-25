"""Acting when no one is typing: scheduled tasks, self-wake and goal rounds."""

from __future__ import annotations

from harness.integrations.automation import scheduling_tools
from harness.loop.selfwake import selfwake_tools

from ..session_helpers import verify_agent_enabled
from .base import FeatureContext


class Continuity:
    id = "continuity"

    def setup(self, ctx: FeatureContext) -> None:
        """Only the agents that opt in with the `scheduling` trait get these."""
        inputs, agent, registry = ctx.inputs, ctx.agent, ctx.registry
        session_id = inputs.session_id
        if not agent.scheduling:
            return
        # Scheduling: an opted-in agent with a workspace can set up scheduled tasks, with this
        # session as the origin.
        if inputs.task_store is not None and ctx.ws is not None:
            origin = {
                "surface": agent.name,
                "session_id": session_id or "",
                "workspace": str(ctx.ws),
                "agent": agent.name,
            }
            registry.register_all(
                scheduling_tools(inputs.task_store, origin=origin, default_workspace=str(ctx.ws)),
                session_bound=True,
            )
        # Self-wake: suspend and schedule its own resumption (on a timer, on completion, on an
        # event), and the scheduler tick resumes due wakes. `depth_of` reads the chain depth of the
        # turn making the call, so the wake system can refuse to extend a chain going nowhere. It is
        # a callable and not a number because this engine is built once and cached while the depth
        # changes every turn, the shape `auto_memory_enabled` uses.
        if inputs.wake_store is not None and session_id:
            registry.register_all(
                selfwake_tools(inputs.wake_store, session_id, depth_of=lambda: ctx.engine_of().wake_depth),
                session_bound=True,
            )
        # Goal rounds: arm an objective the runtime continues on its own while the session is idle
        # (`goal`), in place of parking on a timer. The tool only records state, and the manager's
        # driver opens the rounds, so the round prompt, the cap and the idle-only rule live in one
        # place.
        if inputs.goal_store is not None and session_id:
            from harness.loop.goals import goal_tools

            config = ctx.config
            registry.register_all(
                goal_tools(
                    inputs.goal_store,
                    # The round this turn is executing (0 outside a goal round), read per call like
                    # `depth_of` above, because the tool outlives any one turn.
                    round_of=lambda: getattr(ctx.engine_of(), "goal_round", 0),
                    # The session's resolved `[goal] max_rounds`, so `set` without a cap lands on the
                    # configured default.
                    default_max_rounds=config.goal_max_rounds,
                    # `done` reads its proof from the live transcript (completion_gaps).
                    messages_of=lambda: ctx.engine_of().messages,
                    # A check-less goal needs an independent verdict only where one can be asked
                    # for: the task tool is here and `verify` is not switched off.
                    verifier_of=lambda: registry.get("task") is not None and verify_agent_enabled(config),
                    # The session's scratch folder is not the work a check judges.
                    exempt_of=ctx.edit_exempt_roots,
                    # Whose turn this is: what the goal is and whether it runs are the
                    # person's to decide. A self-wake carries no source of its own, so its chain depth
                    # is what marks it as the session's own turn.
                    person_turn_of=lambda: bool(getattr(ctx.engine_of(), "person_turn", False))
                    and not getattr(ctx.engine_of(), "wake_depth", 0),
                ),
                session_bound=True,
            )
