"""Merging the layers of MCP config into the list of servers a session can use."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from harness.safety.secrets import SecretStore

from .files import global_mcp_path, read_file
from .server import MCPServerDef, parse_server

logger = logging.getLogger(__name__)


def _config_paths(workspace: Optional[str | Path], *, workspace_trusted: bool) -> list[Path]:
    """The config files to merge. Workspace MCP is executable provenance, since a stdio server is a
    process spawned at session open, so an untrusted repo's `.reeve/mcp.json` is never read.
    Cloning a repository must not be enough to define processes that run."""
    paths = [global_mcp_path()]
    if workspace and workspace_trusted:
        paths.append(Path(workspace).expanduser() / ".reeve" / "mcp.json")
    return paths


def load_mcp_servers(
    workspace: Optional[str | Path] = None,
    *,
    secrets: Optional[SecretStore] = None,
    workspace_trusted: bool = False,
) -> list[MCPServerDef]:
    """Merge the built-in, global and (when trusted) workspace `mcpServers` into server definitions.

    Only a trusted workspace contributes, which is the same consent boundary as repository
    `allowed_commands`, and global wins on a name clash, so even a trusted repo cannot silently
    redefine a global server by reusing its name. `${VAR}` in a workspace definition is resolved from
    the person's environment, which is acceptable only because the workspace is trusted. An untrusted
    one is never read.

    Built-in servers (browser automation, computer use) fill any name no config file claims. An entry
    of the same name in the person's file replaces the built-in wholesale, `{"enabled": false}`
    included, and that is how a built-in is turned off."""
    secrets = secrets or SecretStore()
    merged: dict[str, dict[str, Any]] = {}
    for path in _config_paths(workspace, workspace_trusted=workspace_trusted):
        for name, raw in (read_file(path).get("mcpServers") or {}).items():
            if isinstance(raw, dict):
                merged.setdefault(name, raw)  # global comes first, so it wins on a clash
    # Enabled plugins' servers sit under the person's own config and over the built-ins,
    # and they always ask before a call, whatever the plugin declared.
    try:
        from ...plugins import resolve_plugins

        for name, raw in resolve_plugins(workspace=workspace).mcp_servers.items():
            if name not in merged:
                merged[name] = {**{k: v for k, v in raw.items() if k != "_plugin"}, "requires_approval": True}
    except Exception as exc:  # a plugin must never cost the person their own servers
        logger.warning("plugin MCP servers not loaded: %s", exc)
    # Looked up on the package at call time, so a test (or a caller) that replaces the package's
    # `builtin_mcp_servers` changes what loads.
    from .. import config as package

    for name, raw in package.builtin_mcp_servers(secrets).items():
        if name not in merged:
            merged[name] = raw
        else:
            merged[name] = package.current_launch(name, merged[name])
    return [parse_server(name, raw, secrets) for name, raw in merged.items()]
