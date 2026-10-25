"""MCP servers: their config, the connections to them (on the `mcp` SDK) and their tools."""

from __future__ import annotations

from .client import MCPManager
from .config import MCPServerDef, delete_global_server, load_mcp_servers, patch_global_server, put_global_server, read_global
from .tools import MCP_GROUP, build_callables, tool_name

__all__ = [
    "MCPManager",
    "MCPServerDef",
    "MCP_GROUP",
    "build_callables",
    "delete_global_server",
    "load_mcp_servers",
    "patch_global_server",
    "put_global_server",
    "read_global",
    "tool_name",
]
