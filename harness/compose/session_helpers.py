"""Small, self-contained helpers the assembly leans on."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from harness.integrations.connectors import connector_list
from harness.safety.secrets import SecretStore
from harness.tools import ToolRegistry


def describe_elapsed(seconds: float) -> str:
    """The gap since the previous message as one short phrase, or "" when it is noise.

    Under 90 seconds a turn is just the previous turn's continuation, and saying "1 min" on every
    back-and-forth would cost tokens to state the obvious. Above that the number is real context: a
    reply eleven minutes later plausibly means the person stepped away, and one two days later
    certainly does. It is coarse on purpose, since a model reasons about "3h 20m" and not about
    12 003 seconds."""
    if seconds < 90:
        return ""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def enabled_connector_tools(secrets: SecretStore) -> tuple[set[str], set[str]]:
    """`(enabled connectors, enabled tool names)` from what is connected and switched on."""
    connectors = {entry["name"]: entry for entry in connector_list(secrets)}
    enabled = {name for name, entry in connectors.items() if entry.get("connected") and entry.get("enabled")}
    tools = {
        tool["name"]
        for entry in connectors.values()
        if entry.get("name") in enabled
        for tool in entry.get("tools", [])
        if tool.get("enabled")
    }
    return enabled, tools


def loaded_skill_names(messages: list[dict[str, Any]]) -> set[str]:
    """Skills whose instructions successfully entered this conversation, meaning a `load_skill` call
    with a result that is not an error. It drives the disable countermand: a menu quietly shrinking
    is passive, but instructions already in history keep steering the model unless it is explicitly
    asked to stop."""
    results: dict[str, str] = {}
    for message in messages:
        if message.get("role") == "tool" and message.get("tool_call_id"):
            content = message.get("content")
            results[message["tool_call_id"]] = content if isinstance(content, str) else json.dumps(content)
    loaded: set[str] = set()
    for message in messages:
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        for call in message["tool_calls"]:
            function = call.get("function") or {}
            if function.get("name") != "load_skill":
                continue
            try:
                name = str(json.loads(function.get("arguments") or "{}").get("name", ""))
            except Exception:
                continue
            if name and '"instructions"' in results.get(call.get("id", ""), ""):
                loaded.add(name)
    return loaded


def skill_dirs(workspace: Optional[Path]) -> list[Any]:
    """The skill folders a session reads, from lowest to highest precedence (P1-2): the shared
    folders other tools use (`~/.config/opencode/skills`, `~/.claude/skills`, `~/.agents/skills`),
    then enabled plugins' skills (`<plugin>:<name>`), then the product's global folder
    (`state_dir()/skills`), then the project's shared folders (`.agents`, `.claude`,
    `.opencode/skill`, `.opencode/skills`), and last the project's own
    (`<ws>/.harness/extensions/skills`). No skill ships bundled: a fresh install has none. The
    caller puts a persona bundle's folders first, as the lowest, so a person's copy shadows a bundle
    skill."""
    from harness.extensions.skills.store import loader_roots

    return loader_roots(workspace)


def verify_agent_enabled(config: Any) -> bool:
    """Whether the `verify` subagent can be asked in this session's config. A person can switch it
    off in Settings ▸ Subagents, or a config can replace it."""
    try:
        from harness.tools.task_agents import resolve_agents

        agent = resolve_agents(config).get("verify")
    except Exception:
        return False
    return agent is not None and not agent.disabled


def build_hooks(workspace: Optional[str | Path], *, session_id: str = "", remote: bool = False) -> Optional[Any]:
    """The session's hook runner, or None when nothing is registered.

    A remote session gets no hooks. They would run on the controller while the workspace they
    describe lives on another machine, the reason spill and undo are disabled there, and a hook that
    silently ran in the wrong world is worse than one that did not run at all."""
    if remote or not workspace:
        return None
    from harness.extensions.plugins import resolve_plugins
    from harness.loop.hooks import load_hooks
    from harness.loop.hooks_runner import HookRunner

    specs, note = load_hooks(workspace, plugin_hooks=resolve_plugins(workspace=workspace).hooks)
    if not specs and not note:
        return None
    return HookRunner(specs, workspace=workspace, session_id=session_id, note=note)
