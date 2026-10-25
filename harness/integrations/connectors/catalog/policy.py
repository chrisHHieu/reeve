"""What the catalog says about a tool, and the settings a person can change on it."""

from __future__ import annotations

from typing import Any, Optional

from harness.safety.secrets import SecretStore

from . import chat, google, mail, work
from .types import ConnectorToolDef


def _slack_defs() -> tuple[ConnectorToolDef, ...]:
    """Slack's tools come from the catalogue in `slack_specs`, so the builders and this list
    cannot disagree. The admin tools (channels, members, user groups, deletion) start off."""
    from ..slack_specs import READ
    from ..slack_specs_write import ADMIN, WRITE

    return tuple(
        ConnectorToolDef(
            "slack", tool.name, tool.label, tool.kind, tool.description,
            default_enabled=tool.group != "admin", target_arg=tool.target_arg, group=tool.group,
        )
        for tool in READ + WRITE + ADMIN
    )


TOOL_DEFS: tuple[ConnectorToolDef, ...] = mail.ALL + google.ALL + work.ALL + chat.ALL + _slack_defs()

KIND_BY_NAME = {tool.name: tool.kind for tool in TOOL_DEFS}
TOOL_TO_CONNECTOR = {tool.name: tool.connector for tool in TOOL_DEFS}
TOOLS_BY_CONNECTOR: dict[str, list[ConnectorToolDef]] = {}
for _tool in TOOL_DEFS:
    TOOLS_BY_CONNECTOR.setdefault(_tool.connector, []).append(_tool)

# The arguments that can carry a standing rule: the ones the catalogue declares, plus the
# always-available `send_message`, whose `target` is the reply handle "platform:chat_id", the
# exact address a rule pins. This dict is the one place that says which tools can EVER carry a
# standing rule. A tool that runs commands or destroys data must never appear in it.
TARGET_ARGS: dict[str, str] = {tool.name: tool.target_arg for tool in TOOL_DEFS if tool.target_arg}
TARGET_ARGS["send_message"] = "target"


def approval_for_tool(name: str, default: bool = True) -> bool:
    """Whether a connector tool asks for approval. The catalogue's read or write kind is the one
    source: a read on a service the person connected never asks, a write always does. A tool the
    catalogue does not list (MCP and experimental ones) keeps the default it was given, which
    stays conservative."""
    kind = KIND_BY_NAME.get(name)
    return default if kind is None else kind != "read"


def target_arg_for(tool_name: str) -> Optional[str]:
    """The argument that names this tool's standing-rule target, or None when the tool cannot
    carry a standing rule."""
    return TARGET_ARGS.get(tool_name)


def connector_for_tool(tool_name: str) -> Optional[str]:
    return TOOL_TO_CONNECTOR.get(tool_name)


def load_tool_settings(secrets: SecretStore, connector: str) -> dict[str, bool]:
    raw = secrets.get(f"{connector}:tools") or {}
    enabled = raw.get("enabled") if isinstance(raw, dict) else None
    return {str(name): bool(value) for name, value in (enabled or {}).items()}


def tool_enabled(secrets: SecretStore, connector: str, tool_name: str) -> bool:
    overrides = load_tool_settings(secrets, connector)
    if tool_name in overrides:
        return overrides[tool_name]
    for tool in TOOLS_BY_CONNECTOR.get(connector, []):
        if tool.name == tool_name:
            return tool.default_enabled
    return False


def patch_tool_settings(secrets: SecretStore, connector: str, enabled: dict[str, Any]) -> dict[str, Any]:
    known = {tool.name for tool in TOOLS_BY_CONNECTOR.get(connector, [])}
    if not known:
        return {"ok": False, "error": "unknown connector or no tools"}
    current = load_tool_settings(secrets, connector)
    for name, value in enabled.items():
        if name in known:
            current[name] = bool(value)
    secrets.put(f"{connector}:tools", {"enabled": current})
    return {"ok": True, "tools": current}


#: The tools a connector keeps for itself when the vendor's MCP server serves its account: the
#: ones that server cannot do because they act on this machine. GitHub's server reads and
#: writes GitHub, and cannot put a repository on the person's disk.
_KEPT_WITH_VENDOR_MCP: dict[str, tuple[str, ...]] = {"github": ("github_clone", "github_pull")}


def active_tool_defs(secrets: SecretStore, connector: str) -> list[ConnectorToolDef]:
    """The connector's own tool defs.

    A connector the vendor's MCP server serves declares none of its own: its tools come from the
    server, whole, and the live connection describes them (`tool_dicts` reads them off it for
    the Connectors page). What it keeps is what the server cannot do (`_KEPT_WITH_VENDOR_MCP`).
    """
    kept = _KEPT_WITH_VENDOR_MCP.get(connector)
    if kept is not None:
        return [tool for tool in TOOLS_BY_CONNECTOR.get(connector, []) if tool.name in kept]
    if (secrets.get(f"{connector}:default") or {}).get("mode") == "mcp":
        return []
    return TOOLS_BY_CONNECTOR.get(connector, [])


def tool_dicts(secrets: SecretStore, connector: str) -> list[dict[str, Any]]:
    overrides = load_tool_settings(secrets, connector)
    return [
        {
            "name": tool.name,
            "label": tool.label,
            "kind": tool.kind,
            "description": tool.description,
            "enabled": bool(overrides.get(tool.name, tool.default_enabled)),
            "group": tool.group,
            "requires_approval": True,
        }
        for tool in active_tool_defs(secrets, connector)
    ]
