"""The task that owns one server's connection from opening to closing.

One task does both the enter and the exit, because the SDK's transports use anyio cancel scopes that
must be entered and exited on the same task.
"""

from __future__ import annotations

import asyncio
import tempfile
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from ..config import MCPServerDef
from .connection import Conn, connection_key, list_all_tools, read_tail
from .transport import mcp_http_client


def _roots_answer(roots: list[str]):
    """The `roots/list` handler for a server given the session's folders, or None (no roots offered).

    Playwright MCP resolves a relative file name (`browser_take_screenshot` with `filename`) and keeps
    its snapshots against the client's first root, falling back to its own working directory. With no
    roots offered, a session without a project folder had its screenshots saved in the app's install
    folder, where the agent's file tools could not find them (2026-10-20)."""
    if not roots:
        return None
    from pathlib import Path

    from mcp import types

    answer = types.ListRootsResult(
        roots=[types.Root(uri=Path(path).as_uri(), name=Path(path).name or path) for path in roots]
    )

    async def list_roots(_context: Any) -> types.ListRootsResult:
        return answer

    return list_roots


class ServeMixin:
    def _secret_store(self):
        """The secret store, made on first use so library and CLI construction without secrets
        still works."""
        if self._secrets is None:
            from harness.safety.secrets import SecretStore

            self._secrets = SecretStore()
        return self._secrets

    def _http_credentials(self, server: MCPServerDef, interactive: bool) -> tuple[dict[str, str], Any]:
        """The headers and the httpx auth an HTTP server needs, by how it signs in.

        A device-flow token (RFC 8628, no client secret and no DCR) and a pasted API key are both
        plain bearers. Both are read from the secret store here and never carried in `mcp.json`,
        which is plain text and gets pasted between machines."""
        headers = dict(server.headers or {})
        auth = None
        if server.auth == "device":
            from .. import device_auth

            bearer = device_auth.bearer_header(server.name, self._secret_store())
            if bearer is None:
                raise RuntimeError(f"MCP server '{server.name}' is not signed in — connect it from its page")
            headers.update(bearer)
        if server.auth == "bearer":
            from .. import api_key_auth

            bearer = api_key_auth.api_key_header(server.name, self._secret_store())
            if bearer is None:
                raise RuntimeError(f"MCP server '{server.name}' has no API key — connect it from its page")
            headers.update(bearer)
        if server.auth == "oauth":
            from ..oauth import build_auth

            auth = build_auth(server.name, server.url, self._secret_store(), interactive=interactive)
        return headers, auth

    async def _open_http(self, stack: AsyncExitStack, server: MCPServerDef, interactive: bool):
        if not server.url:
            raise ValueError(f"MCP server '{server.name}' is http but has no url")
        headers, auth = self._http_credentials(server, interactive)
        read, write, *_ = await stack.enter_async_context(
            streamablehttp_client(
                server.url,
                headers=headers or None,
                auth=auth,
                httpx_client_factory=mcp_http_client,
            )
        )
        return read, write

    async def _open_stdio(self, stack: AsyncExitStack, server: MCPServerDef, errfile):
        params = StdioServerParameters(
            command=server.command, args=server.args, env=server.env or None, cwd=server.cwd
        )
        return await stack.enter_async_context(stdio_client(params, errlog=errfile))

    async def _serve(
        self,
        server: MCPServerDef,
        ready: asyncio.Future,
        *,
        interactive: bool = False,
        key: tuple[str, str],
    ) -> None:
        errfile = None
        try:
            async with AsyncExitStack() as stack:
                if server.transport == "http":
                    read, write = await self._open_http(stack, server, interactive)
                else:
                    if not server.command:
                        raise ValueError(f"MCP server '{server.name}' is stdio but has no command")
                    # Capture the child's stderr so a startup crash leaves evidence the UI can show.
                    # The SDK needs a real file descriptor here, and the file is held by this task
                    # so the evidence survives a failure while the transport opens.
                    errfile = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
                    read, write = await self._open_stdio(stack, server, errfile)
                session = await stack.enter_async_context(
                    ClientSession(read, write, list_roots_callback=_roots_answer(server.roots))
                )
                await session.initialize()
                conn = Conn(
                    session, await list_all_tools(session), connection_key(server), tuple(server.roots or ())
                )
                self._stderr_tails.pop(key, None)
                if not ready.done():
                    ready.set_result(conn)
                await conn.shutdown.wait()
        except Exception as exc:  # the connection or its initialization failed
            tail = read_tail(errfile)
            if tail:
                self._stderr_tails[key] = tail
            if not ready.done():
                ready.set_exception(exc)
        finally:
            if errfile is not None:
                try:
                    errfile.close()
                except OSError:
                    pass
            self._conns.pop(key, None)
            self._tasks.pop(key, None)
