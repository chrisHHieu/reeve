"""What a session is told a server offers does not follow that server's connection.

Measured on the session store, 2026-10-06: the tool list leads every request, and it
changed 40 times in four sessions because an engine rebuilt after a restart or a long idle
found a server not connected yet. The tools left, the turn ran without them, and they came
back on the next turn, each change a full-price read of the whole conversation.

Pinned here: a server that has answered before is stated from the list it answered with,
the turn does not wait for it, and it does not leave the list because it is slow or down.
What still removes it is configuration, and a server that needs a person to sign in.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.extensions.mcp import MCP_GROUP
from harness.extensions.mcp.config import MCPServerDef
from harness.extensions.mcp.oauth import InteractiveAuthRequired
from harness.extensions.mcp.tool_cache import ToolListCache
from harness.interface.server.manager import SessionManager
from harness.tools.registry import ToolRegistry


class _Tool:
    def __init__(self, name: str, *, read_only: bool = False) -> None:
        self.name = name
        self.description = f"does {name}"
        self.inputSchema = {"type": "object", "properties": {"q": {"type": "string"}}}
        self.annotations = SimpleNamespace(readOnlyHint=True) if read_only else None


class _Conn:
    def __init__(self, tools: list[str]) -> None:
        self.tools = [_Tool(t) for t in tools]
        self.shutdown = threading.Event()


class _Engine:
    def __init__(self) -> None:
        self.registry = ToolRegistry()
        self.notices: list[tuple[str, str]] = []

    def _append_notice(self, kind: str, text: str, **_kw) -> None:
        self.notices.append((kind, text))


def _server(name: str = "docs", url: str = "https://example.test/mcp", **kw) -> MCPServerDef:
    return MCPServerDef(name=name, transport="http", url=url, **kw)


def _names(tools) -> list[str]:
    return sorted(t.__name__ for t in tools)


def _mcp_names(engine: _Engine) -> list[str]:
    return sorted(n for n in engine.registry.names() if n.startswith("mcp__"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A manager on its own state dir. `ensure` is a double whose behaviour a test sets:
    answer with tools, raise, or hang until released."""
    state = tmp_path / "state"
    monkeypatch.setenv("REEVE_STATE_DIR", str(state))
    state.mkdir(parents=True)
    (state / "mcp.json").write_text(
        json.dumps({"mcpServers": {"docs": {"type": "http", "url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    box = SimpleNamespace(
        state=state,
        tmp=tmp_path,
        tools={"docs": ["search", "fetch"]},
        failure={},
        hang={},
        calls=[],
        live={},
    )

    async def _ensure(server, *, scope: str = "", interactive: bool = False):
        box.calls.append(server.name)
        gate = box.hang.get(server.name)
        if gate is not None:
            await gate.wait()
        if server.name in box.failure:
            raise box.failure[server.name]
        conn = _Conn(box.tools[server.name])
        box.live[(scope, server.name)] = conn
        return conn

    def _manager() -> SessionManager:
        manager = SessionManager(data_dir=tmp_path / "data")
        monkeypatch.setattr(manager.mcp, "ensure", _ensure)
        monkeypatch.setattr(manager.mcp, "_conns", box.live)
        return manager

    box.manager = _manager
    return box


# -- the kept list ------------------------------------------------------------------------


def test_a_kept_list_reads_back_as_the_tools_it_was(tmp_path):
    cache = ToolListCache(tmp_path)
    server = _server()
    assert cache.save(server, [_Tool("search", read_only=True), _Tool("write")]) is True

    kept = cache.load(server)

    assert [t.name for t in kept] == ["search", "write"]
    assert kept[0].description == "does search"
    assert kept[0].inputSchema["properties"] == {"q": {"type": "string"}}
    assert kept[0].annotations.readOnlyHint is True
    assert kept[1].annotations is None


def test_a_list_kept_for_one_endpoint_is_not_another_endpoints(tmp_path):
    cache = ToolListCache(tmp_path)
    cache.save(_server(), [_Tool("search")])

    assert cache.load(_server(url="https://moved.test/mcp")) is None
    assert cache.load(_server(name="other")) is None


def test_nothing_usable_reads_as_nothing(tmp_path):
    cache = ToolListCache(tmp_path)
    server = _server()
    assert cache.load(server) is None
    assert cache.save(server, []) is False  # a server that listed nothing is not remembered
    assert cache.load(server) is None
    cache.save(server, [_Tool("search")])
    next(tmp_path.glob("*.json")).write_text("{not json", encoding="utf-8")
    assert cache.load(server) is None


def test_the_file_holds_no_header_value(tmp_path):
    """The endpoint is what the list belongs to, and its headers can carry a credential."""
    cache = ToolListCache(tmp_path)
    server = _server(headers={"Authorization": "Bearer made-up-token-value"})
    cache.save(server, [_Tool("search")])

    text = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")

    assert "made-up-token-value" not in text
    assert "example.test" not in text
    assert cache.load(server) is not None


# -- arming -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_first_connect_keeps_the_list_it_answered_with(env):
    manager = env.manager()

    await manager.arm_mcp("s1")

    assert [t.name for t in manager.mcp_tool_lists.load(_server())] == ["search", "fetch"]


@pytest.mark.asyncio
async def test_a_known_server_is_stated_without_waiting_for_its_connection(env):
    """The restart: a new process, the server still starting. The session is told the
    tools at once and the turn is not held."""
    await env.manager().arm_mcp("s1")  # an earlier process connected and kept the list
    env.live.clear()
    env.hang["docs"] = asyncio.Event()  # this time the connect does not finish
    restarted = env.manager()

    began = time.monotonic()
    tools = await restarted.arm_mcp("s1")

    assert time.monotonic() - began < 1.0
    assert _names(tools) == ["mcp__docs__fetch", "mcp__docs__search"]
    assert all(getattr(t, "__tool_group__", None) == MCP_GROUP for t in tools)
    env.hang["docs"].set()


@pytest.mark.asyncio
async def test_a_server_never_seen_is_still_waited_for_and_joins_later(env, monkeypatch):
    monkeypatch.setattr("harness.interface.server.manager.mcp_arming.MCP_ARM_BUDGET_S", 0.05)
    env.hang["docs"] = asyncio.Event()
    manager = env.manager()

    assert await manager.arm_mcp("s1") == []  # nothing is known about it yet

    env.hang["docs"].set()
    await asyncio.sleep(0.05)
    assert _names(await manager.arm_mcp("s1")) == ["mcp__docs__fetch", "mcp__docs__search"]


@pytest.mark.asyncio
async def test_a_live_session_keeps_its_tools_when_the_connection_is_gone(env):
    """A connection that dropped changes the signature, so the session re-arms. That used
    to be the moment its tools were taken off the list."""
    manager = env.manager()
    engine = _Engine()
    manager._engines["s1"] = engine
    await manager.arm_mcp("s1")
    assert _mcp_names(engine) == ["mcp__docs__fetch", "mcp__docs__search"]

    env.live.clear()
    env.hang["docs"] = asyncio.Event()
    await manager.arm_mcp("s1")

    assert _mcp_names(engine) == ["mcp__docs__fetch", "mcp__docs__search"]
    env.hang["docs"].set()


@pytest.mark.asyncio
async def test_a_known_server_that_fails_keeps_its_tools_and_says_so(env):
    await env.manager().arm_mcp("s1")
    env.live.clear()
    env.failure["docs"] = RuntimeError("could not reach docs")
    manager = env.manager()
    engine = _Engine()
    manager._engines["s1"] = engine

    await manager.arm_mcp("s1")
    await asyncio.sleep(0)  # the connect the arm did not wait for ends
    await asyncio.sleep(0)

    assert _mcp_names(engine) == ["mcp__docs__fetch", "mcp__docs__search"]
    assert "could not reach docs" in manager._mcp_errors["docs"]
    # The failure reaches the transcript on the next turn, though nothing else changed.
    await manager.arm_mcp("s1")
    assert any("failed to start" in text for _kind, text in engine.notices)
    assert env.calls.count("docs") == 2  # the first process, then one attempt: not per turn


@pytest.mark.asyncio
async def test_a_server_that_needs_a_sign_in_is_not_offered_from_memory(env):
    await env.manager().arm_mcp("s1")
    env.live.clear()
    env.failure["docs"] = InteractiveAuthRequired("docs")
    manager = env.manager()

    first = await manager.arm_mcp("fresh")  # stated from the list, connect beside it
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert "sign-in required" in manager._mcp_errors["docs"]

    again = await manager.prepare_mcp_tools("fresh")  # the outcome is known now

    assert _names(first) == ["mcp__docs__fetch", "mcp__docs__search"]
    assert again == []


@pytest.mark.asyncio
async def test_a_server_taken_out_of_the_configuration_still_leaves(env):
    manager = env.manager()
    engine = _Engine()
    manager._engines["s1"] = engine
    await manager.arm_mcp("s1")

    (env.state / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    env.live.clear()
    await manager.arm_mcp("s1")

    assert _mcp_names(engine) == []


@pytest.mark.asyncio
async def test_a_newer_list_replaces_the_kept_one(env):
    await env.manager().arm_mcp("s1")
    env.live.clear()
    env.tools["docs"] = ["search", "fetch", "summarize"]
    manager = env.manager()

    await manager.arm_mcp("s1")  # stated from the old list; the connect runs beside it
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert [t.name for t in manager.mcp_tool_lists.load(_server())] == ["search", "fetch", "summarize"]


@pytest.mark.asyncio
async def test_a_stated_tool_connects_when_it_is_called(env, monkeypatch):
    """The other half of not waiting: the call is what needs the connection, and the
    callable hands the server definition to `MCPManager.call`, which connects first."""
    await env.manager().arm_mcp("s1")
    env.live.clear()
    env.hang["docs"] = asyncio.Event()
    manager = env.manager()
    seen: dict = {}

    async def _call(name, tool, arguments, *, scope="", timeout=None, server=None):
        seen.update(name=name, tool=tool, server=server)
        return {"ok": True}

    monkeypatch.setattr(manager.mcp, "call", _call)
    tools = await manager.arm_mcp("s1")
    search = next(t for t in tools if t.__name__ == "mcp__docs__search")

    result = await asyncio.to_thread(search, q="x")

    assert result == {"ok": True}
    assert seen["tool"] == "search" and seen["server"].name == "docs"
    env.hang["docs"].set()


@pytest.mark.asyncio
async def test_a_server_that_joins_late_brings_back_what_the_thread_had_loaded(env, monkeypatch):
    """A server never seen on this machine is waited for and can miss the turn. When its
    tools arrive, the ones the conversation had loaded are callable by name again at once,
    and stay off the tool list like any loaded tool."""
    from harness.tools.discovery import TOOL_DESCRIBE, discovery_tools

    monkeypatch.setattr("harness.interface.server.manager.mcp_arming.MCP_ARM_BUDGET_S", 0.05)
    env.hang["docs"] = asyncio.Event()
    manager = env.manager()
    engine = _Engine()
    engine.registry.set_progressive_discovery(True)
    engine.registry.register_all(discovery_tools(engine.registry))
    engine.messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c",
                    "type": "function",
                    "function": {"name": TOOL_DESCRIBE, "arguments": json.dumps({"name": "mcp__docs__search"})},
                }
            ],
        }
    ]
    manager._engines["s1"] = engine

    await manager.arm_mcp("s1")
    assert _mcp_names(engine) == []

    env.hang["docs"].set()
    await asyncio.sleep(0.05)
    env.live[("", "docs")] = _Conn(["search", "fetch"])  # the connect finished: the signature moves
    await manager.arm_mcp("s1")

    callable_now = engine.registry.directly_callable()
    assert "mcp__docs__search" in callable_now
    assert "mcp__docs__fetch" not in callable_now  # still behind the bridge: never loaded
    on_the_list = {schema["function"]["name"] for schema in engine.registry.schemas()}
    assert not {"mcp__docs__search", "mcp__docs__fetch"} & on_the_list
