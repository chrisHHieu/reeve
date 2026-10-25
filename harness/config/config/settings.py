"""The settings a session reads, and which of them a repository may not override."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.loop.goals import DEFAULT_MAX_ROUNDS as DEFAULT_GOAL_MAX_ROUNDS
from harness.loop.goals import MAX_MAX_ROUNDS

# Commands that run WITHOUT an approval prompt. No executable is generally safe: programs that look
# read-only can read secrets outside the workspace, expand environment variables, load
# project-controlled config or plugins, or run helpers (`find -exec`, pytest collection). So the
# built-in list is empty, and a person may opt into command prefixes in their own global config,
# accepting that authority.
DEFAULT_ALLOWED_COMMANDS: list[str] = []


@dataclass
class Config:
    model: str = "gpt-5.6-sol"
    # Routes tried when the session's model fails in a way another model can answer:
    # model ids in the router's `provider:model` form, in order. Empty keeps one route. GLOBAL-ONLY,
    # since a cloned repository must not redirect model traffic on failure (same reasoning as
    # `allowed_domains`).
    fallback_models: list[str] = field(default_factory=list)
    mode: str = "workspace-write"
    # Fallback step cap for a turn, used only when the active agent declares no `steps` of its own.
    # 0 means NO cap: a turn runs until the model stops, the person aborts, or the repeat guard
    # trips, and resource bounds are left to compaction, cancel and tool timeouts. An agent that
    # wants a rail declares `steps:` in its manifest; a positive number here caps every agent that
    # has none.
    max_iterations: int = 0
    # Step cap for a SUBAGENT child (a step is one model turn with its tool calls). 0 means
    # UNCAPPED: a child runs until it stops, its wall clock fires or the repeat guard trips. Its
    # other rails stay (the 600 s clock, the repeat guard, the token budget the parent folds in, the
    # 4-live-run limit), and when one fires the child is asked for its REPORT, not a handoff, so a
    # stop still returns findings. A positive number is the default for every child, and
    # `[agents.<name>].steps` overrides it for one agent.
    subagent_steps: int = 0
    # How many levels of subagents a chain may reach, counted by walking the session ancestry at
    # call time (a root is depth 0, its child 1, a grandchild 2). The default 1 lets a session
    # delegate but stops a child from delegating further; `0` disables subagents entirely, so the
    # `task` tool is not registered and a direct call refuses with the same message.
    subagent_depth: int = 1
    # How many children ONE parent session may have running at once. `0` means no cap (every child
    # is a real session with its own model calls, so that is for power users).
    subagent_max_parallel: int = 4
    # `workflow` cost controls, read per run by the engine's intercepted tool: `workflow_timeout`
    # is the whole script's wall clock; `workflow_max_agents` and `workflow_max_tokens` are the
    # per-run fatal caps (0 tokens = uncapped).
    workflow_timeout: float = 600.0
    workflow_max_agents: int = 24
    workflow_max_tokens: int = 0
    # Goal rounds: `[goal] max_rounds` is the TOML table (global < workspace, like every other
    # table), and `goal_max_rounds` is the resolved value `load_config` normalizes from it. Invalid
    # or non-positive input degrades to the default with a log, and a huge value clamps to
    # `goals.MAX_MAX_ROUNDS`.
    goal: dict[str, Any] = field(default_factory=dict)
    goal_max_rounds: int = DEFAULT_GOAL_MAX_ROUNDS
    # Auto-compaction knobs, set in `config.toml` under `[compaction]` and never in a UI. Keys:
    # `buffer_tokens`, `keep_tokens`, `max_context_tokens` (a number or "auto": the model's window
    # up to 400k), `model` (summarizer pin), `instructions` (replaces the built-in summary prompt).
    # Absent keys fall back to the built-in defaults, and a typo degrades where it is used, never
    # mid-turn. A workspace may override it like any non-global-only field.
    compaction: dict[str, Any] = field(default_factory=dict)
    # Delegation behaviour under `[delegation]`. Key: `worktree_isolation` (default false: each
    # `task` child gets its own git worktree). Absent or mistyped degrades to the default
    # where it is used; a workspace may opt in, since the worktree lives inside the repo it
    # isolates.
    delegation: dict[str, Any] = field(default_factory=dict)
    # Anthropic server-side context editing (the `context-management-2025-06-27` beta): old tool
    # results and old thinking blocks are cleared from the prompt BEFORE it reaches the model, so a
    # long session stops growing without waiting for a full compaction. Keys: `enabled` (default
    # true), `trigger_input_tokens`, `keep_tool_uses`, `clear_at_least_tokens`, `exclude_tools`,
    # `clear_tool_inputs`, `clear_thinking`. Provider-level, so GLOBAL-ONLY (clients are cached per
    # provider, not per workspace); a model or gateway that rejects the beta disables it for the
    # process with one warning (see AnthropicProvider._create).
    context_editing: dict[str, Any] = field(default_factory=dict)
    # The agent a new session starts on. It must name an agent in the task-agent map that is neither
    # a `subagent` nor hidden; a bad name is an error at resolve time (`resolve_agents`). The default
    # primary is owned by the persona registry, so this is validated and served as data.
    default_agent: str = ""
    # Tool names denied in EVERY child, whatever the agent's own ruleset says: a tool reserved for
    # the person-facing session (say `send_message`) must not become reachable by delegating. The
    # tool is REMOVED from the child's registry, so its model never proposes it.
    primary_tools: list[str] = field(default_factory=list)
    # Subagent definitions: `[agents.explore]` with `description`, `mode` ("subagent" / "primary" /
    # "all"), `model`, `prompt` / `prompt_file`, `temperature`, `top_p`, `steps`, `hidden`,
    # `permission` rules and the deprecated `tools` booleans. The two built-ins (`general`,
    # `explore`) are always there; a table for a new name ADDS an agent, `disable = true` removes
    # one. Markdown files merge INTO this table after the TOML pass, same name and keys, markdown
    # winning.
    agents: dict[str, Any] = field(default_factory=dict)
    # The top-level `tools` map: `write_file = false` is a global permission rule every agent
    # inherits. It is the BASE layer; the `permission` table below merges over it, and an agent's
    # own rules over that. `write` / `edit` / `patch` (and the concrete `write_file`-family names)
    # fold onto the `edit` permission, the alias mapping `task_agents` defines.
    tools: dict[str, Any] = field(default_factory=dict)
    # The top-level `permission` table, merged into EVERY agent's ruleset after the definition's own
    # rules, so a global `edit = "deny"` wins.
    permission: dict[str, Any] = field(default_factory=dict)
    # Low-risk tool calls that may overlap within one step. A response asking for thirty reads would
    # otherwise start thirty at once, which the thread pool, any per-directory lock and the
    # provider's rate limits all feel. 1 = serial even for reads; 0 = uncapped.
    max_parallel_tools: int = 10
    # Provider traffic ONE turn may spend, in tokens, before the turn stops on the same graceful
    # path the iteration cap uses (a checkpoint is written, the end is typed
    # `token_budget_exceeded`, and "continue" resumes from the handoff).
    #
    # OFF by default: a turn is bounded by steps and by the context window (compaction), and a
    # default that stops a long, intentional turn mid-work is the wrong default. Set a number to opt
    # in; `0` is off. It counts the turn's own round-trips (cache reads included, see
    # `TokenUsage.total`) plus whatever its subagents reported, so a fan-out counts too, but not
    # compaction's summarizer call. User-global only (`GLOBAL_ONLY_FIELDS`), because raising a
    # spend ceiling is something a cloned repository must not do to you.
    token_budget: int = 0
    allowed_commands: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_COMMANDS))
    # Tools auto-approved in every preset that would otherwise ask (workspace-write and read-only's
    # non-consequential paths): the person has decided these never need a prompt.
    auto_allow: list[str] = field(default_factory=list)
    # Egress destinations `web_fetch` may reach WITHOUT an approval prompt (exact host or
    # subdomain). Empty by default, so the first fetch to any host asks. A power-user opt-in like
    # `allowed_commands`, and user-global only so a repo cannot widen the agent's network reach.
    allowed_domains: list[str] = field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = 8765
    # Web search: "" runs the shipped chain `exa -> duckduckgo` (Exa over its API when an
    # EXA_API_KEY or profile key exists, over the keyless public MCP otherwise; DuckDuckGo catches
    # Exa's rate limits and misses). Pin "duckduckgo" for the keyless-only path.
    web_search_provider: str = ""
    # Session sharing (`/share`) has NO hosted backend here, on purpose. The person
    # configures an endpoint that accepts the exported session JSON; empty means the command is
    # disabled, with that reason. Global-only: a cloned repository must not be able to point the
    # upload of your transcript at a server it controls.
    share: dict[str, Any] = field(default_factory=dict)
    # Language servers: `[lsp] enabled = false` turns the `lsp` tool off entirely, and
    # `[lsp.servers.<lang>] command = ["..."]` replaces the shipped default for one language. The
    # `servers` sub-table is data on purpose, so the set of languages grows by adding a table, never
    # by changing a type. A workspace may override it like any non-global-only field.
    lsp: dict[str, Any] = field(default_factory=dict)
    # Web tools under `[web]`. Keys: `extract` ("auto" | "off": whether
    # `web_fetch(prompt=...)` has a model read the page and return only the answer) and
    # `extract_model` ("provider:model"; "" = the session's own model). GLOBAL-ONLY: a pinned model
    # is sent page text, and a cloned repository must not choose where it goes.
    web: dict[str, Any] = field(default_factory=dict)
    # Which of the session's features are left out: `[features] remove = ["web", "lsp"]`
    # drops each named feature's tools, prompt section and live context from new sessions. The core
    # (permissions, compaction, the memory door, the session log) is no feature and cannot be named.
    # `[features] add = ["tool_request"]` turns on an opt-in feature. A workspace may set `remove`,
    # which joins the person's; its `add` is ignored, since it can only take away.
    features: dict[str, Any] = field(default_factory=dict)


FIELDS = frozenset(
    {
        "model",
        "fallback_models",
        "mode",
        "max_iterations",
        "max_parallel_tools",
        "token_budget",
        "subagent_steps",
        "subagent_depth",
        "subagent_max_parallel",
        "workflow_timeout",
        "workflow_max_agents",
        "workflow_max_tokens",
        "goal",
        "compaction",
        "delegation",
        "context_editing",
        "web",
        "default_agent",
        "primary_tools",
        "agents",
        "tools",
        "permission",
        "allowed_commands",
        "auto_allow",
        "allowed_domains",
        "host",
        "port",
        "web_search_provider",
        "share",
        "lsp",
        "features",
    }
)

# These change what consequential actions can run without a prompt, so the workspace override pass
# never applies them. `allowed_commands` is added separately, and only for a workspace the person
# has trusted by its canonical path; `auto_allow` and `allowed_domains` stay user-global (a repo
# must not widen the agent's command or network reach).
GLOBAL_ONLY_FIELDS = frozenset(
    {
        "allowed_commands",
        "auto_allow",
        "allowed_domains",
        "token_budget",
        # A repo must not point the upload of your transcript (and its code) at a server it
        # controls: the same egress reasoning as `allowed_domains`.
        "share",
        # The fallback chain decides where model traffic goes when the primary fails: the person's
        # routing decision, not a cloned repository's.
        "fallback_models",
        # Provider-level behaviour over a cached client: a workspace override could never take
        # effect, and offering one would be a surface that lies.
        "context_editing",
        # `[web] extract_model` decides which provider reads fetched pages: the person's routing
        # decision, not a cloned repository's.
        "web",
    }
)
WORKSPACE_FIELDS = FIELDS - GLOBAL_ONLY_FIELDS
