"""One live connection to a server, what identifies its far end, and reading what a server offers."""

from __future__ import annotations

import asyncio
import logging
from typing import IO, Any, Optional

from mcp import ClientSession

from ..config import MCPServerDef

logger = logging.getLogger(__name__)

_STDERR_TAIL_LINES = 20
_STDERR_TAIL_CHARS = 1500

# A safety stop for the tools/list walk. A server that returns a cursor forever would otherwise loop
# until the connect timeout with nothing to show for it.
_MAX_TOOL_PAGES = 20


def connection_key(server: MCPServerDef) -> tuple:
    """What a live connection is, beyond its name.

    Connections are cached by server name, which quietly assumes a name always means the same
    endpoint. It does not. A vendor moves an endpoint, or the person picks a different one of
    several the same vendor publishes (Postman's Essentials, Full and Code). When that happened the
    cache handed back the connection to the old endpoint, a `tools/list` on it succeeded, and
    connect reported success: no sign-in, no new tokens, and a tool set from an endpoint the person
    had just chosen to leave (2026-09-08).

    Only what identifies the far end belongs here. Cosmetic config such as approval flags and tool
    filters must not, or every unrelated edit would drop a working connection."""
    return (
        server.transport,
        server.url,
        server.command,
        tuple(server.args or ()),
        server.auth,
        tuple(sorted((server.headers or {}).items())),
    )


class Conn:
    def __init__(self, session: ClientSession, tools: list[Any], key: tuple = (), roots: tuple = ()) -> None:
        self.session = session
        self.tools = tools  # list[mcp.types.Tool]
        self.shutdown = asyncio.Event()
        self.key = key  # the endpoint this connection is actually talking to
        # The folders it was told are the session's. Not part of `key`, which also names the kept
        # tool list (`tool_cache`): every session has its own folder, and a key per folder would
        # make every new session wait for its browser's first start.
        self.roots = roots


def same_endpoint(conn: "Conn", server: MCPServerDef) -> bool:
    """Whether a live connection is the one `server` asks for: the same far end, told the same
    folders (a server reads its roots once, when it starts)."""
    return conn.key == connection_key(server) and conn.roots == tuple(server.roots or ())


async def list_all_tools(session: ClientSession, *, timeout: float = 20.0) -> list[Any]:
    """Every tool the server offers, following `nextCursor` to the end.

    `tools/list` is paginated in the MCP spec, and a single call returns only the first page. That
    was invisible while every server we talked to answered in one page. It stops being invisible the
    moment one does not: the tools past page one are simply missing, and for a connector-backed
    server they then fail the `include_tools` pin and vanish without an error anywhere. Atlassian's
    v2 endpoint with `?tools=all` is exactly that case, and Jira alone lists more than 30."""
    tools: list[Any] = []
    cursor: Optional[str] = None
    for _ in range(_MAX_TOOL_PAGES):
        page = await asyncio.wait_for(session.list_tools(cursor), timeout=timeout)
        tools.extend(page.tools)
        cursor = getattr(page, "nextCursor", None)
        if not cursor:
            return tools
    logger.warning(
        "mcp tools/list did not terminate after %d pages; using %d tools", _MAX_TOOL_PAGES, len(tools)
    )
    return tools


def read_tail(errfile: Optional[IO[str]]) -> Optional[str]:
    """The last few lines of a captured stderr file: the crash evidence, not the log."""
    if errfile is None:
        return None
    try:
        errfile.seek(0)
        text = errfile.read()
    except (OSError, ValueError):
        return None
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return None
    return "\n".join(lines[-_STDERR_TAIL_LINES:])[-_STDERR_TAIL_CHARS:]
