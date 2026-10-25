"""`build_engine`: a TurnEngine put together from an Agent and the session around it.

The core comes first: the ground the session stands on (`workspace`), its world, the agent's own
tools, the permission engine. Then the features run in order (`features/`), each
registering its tools and what else it brings; then the prompt (`instructions`), the live context
(`live_context`) and the wiring of the finished engine (`wiring`). This file only decides the order
and passes what one stage made to the next.
"""

from __future__ import annotations

import logging
from dataclasses import fields
from pathlib import Path
from typing import Any, Callable, Optional

from harness.config.config import load_config
from harness.config.overrides import RiskOverrideStore
from harness.config.roots import render_context
from harness.llm.providers import ProviderClient, ProviderRouter
from harness.llm.providers.fallback import RouteCooldown
from harness.loop import Approver, TurnEngine
from harness.modes.agents import Agent, AgentContext
from harness.safety.permissions import Mode, PermissionEngine
from harness.safety.secrets import SecretStore, state_dir
from harness.safety.workspace_trust import WorkspaceTrustStore
from harness.tools import ToolRegistry
from harness.tools.edit_surface import fit_edit_tools
from harness.tools.todo import TodoList

from .features import FeatureContext, SessionInputs, active_features
from .instructions import compose_instructions, memory_witnesses
from .live_context import make_context_provider
from .session_helpers import build_hooks
from .wiring import attach_pipeline, attach_session_state
from .workspace import (
    attach_memory_root,
    build_root_list,
    make_edit_exempt_roots,
    open_side_stores,
    open_world,
    resolve_workspace,
)

logger = logging.getLogger(__name__)


