"""MCP server config: the standard `mcpServers` JSON, layered global and workspace.

    Global:     ~/.config/Reeve/mcp.json
    Workspace:  <workspace>/.reeve/mcp.json   (overrides global on a name clash, but only after the
                person trusts that workspace, the same gate as repository `allowed_commands`)

It is paste-compatible with the config files other MCP hosts use. `${VAR}` in command, args, env, url
and headers is resolved at load time through the secret store (environment plus a local `.env`). REST
edits target the global file.
"""

from __future__ import annotations

from . import builtin
from .builtin import browser_preference, builtin_mcp_servers, current_launch
from .files import (
    delete_global_server,
    global_mcp_path,
    patch_global_server,
    put_global_server,
    read_global,
)
from .loading import load_mcp_servers
from .server import MCPServerDef

__all__ = [
    "MCPServerDef",
    "browser_preference",
    "builtin",
    "builtin_mcp_servers",
    "current_launch",
    "delete_global_server",
    "global_mcp_path",
    "load_mcp_servers",
    "patch_global_server",
    "put_global_server",
    "read_global",
]
