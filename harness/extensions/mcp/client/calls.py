"""Calling a tool on a connected server, with the deadline held on the side that can cancel."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from ..call_errors import looks_disconnected, timeout_error
from ..config import MCPServerDef
from .connection import Conn
from .payload import result_payload

# How long one MCP tool call may take. It lives here and not in `mcp/tools.py` because this is the
# layer that can cancel. `tools.py` runs on a worker thread, and its budget can only stop the caller
# waiting. `tools.py` derives its own backstop from this value plus a margin, so the cancelling
# deadline always fires first.
CALL_TIMEOUT = 110.0


class CallsMixin:
    async def call(
        self,
        name: str,
        tool: str,
        arguments: Optional[dict[str, Any]],
        *,
        scope: str = "",
        timeout: Optional[float] = None,
        server: Optional[MCPServerDef] = None,
    ) -> Any:
        """Invoke one tool on a connected server.

        The deadline lives here, on the async side, because this is the only layer that can stop the
        work: `wait_for` cancels the in-flight request, so the session is free for the next call.
        The thread-side budget in `mcp/tools.py` only ever stopped the caller waiting, and an
        abandoned request kept running on the server loop, holding the session and visible to no one.

        Passing `server` gives a call a reconnect before it and one retry on a confirmed disconnect,
        so it survives a server that died quietly. Without it, the next tool call raised "not
        connected" and the session had to be told to re-arm. Two rules keep it honest. The reconnect
        happens before the call when the connection is already gone. The retry happens only when the
        failure looks like a disconnect and never on an ordinary tool error, because reconnecting
        there would hide a broken tool behind a reconnect loop. The retry shares the original
        deadline, so a reconnect cannot double the caller's wait."""
        key = self._key(scope, name)
        budget = CALL_TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + budget
        conn = self._conns.get(key)
        if conn is None:
            if server is None:
                raise RuntimeError(f"MCP server not connected: {name}")
            conn = await self.ensure(server, scope=scope)
        self.last_model_call[key] = time.monotonic()
        try:
            return await self._call_once(conn, name, tool, arguments, deadline, budget)
        except Exception as exc:
            if server is None or not looks_disconnected(exc):
                raise
            await self._drop(key)
            conn = await self.ensure(server, scope=scope)
            return await self._call_once(conn, name, tool, arguments, deadline, budget)

    async def _call_once(
        self,
        conn: Conn,
        name: str,
        tool: str,
        arguments: Optional[dict[str, Any]],
        deadline: float,
        budget: float,
    ) -> Any:
        # The wait is the time left on the caller's clock, and the message names the budget they
        # asked for. So a retry cannot stretch the wait past the deadline, and a caller who asked
        # for 0.4 s still hears "0.4s" when it expires.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise timeout_error(name, tool, budget)
        try:
            result = await asyncio.wait_for(conn.session.call_tool(tool, arguments or {}), timeout=remaining)
        except asyncio.TimeoutError:
            raise timeout_error(name, tool, budget) from None
        return result_payload(result)