def build_engine(
    *,
    agent: Agent,
    workspace: Optional[str | Path] = None,
    # The SSH environment profile (phase 2b) whose host owns `workspace`. When set,
    # `workspace` is a remote POSIX path and the session's one world is a `RemoteWorld`; no local
    # instruction, config or memory path is read from it.
    environment: Optional[dict[str, Any]] = None,
    # The runner seam the remote world's ssh calls go through (a FakeSsh in tests, None for the real
    # system binary). It only matters with `environment`.
    environment_runner: Optional[Any] = None,
    # The password for the remote world's ssh calls (password auth): the profile's
    # remembered secret, already resolved by the caller. None keeps the phase-1 key-only path exactly
    # as it was, and a password switches ssh to the askpass helper. It is never an argv entry or a
    # profile field.
    environment_password: Optional[str] = None,
    # The LSP manager that owns language-server processes (harness/integrations/lsp/manager.py).
    # `SessionManager` passes its own so every engine on a root shares one server per language. None
    # (the CLI, direct `build_engine` callers, tests) falls back to the process-wide default, which
    # `atexit` closes.
    lsp_manager: Optional[Any] = None,
    # The still-running background subagents, as a live-context line. None without a manager.
    background_status: Optional[Callable[[], str]] = None,
    model: str = "gpt-5.6-sol",
    # Routes tried when `model` fails in a way another model can answer, as
    # `provider:model` ids in order. None reads the config's `fallback_models`.
    model_fallback: Optional[list[str]] = None,
    # The shared cooldown table. None is the process-wide default; tests inject their own.
    fallback_cooldown: Optional[RouteCooldown] = None,
    mode: Mode = Mode.WORKSPACE_WRITE,
    # The plan overlay (`/plan`): while true every consequential call is refused on top of whatever
    # preset `mode` selects. It is not a mode, and the same session toggles it without losing its
    # permission choice.
    plan: bool = False,
    # Progressive tool discovery: deferrable tools (MCP today) leave the wire for the
    # `tool_search`/`tool_describe`/`tool_call` bridge. It applies on the next build, since the tool
    # list is part of the cached prefix.
    tool_discovery: bool = False,
    # Whether a delegation call may pick its own LLM route: `task` advertises and accepts
    # `provider` and `model`, `list_subagent_models` is registered, and a CLI child may take a model
    # from its own account catalog. Manager-backed sessions pass the Settings toggle here, and the
    # CLI and direct callers get the feature on.
    model_selection: bool = True,
    # When `show_visual` is offered: "auto" (the model judges), "asked" (only when the
    # person asks to see something) or "off" (not registered). The Settings > Visuals choice;
    # it applies on the next build, since the tool and its description are in the cached prefix.
    visuals: str = "auto",
    approver: Optional[Approver] = None,
    provider: Optional[ProviderClient] = None,
    allowed_commands: Optional[list[str]] = None,
    max_iterations: Optional[int] = None,
    # Low-risk tool calls that may overlap within one step. None reads the config value
    # (`Config.max_parallel_tools`, default 10; 0 is uncapped).
    max_parallel_tools: Optional[int] = None,
    # The wall-clock ceiling on every tool call, in milliseconds. None uses
    # `tools.deadline.DEADLINE_CEILING_MS`. It is a parameter and not a read of the module constant
    # so a test can inject a small ceiling and prove the mechanism in milliseconds, not wait out a
    # 12-minute default.
    deadline_ceiling_ms: Optional[int] = None,
    model_settings: Optional[dict[str, Any]] = None,
    # A named thinking level (`low`, `medium`, `high`, …) the person picked for this session. It is
    # translated to a provider-specific body patch by `providers/variants.py` and merged under
    # `model_settings`, so an explicit setting a caller names still wins. None or "default" means the
    # provider's own default rides, with no patch at all.
    thinking_level: Optional[str] = None,
    # The person's own instructions, SOUL.md (the Settings textarea). They are injected as written at
    # the top of the prompt, above the environment and any memory, and are independent of the memory
    # on/off switch. No tool writes them. A callable is read per turn (the server passes one so a
    # Settings edit reaches conversations already open), and a plain string is a fixed value for the
    # CLI and tests.
    user_rules: Optional[Any] = None,
    # Auto memory as markdown files (`memory/auto_memory.py`), beside the row store. The object is
    # per project and machine-local, built by the manager (None in the CLI and tests means no tool and
    # no injected index). Only its MEMORY.md index rides the cached prefix, and topic files are read
    # on demand.
    auto_memory: Optional[Any] = None,
    # The live write switch for the auto-memory tool, consulted per write, so turning it off in
    # Settings applies to conversations already running.
    auto_memory_enabled: Optional[Any] = None,
    messages: Optional[list[dict[str, Any]]] = None,
    extra_tools: Optional[list[Any]] = None,
    # A pre-built registry to seed this engine's tools. A subagent child inherits the
    # parent session's registry, MCP and connector tools included, as a `ToolRegistry.filtered` copy
    # and does not compose the fixed persona set again. The copy already carries every `ToolSpec`
    # (func, schema, contract, metadata, group, policy), so `agent.build_tools` is skipped. The
    # registrations below still run (web, skills, plan, run_code), which is why the child path filters
    # the result after the build. None is the ordinary path, built from the agent definition.
    base_registry: Optional[ToolRegistry] = None,
    secrets: Optional[SecretStore] = None,
    # The session's own conversation store (item 2.5), so `search_history` can reach what compaction
    # pushed out of the model's view. It is wired like `memory_store` and not declared per persona,
    # because it is the same kind of thing, a read over the person's own past. Putting it in a
    # manifest would grow every persona's declared capabilities and force a new consent for a tool
    # that reads what the sidebar already shows.
    conversation_store: Optional[Any] = None,
    task_store: Optional[Any] = None,
    wake_store: Optional[Any] = None,
    # The manager's sync bridge into `SessionManager.spawn_subagent` (step B). When set, the
    # `task` tool spawns a real child session driven by the manager's turn driver and not a private
    # thread loop. None (the CLI, direct callers, tests) keeps the thread path exactly as it was.
    subagent_spawner: Optional[Any] = None,
    # The session's ancestry depth, counted by the host that owns the parent links
    # (`SessionManager.session_depth`). The chain is counted when `task` is called, so this is a
    # callable and not a number: a session may gain children as it works. None (the CLI, direct
    # callers) means root.
    session_depth: Optional[Any] = None,
    # The manager's sync emitter for the parent session's `tool_metadata` frames while a spawned child
    # runs (step C). It is passed down like `subagent_spawner`: only a host that owns a
    # parent session and a socket can emit, so None changes nothing.
    subagent_metadata: Optional[Any] = None,
    # Stops the children a dead program spawned (`agent()` inside a workflow), keyed by the root tool
    # call id. The manager passes `cancel_call_children`, and it is None elsewhere.
    subagent_cancel_origin: Optional[Any] = None,
    # Starts an approved `workflow` call as a background child session:
    # `(call_id, arguments, on_metadata)` returning an awaitable result. The manager passes it for a
    # root session, and None (the CLI, children, tests) runs every workflow in the call itself.
    workflow_spawner: Optional[Any] = None,
    # The manager's sync door into the permission broker for the `task` spawn ask (P1). It is called
    # only when the ruleset's action for `task.<subagent_type>` is `ask`, and None (the CLI, the
    # thread path) keeps spawning without a prompt.
    permission_asker: Optional[Any] = None,
    # The session's goal record (`harness/loop/goals.py`): a durable objective the runtime continues
    # on its own, one round per idle moment, up to the cap. The manager owns the driver and the
    # engine only carries the model's controls. It is gated on the same `scheduling` trait as
    # self-wake, because autonomous continuity is the same capability.
    goal_store: Optional[Any] = None,
    session_id: Optional[str] = None,
    audit_sink: Optional[Any] = None,
    roots: Optional[list] = None,
    plan_approver: Optional[Any] = None,
    question_asker: Optional[Any] = None,
    tool_requester: Optional[Any] = None,
    subscription_store: Optional[Any] = None,
    channel_buffer: Optional[Any] = None,
    connector_filter: Optional[set[str]] = None,
    # A set (a snapshot) or a zero-argument callable (live, evaluated per `load_skill`).
    skill_filter: Optional[set[str] | Callable[[], set[str]]] = None,
    # Skill folders a persona carries, so its skills can be read by `load_skill`.
    extra_skill_dirs: Optional[list[str | Path]] = None,
) -> TurnEngine:
    given = dict(locals())
    inputs = SessionInputs(**{f.name: given[f.name] for f in fields(SessionInputs)})

    remote, remote_root, ws = resolve_workspace(workspace, environment, agent)
    if session_id:
        # Session affinity for providers that route by cache prefix (OpenAI's `prompt_cache_key`),
        # injected on the way into every provider call so the engine stays provider-agnostic. Other
        # providers drop it, and without a session id (CLI, TUI, one-off) there is nothing to key on.
        from harness.llm.providers.base import CACHE_AFFINITY

        model_settings = {**(model_settings or {}), CACHE_AFFINITY: session_id}

    root_list = build_root_list(remote, remote_root, roots, ws)
    memory_root = attach_memory_root(auto_memory, remote, root_list)
    config = load_config(ws, workspace_trusted=bool(ws and WorkspaceTrustStore().is_trusted(ws)))
    stores = open_side_stores(remote, root_list, ws, session_id)
    world, shell_env = open_world(
        remote=remote,
        remote_root=remote_root,
        ws=ws,
        root_list=root_list,
        spill=stores.spill,
        session_id=session_id,
        environment=environment,
        environment_runner=environment_runner,
        environment_password=environment_password,
    )
    executor = world.executor if world is not None else None
    # Persistent terminals (tools/terminal.py) share the managed environment of `run_shell`, so no
    # second shell has different facts in it.
    terminals = world.terminals if world is not None else None
    todo = TodoList()
    context = AgentContext(
        workspace=ws, executor=executor, todo=todo, terminals=terminals, roots=root_list or None, world=world
    )

    registry = _start_registry(agent, context, base_registry, extra_tools)
    # The edit tools follow the model: `apply_patch` is offered only to the families
    # trained on its envelope. A model switch fits them again (`sync_edit_surface`), so this is core
    # and not a feature that could be left out.
    held_edit_tools = fit_edit_tools(registry, model, {})
    secrets = secrets or SecretStore()
    # Route by the model's `provider:` prefix (OpenAI by default, Ollama, …). The manager normally
    # passes its shared router, and this fallback covers the TUI and direct `build_engine()` callers.
    provider = provider or ProviderRouter(secrets, default_provider="openai")
    # The person's local risk overrides, mainly to relax MCP's conservative default. An empty store
    # does nothing, and agent loading never writes it (the no-self-grant rule).
    risk_overrides = RiskOverrideStore(state_dir() / "risk_overrides.json").resolver()
    permissions = PermissionEngine(
        # The remote anchor is a POSIX string. The PermissionEngine and Boundary resolve it to the
        # controller's spelling consistently, roots and candidates alike, and the RemoteFileBackend is
        # the real containment enforcer on the other side.
        workspace_root=remote_root if remote else (ws or (root_list[0].path if root_list else Path.cwd())),
        mode=mode,
        plan=plan,
        # `[]` is an explicit deny-by-default override and not a request to fall back to config.
        allowed_commands=allowed_commands if allowed_commands is not None else config.allowed_commands,
        auto_allow_tools=set(config.auto_allow),
        allowed_domains=list(config.allowed_domains),
        roots=root_list or None,
        risk_overrides=risk_overrides,
        memory_dirs=[memory_root] if memory_root is not None else [],
        # A read-only command is judged in the folder the shell stands in.
        shell_cwd=(lambda: getattr(executor, "cwd", None)) if executor is not None else None,
    )

    ctx = FeatureContext(
        inputs=inputs, agent=agent, config=config, registry=registry, permissions=permissions,
        secrets=secrets, provider=provider, model=model, model_settings=model_settings, world=world,
        executor=executor, context=context, todo=todo, root_list=root_list, ws=ws, remote=remote,
        remote_root=remote_root, memory_root=memory_root, spill=stores.spill, undo=stores.undo,
        intents=stores.intents, plan_path=stores.plan_path, witnesses=None,
        edit_exempt_roots=make_edit_exempt_roots(root_list, ws), held_edit_tools=held_edit_tools,
    )
    # Read when a memory is written, from the engine that exists by then.
    ctx.witnesses = memory_witnesses(session_id, ctx.engine_of)
    # The features, in order: each registers its tools and what else it brings.
    for feature in active_features(config):
        feature.setup(ctx)

    instructions = compose_instructions(
        agent=agent, model=model, user_rules=user_rules, remote=remote, environment=environment,
        remote_root=remote_root, ws=ws, shell_env=shell_env, has_shell=executor is not None,
        sections=ctx.render_prompt_sections(),
        tools=registry.names(),
    )
    # A remote session's roots are POSIX dicts and the static environment block already names the
    # anchor. `render_context` is the local-root renderer and stays local.
    roots_context = (lambda: render_context(root_list)) if root_list and not remote else None
    context_provider, context_parts = make_context_provider(
        permissions=permissions, roots_context=roots_context, background_status=background_status,
        engine_of=ctx.engine_of, contributors=ctx.live_context,
    )

    # Stop kills the in-flight foreground shell command and not just the loop, then what the
    # features stop (an explorer subagent stuck inside a tool call).
    interrupt_hooks: list[Callable[[], None]] = [executor.interrupt_now] if executor is not None else []
    interrupt_hooks += ctx.interrupt_hooks
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=permissions,
        model=model,
        instructions=instructions,
        approver=approver,
        # The session spill sink. The step's output budget writes oversized results here
        # (`_enforce_turn_budget`), the same store the shell tools use.
        spill=stores.spill,
        interrupt_hooks=interrupt_hooks or None,
        # Precedence: an explicit caller value wins, then the agent's own `steps`, then the global
        # config fallback. 0 or None means no cap: the turn runs until the model stops, the person
        # aborts, or the repeat guard trips.
        max_iterations=(
            max_iterations
            if max_iterations is not None
            else (agent.steps if agent.steps is not None else (config.max_iterations or None))
        ),
        # The spend rail beside the effort rail: `max_iterations` bounds how many steps a turn takes,
        # and this bounds how large they were (see `Config.token_budget`).
        token_budget=config.token_budget,
        # How many low-risk reads may overlap in one step (see `Config.max_parallel_tools`). An
        # explicit caller value wins, so a test can prove the pool with a tiny cap.
        max_parallel_tools=max_parallel_tools if max_parallel_tools is not None else config.max_parallel_tools,
        model_settings=model_settings,
        thinking_level=thinking_level,
        # Fallback routes: an explicit caller value wins, otherwise the global config's
        # `fallback_models`, where empty means the one-route behaviour.
        model_fallback=model_fallback if model_fallback is not None else config.fallback_models,
        fallback_cooldown=fallback_cooldown,
        messages=messages,
        audit_sink=audit_sink,
        hooks=build_hooks(workspace, session_id=session_id or "", remote=environment is not None),
        context_provider=context_provider,
        context_parts=context_parts,
        plan_approver=plan_approver,
        question_asker=question_asker,
        tool_requester=tool_requester,
    )
    attach_session_state(engine, ctx)
    attach_pipeline(engine, ctx)
    # A message the person types into the turn releases the shell's waits (`person_steered`).
    if executor is not None:
        executor.set_person_waiting(engine.person_waiting)
        engine.add_person_steer_hook(executor.wake_waiters)
    # Now the live-context closure and the late-bound tools can find it, and the features finish
    # what needed it (the web reader's model).
    ctx.ready(engine)
    return engine


def _start_registry(
    agent: Agent, context: AgentContext, base_registry: Optional[ToolRegistry], extra_tools: Optional[list]
) -> ToolRegistry:
    """The agent's own tools (its manifest: files, search, shell, terminals, the task list), then
    those the manager supplies. MCP and connector tools carry their own metadata and schema. A
    subagent seeds from the parent's registry, which already holds every spec, so the agent's tools
    are not built again, while the features still run on it (the child path filters afterwards)."""
    registry = base_registry if base_registry is not None else ToolRegistry()
    if base_registry is None:
        registry.register_all(agent.build_tools(context))
    if extra_tools:
        registry.register_all(extra_tools)
    return registry
