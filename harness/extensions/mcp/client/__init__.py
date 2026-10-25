"""`MCPManager`: our own thin async MCP client over the official `mcp` SDK.

It is async-native, with no `nest_asyncio` and no second event loop. Each server runs in a dedicated
asyncio task that opens the transport and the `ClientSession`, keeps them alive until shutdown, and
closes them in the same task (`serve.py`). That is required because the SDK's transports use anyio
cancel scopes that must be entered and exited on one task. Tool calls are awaited from any task on the
same loop, which is safe.

Tool execution from the synchronous `ToolRegistry` bridges back here through
`run_coroutine_threadsafe`, in `harness/extensions/mcp/tools.py`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from ..config import MCPServerDef
from .calls import CALL_TIMEOUT, CallsMixin
from .connection import Conn, connection_key, list_all_tools, same_endpoint
from .payload import result_payload
from .serve import ServeMixin
from .transport import MCP_USER_AGENT, UserAgentTransport, mcp_http_client

__all__ = [
    "CALL_TIMEOUT",
    "MCPManager",
    "MCP_USER_AGENT",
    "UserAgentTransport",
    "connection_key",
    "list_all_tools",
    "mcp_http_client",
    "result_payload",
]


class MCPManager(ServeMixin, CallsMixin):
    """Owns persistent MCP connections keyed by (scope, server name), connecting lazily.

    The scope is the owning session id for servers that must not be shared, such as the built-in
    browser, since every live session gets its own. It is `""` for everything else (remote servers,
    sidecars). Two scopes of the same server are two independent connections, and the tool names stay
    identical because each engine arms its own list."""

    def __init__(self, secrets: Any = None) -> None:
        self._conns: dict[tuple[str, str], Conn] = {}
        # The monotonic time of the last model-driven tool call for each server. `call()` is reached
        # only from the model's tools, since read-only mirrors poll the session directly and must not
        # count. Surfaces like the rail's browser mirror gate on this: mirroring may watch an active
        # browser but must never be what wakes it.
        self.last_model_call: dict[tuple[str, str], float] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._stderr_tails: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()
        # The secret store for OAuth servers' token persistence (mcp/oauth). It is made on first use,
        # so library and CLI construction without secrets keeps working.
        self._secrets = secrets

    @staticmethod
    def _key(scope: str, name: str) -> tuple[str, str]:
        """The connection identity: the owning session (`""` means shared) and the server name."""
        return (scope or "", name)

    async def ensure(self, server: MCPServerDef, *, scope: str = "", interactive: bool = False) -> Conn:
        """A live connection for `server` in `scope`, connecting once if needed.

        `interactive=True`, for explicit connect actions only, lets an OAuth server run the browser
        sign-in. The default refuses it: stored tokens and silent refresh still work, but a server
        that insists on authorizing again raises `InteractiveAuthRequired` and does not take over the
        person's browser."""
        key = self._key(scope, server.name)
        async with self._lock:
            existing = self._conns.get(key)
            if existing is not None and same_endpoint(existing, server):
                return existing
            if existing is not None:
                # The same scope and name but a different endpoint. Returning this would answer
                # with the tool set of a server the caller is not asking for.
                await self._drop(key)
            ready: asyncio.Future = asyncio.get_running_loop().create_future()
            self._tasks[key] = asyncio.create_task(
                self._serve(server, ready, interactive=interactive, key=key)
            )
            conn = await ready  # connection errors propagate from here
            self._conns[key] = conn
            return conn

    async def tools(self, server: MCPServerDef, *, scope: str = "") -> list[Any]:
        return (await self.ensure(server, scope=scope)).tools

    async def verify(self, server: MCPServerDef, *, scope: str = "", interactive: bool = False) -> Conn:
        """A real health check, for explicit Test actions.

        `ensure` returns a cached connection untouched, which made Test on a live server a silent
        no-op that could not detect a dead one (2026-08-21). Here a cached connection is round-tripped
        with `tools/list`, which also refreshes the tool set, and a dead one is torn down and
        reconnected fresh."""
        key = self._key(scope, server.name)
        conn = self._conns.get(key)
        if conn is not None and not same_endpoint(conn, server):
            # The definition moved under the connection, through a vendor endpoint migration or the
            # person picking a different endpoint the same vendor publishes. Health-checking the old
            # one would pass and report success for a server nobody asked about. The Postman variant
            # switch reported "connected", opened no browser and kept the previous endpoint's 23
            # tools (2026-09-08).
            await self._drop(key)
            conn = None
        if conn is not None:
            try:
                conn.tools = await list_all_tools(conn.session)
                return conn
            except Exception:
                await self._drop(key)
        return await self.ensure(server, scope=scope, interactive=interactive)

    def forget(self, name: str) -> None:
        """Synchronous teardown of every scope of `name`, for disconnect and delete.

        Flagging `shutdown` alone is not enough. The entry stays in `_conns` until the serving task
        gets round to unwinding, and a connect arriving in that window is handed the connection that
        was just revoked. That window is exactly what a person does, Disconnect and then Connect, and
        it reported success with no sign-in and no tokens (2026-09-08). Removing the entries here
        closes it. The tasks still unwind on their own, which they must, since the transport can only
        be closed by the task that opened it."""
        self._release([key for key in self._conns if key[1] == name])

    def forget_scope(self, scope: str) -> None:
        """Synchronous teardown of everything a session owns, when the session is deleted.

        Only per-session servers live in a scope, so this is how a deleted session's browser is
        released and does not sit idle until the process ends."""
        self._release([key for key in self._conns if key[0] == (scope or "")])

    def _release(self, keys: list[tuple[str, str]]) -> None:
        for key in keys:
            conn = self._conns.pop(key, None)
            if conn is not None:
                conn.shutdown.set()
            self._tasks.pop(key, None)

    async def _drop(self, key: tuple[str, str]) -> None:
        """Tear one connection down and wait for its task to unwind, so the next connect starts from
        nothing. The transport must close on the task that opened it (anyio cancel scopes), which is
        why this waits and does not just forget the entry."""
        conn = self._conns.get(key)
        if conn is not None:
            conn.shutdown.set()
        task = self._tasks.pop(key, None)
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except Exception:
                task.cancel()
        self._conns.pop(key, None)  # `_serve` pops it too, and this makes sure

    def live_names(self) -> set[str]:
        """The servers with a connection open now, in any scope. It is part of a session's MCP
        signature: a server repaired and connected from the Connectors page changes this set without
        changing any config, and that has to be enough to re-arm an open session."""
        return {key[1] for key in self._conns}

    def connected(self, name: str, *, scope: Optional[str] = None) -> bool:
        """Whether `name` has a live connection, in one scope or in any."""
        if scope is None:
            return any(key[1] == name for key in self._conns)
        return self._key(scope, name) in self._conns

    def conn(self, name: str, *, scope: Optional[str] = None) -> Optional[Conn]:
        """The live connection for `name`, preferring `scope`. With no scope it falls back to any,
        for surfaces that only need to know that something is live."""
        if scope is not None:
            return self._conns.get(self._key(scope, name))
        for key, conn in self._conns.items():
            if key[1] == name:
                return conn
        return None

    def tool_count(self, name: str, *, scope: Optional[str] = None) -> Optional[int]:
        """How many tools a live connection serves, or None when it is not connected."""
        conn = self.conn(name, scope=scope)
        return len(conn.tools) if conn is not None else None

    def last_stderr(self, name: str, *, scope: str = "") -> Optional[str]:
        """The stderr tail from the most recent failed startup of `name`, if any. A scoped lookup
        falls back to any scope, so a diagnostic is never lost to the scope key."""
        tail = self._stderr_tails.get(self._key(scope, name))
        if tail is None:
            tail = next((value for key, value in self._stderr_tails.items() if key[1] == name), None)
        return tail

    async def aclose(self) -> None:
        for conn in self._conns.values():
            conn.shutdown.set()
        for task in list(self._tasks.values()):
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
        self._conns.clear()
        self._tasks.clear()
