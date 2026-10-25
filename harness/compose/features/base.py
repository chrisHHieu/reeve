"""What a feature is, and the one context every feature is set up in.

A feature is one part of what a session can do (the web, language servers, subagents, skills…):
one file that registers its tools and says what else it brings, its piece of the prompt, a line of
live context, what its approval card shows, what it does when the engine exists or is stopped.
The assembly (`assembly.py`) runs the features of `FEATURES` in order and knows no tool by name.

What is not a feature is the core: permissions, the session's world, compaction, the memory door,
the session log and the listeners whose order is part of their meaning. A feature reads the core
through this context and cannot replace it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)

#: Where a feature's prompt section goes among the others: sections are joined in this order
#: after the session's own prompt (agent, model, rules, environment, project conventions), so the
#: prompt's bytes do not depend on the order the features ran in.
PROMPT_MEMORY = 10
PROMPT_RECAP = 20


@dataclass
class SessionInputs:
    """What the caller of `build_engine` handed over, as it handed it over. Features read these;
    the derived values (the resolved provider, the secret store, the roots) are on the context."""

    agent: Any
    workspace: Any = None
    environment: Optional[dict[str, Any]] = None
    environment_runner: Optional[Any] = None
    environment_password: Optional[str] = None
    lsp_manager: Optional[Any] = None
    background_status: Optional[Callable[[], str]] = None
    model: str = ""
    model_fallback: Optional[list[str]] = None
    fallback_cooldown: Optional[Any] = None
    mode: Any = None
    plan: bool = False
    tool_discovery: bool = False
    model_selection: bool = True
    visuals: str = "auto"
    approver: Optional[Any] = None
    provider: Optional[Any] = None
    allowed_commands: Optional[list[str]] = None
    max_iterations: Optional[int] = None
    max_parallel_tools: Optional[int] = None
    deadline_ceiling_ms: Optional[int] = None
    model_settings: Optional[dict[str, Any]] = None
    thinking_level: Optional[str] = None
    user_rules: Optional[Any] = None
    auto_memory: Optional[Any] = None
    auto_memory_enabled: Optional[Any] = None
    messages: Optional[list[dict[str, Any]]] = None
    extra_tools: Optional[list[Any]] = None
    base_registry: Optional[Any] = None
    secrets: Optional[Any] = None
    conversation_store: Optional[Any] = None
    task_store: Optional[Any] = None
    wake_store: Optional[Any] = None
    subagent_spawner: Optional[Any] = None
    session_depth: Optional[Any] = None
    subagent_metadata: Optional[Any] = None
    subagent_cancel_origin: Optional[Any] = None
    workflow_spawner: Optional[Any] = None
    permission_asker: Optional[Any] = None
    goal_store: Optional[Any] = None
    session_id: Optional[str] = None
    audit_sink: Optional[Any] = None
    roots: Optional[list] = None
    plan_approver: Optional[Any] = None
    question_asker: Optional[Any] = None
    tool_requester: Optional[Any] = None
    subscription_store: Optional[Any] = None
    channel_buffer: Optional[Any] = None
    connector_filter: Optional[set[str]] = None
    skill_filter: Optional[Any] = None
    extra_skill_dirs: Optional[list[str | Path]] = None


@dataclass
class FeatureContext:
    """The session as the core resolved it, and what the features contribute to it."""

    inputs: SessionInputs
    agent: Any
    config: Any
    registry: Any
    permissions: Any
    secrets: Any
    provider: Any
    model: str
    model_settings: Optional[dict[str, Any]]
    world: Optional[Any]
    executor: Optional[Any]
    context: Any
    todo: Any
    root_list: list
    ws: Optional[Path]
    remote: bool
    remote_root: str
    memory_root: Optional[Path]
    spill: Optional[Any]
    undo: Optional[Any]
    intents: Optional[Any]
    plan_path: Optional[Path]
    witnesses: Any
    edit_exempt_roots: Callable[[], list[str]]
    held_edit_tools: Any
    # What the features contribute, read by the assembly once every feature has run.
    services: dict[str, Any] = field(default_factory=dict)
    prompt_sections: list[tuple[int, Callable[[], str]]] = field(default_factory=list)
    live_context: list[Callable[[Any], list[tuple[str, str]]]] = field(default_factory=list)
    approval_extras: dict[str, Callable[[dict], dict]] = field(default_factory=dict)
    interrupt_hooks: list[Callable[[], None]] = field(default_factory=list)
    ready_hooks: list[Callable[[Any], None]] = field(default_factory=list)
    engine: Optional[Any] = None

    # -- what a feature calls in `setup` ------------------------------------------------------

    def engine_of(self) -> Optional[Any]:
        """The engine, once it exists. Tools are built before it and read it when called."""
        return self.engine

    def prompt(self, order: int, render: Callable[[], str]) -> None:
        """A section of the system prompt, rendered after every feature has run and the tool
        list is final, so a section that names a tool can ask whether the tool is there."""
        self.prompt_sections.append((order, render))

    def live(self, contribute: Callable[[Any], list[tuple[str, str]]]) -> None:
        """Lines of the per-turn live context, as `(label, text)` pairs; called each turn with the
        engine (None before it exists), after the core's clock, background, mode and roots."""
        self.live_context.append(contribute)

    def approval_extra(self, tool_name: str, extra: Callable[[dict], dict]) -> None:
        """What the approval card for `tool_name` shows besides the call, resolved when the card
        is raised."""
        self.approval_extras[tool_name] = extra

    def on_interrupt(self, hook: Callable[[], None]) -> None:
        """Called when the person stops the turn, after the core stops the foreground shell."""
        self.interrupt_hooks.append(hook)

    def on_engine_ready(self, hook: Callable[[Any], None]) -> None:
        """Called once with the engine, after it is built and wired."""
        self.ready_hooks.append(hook)

    def provide(self, key: str, value: Any) -> None:
        """A value the core puts on the engine for others to read (the language-server pool,
        the skill loader)."""
        self.services[key] = value

    # -- what the assembly calls ---------------------------------------------------------------

    def render_prompt_sections(self) -> list[str]:
        return [render() for _, render in sorted(self.prompt_sections, key=lambda item: item[0])]

    def ready(self, engine: Any) -> None:
        self.engine = engine
        for hook in self.ready_hooks:
            hook(engine)


class Feature(Protocol):
    """One part of what a session can do. `id` is what `[features] remove` names. A feature a
    package adds (`installed.py`) may also say `after = "<id>"`: the feature it follows in the
    order. A feature that says `opt_in = True` is off until the person's own config names it in
    `[features] add`: its tools are sent with every request of every session, and the need they
    serve is rare enough that only someone who has it should pay for them."""

    id: str

    def setup(self, ctx: FeatureContext) -> None:
        """Register this feature's tools and contributions on `ctx`, or nothing when the session
        cannot have it (no workspace, a remote world, a trait the agent lacks)."""
        ...
