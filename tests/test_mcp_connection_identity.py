"""A live MCP connection belongs to an ENDPOINT, not just to a name.

Connections are cached by server name. That quietly assumes a name always means the same
far end — and it does not. A vendor migrates an endpoint (Atlassian's v1 -> v2), or the
same vendor publishes several and the user picks a different one (Postman's Essentials /
Full / Code generation, three URLs behind one connector).

What that cost, on 2026-09-08: the user switched Postman from Code generation to
Essentials. The config was re-seeded to `/minimal`, `verify` found the still-live `/code`
connection, ran `tools/list` on it, got a healthy answer and returned it. Connect reported
SUCCESS. No browser opened, no tokens were minted for the new endpoint, and the session
kept the 23 read-only tools of the endpoint the user had just chosen to leave.

Nothing was broken enough to notice: a green card, a plausible tool list, no error.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness.extensions.mcp.client import Conn as _Conn, MCPManager, connection_key
from harness.extensions.mcp.config import MCPServerDef


def _server(url: str, **over) -> MCPServerDef:
    return MCPServerDef(
        name="postman", transport="http", url=url, auth="oauth", **over
    )


def test_the_key_is_what_identifies_the_far_end():
    a = connection_key(_server("https://mcp.postman.com/minimal"))
    b = connection_key(_server("https://mcp.postman.com/code"))
    assert a != b, "two endpoints are two connections"
    assert a == connection_key(_server("https://mcp.postman.com/minimal"))


def test_cosmetic_config_does_not_invalidate_a_working_connection():
    """Approval flags and tool filters change nothing about who we are talking to.
    Including them would drop a live connection on every unrelated settings edit."""
    base = _server("https://mcp.postman.com/minimal")
    noisy = _server(
        "https://mcp.postman.com/minimal",
        requires_approval=False,
        include_tools=["a"],
        enabled=True,
    )
    assert connection_key(base) == connection_key(noisy)


@pytest.mark.asyncio
async def test_ensure_will_not_hand_back_a_connection_to_another_endpoint():
    mgr = MCPManager()
    old = _Conn(SimpleNamespace(), tools=["from /code"],
                key=connection_key(_server("https://mcp.postman.com/code")))
    mgr._conns[("", "postman")] = old

    connected: list[str] = []

    async def fake_serve(server, ready, *, interactive=False, key=("", "")):
        connected.append(server.url)
        ready.set_result(
            _Conn(SimpleNamespace(), tools=["from /minimal"], key=connection_key(server))
        )

    mgr._serve = fake_serve  # type: ignore[assignment]
    conn = await mgr.ensure(_server("https://mcp.postman.com/minimal"))

    assert connected == ["https://mcp.postman.com/minimal"], "it must reconnect"
    assert conn.tools == ["from /minimal"]
    assert old.shutdown.is_set(), "the old endpoint's connection is torn down"


@pytest.mark.asyncio
async def test_ensure_still_reuses_the_connection_it_already_has():
    """The cache has to keep working, or every turn pays a fresh connect."""
    mgr = MCPManager()
    server = _server("https://mcp.postman.com/minimal")
    live = _Conn(SimpleNamespace(), tools=["t"], key=connection_key(server))
    mgr._conns[("", "postman")] = live

    async def fake_serve(server, ready, *, interactive=False, key=("", "")):  # pragma: no cover
        raise AssertionError("must not reconnect a healthy, matching connection")

    mgr._serve = fake_serve  # type: ignore[assignment]
    assert await mgr.ensure(server) is live


@pytest.mark.asyncio
async def test_verify_does_not_health_check_the_wrong_server():
    """The bug in its exact shape: `verify` round-tripped the OLD connection, the
    round-trip succeeded, and success was reported for an endpoint nobody asked about."""
    mgr = MCPManager()
    listed: list[str] = []

    class _Session:
        async def list_tools(self, cursor=None):
            listed.append("old endpoint was queried")
            return SimpleNamespace(tools=["from /code"], nextCursor=None)

    old = _Conn(_Session(), tools=["from /code"],
                key=connection_key(_server("https://mcp.postman.com/code")))
    mgr._conns[("", "postman")] = old

    async def fake_serve(server, ready, *, interactive=False, key=("", "")):
        ready.set_result(
            _Conn(SimpleNamespace(), tools=["from /minimal"], key=connection_key(server))
        )

    mgr._serve = fake_serve  # type: ignore[assignment]
    conn = await mgr.verify(_server("https://mcp.postman.com/minimal"), interactive=True)

    assert listed == [], "the old endpoint must not be the thing that gets health-checked"
    assert conn.tools == ["from /minimal"]


@pytest.mark.asyncio
async def test_verify_still_round_trips_a_matching_connection():
    """Its original job (owner-hit 2026-08-21: Test-on-Live was a silent no-op) has to
    survive the fix."""
    mgr = MCPManager()
    server = _server("https://mcp.postman.com/minimal")
    listed: list[str] = []

    class _Session:
        async def list_tools(self, cursor=None):
            listed.append("checked")
            return SimpleNamespace(tools=["fresh"], nextCursor=None)

    mgr._conns[("", "postman")] = _Conn(_Session(), tools=["stale"], key=connection_key(server))
    conn = await mgr.verify(server)
    assert listed == ["checked"]
    assert conn.tools == ["fresh"], "the tool set is refreshed, not just accepted"


@pytest.mark.asyncio
async def test_disconnect_removes_the_connection_not_just_flags_it():
    """Disconnect used to set `shutdown` and leave the entry in the cache until the
    serving task got round to unwinding. A Connect pressed inside that window — which is
    precisely what a person does — was handed the revoked connection: `verify` saw a
    live entry, round-tripped it happily, and reported success with no sign-in and no
    tokens (owner-hit 2026-09-08)."""
    mgr = MCPManager()
    server = _server("https://mcp.postman.com/minimal")
    live = _Conn(SimpleNamespace(), tools=["t"], key=connection_key(server))
    mgr._conns[("", "postman")] = live
    mgr._tasks[("", "postman")] = SimpleNamespace()  # stand-in for the serving task

    mgr.forget("postman")

    assert ("", "postman") not in mgr._conns, "a revoked connection must not be reachable"
    assert ("", "postman") not in mgr._tasks
    assert live.shutdown.is_set(), "and the transport is still told to close"


def test_disconnecting_a_connector_forgets_its_live_connection(tmp_path):
    """The manager path, end to end: the API's disconnect must reach `forget`."""
    from harness.interface.server import SessionManager

    mgr = SessionManager(data_dir=tmp_path / "data")
    server = _server("https://mcp.postman.com/minimal")
    live = _Conn(SimpleNamespace(), tools=["t"], key=connection_key(server))
    mgr.mcp._conns[("", "postman")] = live

    mgr.disconnect_connector("postman")

    assert ("", "postman") not in mgr.mcp._conns
    assert live.shutdown.is_set()
