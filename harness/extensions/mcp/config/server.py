"""One server definition, and reading it from its `mcpServers` entry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from harness.safety.secrets import SecretStore

_HTTP_TYPES = {"http", "https", "sse", "streamable-http", "streamable_http"}


@dataclass
class MCPServerDef:
    name: str
    transport: str  # "stdio" | "http"
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    include_tools: Optional[list[str]] = None
    exclude_tools: Optional[list[str]] = None
    requires_approval: bool = True
    # When true, the server's own `readOnlyHint` decides the gate for each tool: read-only runs and
    # the rest asks, and no hint means it asks. Only a connector descriptor sets this, for a
    # first-party server, and it is never read from a hand-written entry's claim about itself. A
    # third party calling itself read-only must not turn a human gate into an auto-run
    # (mcp/tools.py).
    approval_by_hints: bool = False
    # When true, the server is owned per session. Each live session gets its own child process and
    # connection (the built-in browser), released when the session is deleted. When false there is
    # one shared connection for the whole app (remote servers, the single desktop's computer-use
    # sidecar).
    per_session: bool = False
    # The session's folders, answered to the server's `roots/list` (the MCP way a client says where
    # the work is). Set by the arm path for a per-session built-in and never read from a config file.
    # The process's cwd cannot say it for a session with no project folder: its scratch folder is
    # made when the first turn starts, after the browser was started.
    roots: list[str] = field(default_factory=list)
    # How an HTTP server signs in. Tokens live in the secret store and never in this file.
    #   "oauth"  browser OAuth 2.1 with PKCE and Dynamic Client Registration (mcp/oauth).
    #   "device" the OAuth device grant (mcp/device_auth.py), for vendors that offer no DCR and
    #            demand a client secret they will not let a desktop app keep.
    #   "bearer" the connector's own pasted API key, sent as an Authorization header
    #            (mcp/api_key_auth.py). It suits a vendor whose MCP server accepts a plain API key,
    #            which is most of the ones that are not OAuth.
    auth: Optional[str] = None
    # The public OAuth client id for the device grant. It is not a secret. It identifies the app, the
    # way the `gh` CLI ships one in its binary, so it is fine in this file.
    client_id: Optional[str] = None


def parse_server(name: str, raw: dict[str, Any], secrets: SecretStore) -> MCPServerDef:
    raw = secrets.resolve(raw)  # `${VAR}` is resolved everywhere before the definition is built
    declared = str(raw.get("type", "")).lower()
    is_http = declared in _HTTP_TYPES or bool(raw.get("url"))
    return MCPServerDef(
        name=name,
        transport="http" if is_http else "stdio",
        command=raw.get("command"),
        args=list(raw.get("args", []) or []),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        cwd=raw.get("cwd"),
        url=raw.get("url"),
        headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
        enabled=bool(raw.get("enabled", True)),
        include_tools=raw.get("include_tools"),
        client_id=raw.get("client_id"),
        exclude_tools=raw.get("exclude_tools"),
        requires_approval=bool(raw.get("requires_approval", True)),
        approval_by_hints=bool(raw.get("approval_by_hints", False)),
        per_session=bool(raw.get("per_session", False)),
        auth=(str(raw["auth"]).lower() if raw.get("auth") else None),
    )
