"""Claude Code → Reeve: tool names and a plugin agent's frontmatter (design §5.3, §14.2).

One table, both directions, so a hook matcher, an agent's tool list and (later) a hook's
payload all speak the same names. A tool with no Reeve counterpart is dropped and said
so — never guessed.

The agent conversion keeps the plugin rule that a plugin never grants: a Claude Code
`tools` list is an ALLOWLIST, so it becomes "deny everything, then restore the session's
own verdict for the listed tools" — a listed tool the session asks about is still asked
about. `permissionMode`, `hooks`, `mcpServers` and `initialPrompt` are always dropped, as
Claude Code drops them for plugin agents.
"""

from __future__ import annotations

from typing import Any, Optional

#: Claude Code tool name → Reeve tool name (None: no counterpart).
TOOL_ALIASES: dict[str, Optional[str]] = {
    "Bash": "run_shell",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "replace_in_file",
    "MultiEdit": "apply_patch",
    "Grep": "grep",
    "Glob": "list_files",
    "WebFetch": "web_fetch",
    "WebSearch": "web_search",
    "Task": "task",
    "Agent": "task",
    "TodoWrite": "todo_write",
    # Not granted from a plugin agent's tool list: Reeve's notebook editor answers to the `edit`
    # permission with every other writer, so allowing it would allow them all.
    "NotebookEdit": None,
}

#: Reeve → Claude Code, for the reverse direction (a hook's `tool_name`). `task` answers
#: with its current Claude Code name.
REEVE_TO_CLAUDE: dict[str, str] = {
    reeve: claude
    for claude, reeve in TOOL_ALIASES.items()
    if reeve is not None and claude != "Task"
}
#: A hook still hears the notebook editor by its Claude Code name.
REEVE_TO_CLAUDE["edit_notebook"] = "NotebookEdit"

#: Agent frontmatter Reeve has no counterpart for yet.
_AGENT_UNSUPPORTED = ("memory", "omitClaudeMd", "experimental")
#: Agent frontmatter a plugin agent may never set (Claude Code ignores them too).
_AGENT_REFUSED = ("permissionMode", "hooks", "mcpServers", "initialPrompt")
#: Model aliases that mean "whatever the parent session runs".
_MODEL_INHERIT = {"inherit", "sonnet", "opus", "haiku", "fable"}


def reeve_tool(claude_name: str) -> Optional[str]:
    """The Reeve tool for a Claude Code tool name; a Reeve or MCP name passes through."""
    name = str(claude_name or "").strip()
    if name in TOOL_ALIASES:
        return TOOL_ALIASES[name]
    return name or None


def claude_tool(reeve_name: str) -> str:
    """The Claude Code name for a Reeve tool; any other name passes through."""
    return REEVE_TO_CLAUDE.get(reeve_name, reeve_name)


def _tool_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def agent_from_claude(
    frontmatter: dict[str, Any], body: str, *, plugin: str
) -> tuple[dict[str, Any], list[str]]:
    """A plugin agent's Claude Code frontmatter as a Reeve agent entry, plus what was
    dropped. The entry carries `_allow`/`_deny` Reeve tool names; `resolve_agents` turns
    them into narrowing rules against the agent's own ruleset (`task_agents.narrowing_rules`)."""
    skipped: list[str] = []
    entry: dict[str, Any] = {"mode": "subagent"}
    description = str(frontmatter.get("description") or "").strip()
    entry["description"] = description or f"Agent from {plugin} plugin"
    if body.strip():
        entry["prompt"] = body.strip()
    model = str(frontmatter.get("model") or "").strip()
    if model and model.lower() not in _MODEL_INHERIT:
        # A concrete id is kept only when it is a Reeve route (`provider:model`); a bare
        # vendor id would name a model this person may not have configured.
        if ":" in model:
            entry["model"] = model
        else:
            skipped.append(f"model {model}: not a configured route, using the session's")
    if frontmatter.get("color"):
        entry["color"] = str(frontmatter["color"])

    allow: list[str] = []
    for name in _tool_list(frontmatter.get("tools")):
        mapped = reeve_tool(name)
        if mapped is None:
            skipped.append(f"tool {name}: no Reeve counterpart")
        else:
            allow.append(mapped)
    deny: list[str] = []
    for name in _tool_list(frontmatter.get("disallowedTools")):
        mapped = reeve_tool(name)
        if mapped is not None:
            deny.append(mapped)
    if allow:
        entry["_allow"] = allow
    if deny:
        entry["_deny"] = deny
    _carry_run_settings(frontmatter, entry, skipped)
    for key in _AGENT_UNSUPPORTED:
        if key in frontmatter:
            skipped.append(f"{key}: not supported")
    for key in _AGENT_REFUSED:
        if key in frontmatter:
            skipped.append(f"{key}: a plugin agent may not set it")
    return entry, skipped


def _carry_run_settings(frontmatter: dict[str, Any], entry: dict[str, Any], skipped: list[str]) -> None:
    """How a plugin agent runs, onto the Reeve agent fields that mean the same: `maxTurns` is its
    step cap, `effort` its thinking level, `background` whether every delegation to it runs in
    the background, `isolation: worktree` its own git worktree. A value Reeve cannot honour as
    written is skipped, not approximated."""
    turns = frontmatter.get("maxTurns")
    if turns is not None:
        if isinstance(turns, int) and not isinstance(turns, bool) and turns > 0:
            entry["steps"] = turns
        else:
            skipped.append(f"maxTurns {turns!r}: not a positive whole number")
    effort = str(frontmatter.get("effort") or "").strip().lower()
    if effort:
        entry["variant"] = effort
    background = frontmatter.get("background")
    if background is not None:
        entry["background"] = background is True or str(background).strip().lower() == "true"
    isolation = str(frontmatter.get("isolation") or "").strip().lower()
    if isolation:
        if isolation == "worktree":
            entry["isolation"] = "worktree"
        else:
            skipped.append(f"isolation {isolation}: only worktree is supported")

