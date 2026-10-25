"""A session uses the MCP servers that are enabled, from its next turn on.

That sentence is the whole change, and it used not to be true in three different ways:

  · MCP was attached only at the instant an engine was BUILT. `prepare_mcp_tools` returned
    `[]` for any session already in `_engines`, and nothing else ever re-armed one — so a
    server added while a chat was open never reached it. Not once, not ever. The Connectors
    page said "Live", the Access rail said nothing, and the agent said it had no MCP tools:
    three surfaces, three answers, none of them true (owner-hit 2026-08-26).
  · Four other paths built engines with no MCP tools AT ALL — durable resume, out-of-band
    delivery, a Slack mention's brand-new session, and a scheduled run. So "open a new
    session" was not even a rule that worked: those sessions were new and still had none.
  · The Sources panel was built from the connector registry alone, so a raw MCP server was
    structurally unable to appear in the list of what a session can touch.

The tests below pin the rule AND the two things it must not cost: a dead server must not
make every turn pay a connection timeout, and a remote peer must not be able to take the
name of a local tool by choosing it.
"""

from __future__ import annotations

import asyncio
import sys
import json
import threading
from pathlib import Path

import pytest

from harness.extensions.mcp import MCP_GROUP
from harness.session.session_log import NULL_LOG
from harness.interface.server.manager import SessionManager
from harness.tools.registry import ToolRegistry


# -- doubles -------------------------------------------------------------------------


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"does {name}"
        self.inputSchema = {"type": "object", "properties": {}}


class _Conn:
    def __init__(self, tools: list[str]) -> None:
        self.tools = [_Tool(t) for t in tools]
        self.shutdown = threading.Event()


class _Engine:
    """Only what arming touches. A real TurnEngine would drag a provider in."""

    def __init__(self) -> None:
        self.registry = ToolRegistry()
        self.notices: list[tuple[str, str]] = []

    def _append_notice(self, kind: str, text: str, **_kw) -> None:
        self.notices.append((kind, text))


def _remote(name: str):
    """A callable shaped like `build_callables` output: an MCP tool's schema comes from
    the server, so it never goes through signature inference."""

    def _fn(**_kw):
        return None

    _fn.__name__ = name
    _fn.__tool_schema__ = {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object", "properties": {}}},
    }
    _fn.__tool_group__ = MCP_GROUP
    return _fn


def _local(name: str):
    """A built-in: a real signature, so aisuite can derive its schema — the same way every
    tool in `harness/tools/` is registered."""

    def _fn(path: str = "") -> str:
        return "local"

    _fn.__name__ = name
    return _fn


def _write_config(state: Path, servers: dict) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A manager on an isolated state dir, with connections faked at MCPManager.ensure."""
    state = tmp_path / "state"
    monkeypatch.setenv("REEVE_STATE_DIR", str(state))
    _write_config(
        state,
        {"docs": {"type": "http", "url": "https://example.test/mcp", "enabled": True}},
    )
    manager = SessionManager(data_dir=tmp_path / "data")

    calls: list[str] = []
    tools_by_server = {"docs": ["search", "fetch"]}
    live: dict[str, _Conn] = {}

    async def _ensure(server, *, scope: str = "", interactive: bool = False):
        calls.append(server.name)
        names = tools_by_server.get(server.name)
        if names is None:
            raise RuntimeError(f"could not reach {server.name}")
        conn = _Conn(names)
        live[(scope, server.name)] = conn
        return conn

    monkeypatch.setattr(manager.mcp, "ensure", _ensure)
    # `live_names` and the Sources row read the real pool; keep the double consistent
    # with it, or the signature would never notice a server coming up.
    monkeypatch.setattr(manager.mcp, "_conns", live)
    return {
        "manager": manager,
        "state": state,
        "calls": calls,
        "tools": tools_by_server,
        "live": live,
    }


def _mcp_names(engine: _Engine) -> list[str]:
    return sorted(n for n in engine.registry.names() if n.startswith("mcp__"))


# -- the bug, exactly as reported ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_server_added_mid_conversation_reaches_the_open_session(env):
    """The report: configure MCP, go back to the chat, agent says it has none."""
    manager, state = env["manager"], env["state"]
    engine = _Engine()
    manager._engines["s1"] = engine

    await manager.arm_mcp("s1")
    assert _mcp_names(engine) == ["mcp__docs__fetch", "mcp__docs__search"]

    # Now add a second server the way the Connectors page does — while the session is open.
    env["tools"]["notes"] = ["append"]
    _write_config(
        state,
        {
            "docs": {"type": "http", "url": "https://example.test/mcp", "enabled": True},
            "notes": {"type": "http", "url": "https://notes.test/mcp", "enabled": True},
        },
    )
    await manager.arm_mcp("s1")

    assert "mcp__notes__append" in _mcp_names(engine)
    # …and the conversation was NOT thrown away to get there.
    assert manager._engines["s1"] is engine


@pytest.mark.asyncio
async def test_a_server_removed_mid_conversation_leaves_the_open_session(env):
    """The direction that used to lie to the AGENT: the connection was cut but the tool
    stayed in the schema, so the model saw a tool that no longer existed, called it, and
    got `MCP server not connected` for its trouble."""
    manager, state = env["manager"], env["state"]
    engine = _Engine()
    manager._engines["s1"] = engine
    await manager.arm_mcp("s1")
    assert _mcp_names(engine)

    _write_config(state, {})
    env["live"].clear()
    await manager.arm_mcp("s1")

    assert _mcp_names(engine) == []


@pytest.mark.asyncio
async def test_disabling_a_server_is_enough(env):
    """`enabled: false` is the switch on the Connectors page, and it must act like removal
    for a live session — not only for the next one."""
    manager, state = env["manager"], env["state"]
    engine = _Engine()
    manager._engines["s1"] = engine
    await manager.arm_mcp("s1")
    assert _mcp_names(engine)

    _write_config(
        state,
        {"docs": {"type": "http", "url": "https://example.test/mcp", "enabled": False}},
    )
    env["live"].clear()
    await manager.arm_mcp("s1")
    assert _mcp_names(engine) == []


# -- what it must not cost -----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unchanged_configuration_touches_nothing(env):
    """This runs before EVERY turn. If it reconnected each time, the fix would be a tax on
    every message — so "nothing changed" has to be decided without the network."""
    manager = env["manager"]
    engine = _Engine()
    manager._engines["s1"] = engine
    await manager.arm_mcp("s1")
    before = len(env["calls"])

    for _ in range(5):
        await manager.arm_mcp("s1")

    assert len(env["calls"]) == before
    assert _mcp_names(engine) == ["mcp__docs__fetch", "mcp__docs__search"]


@pytest.mark.asyncio
async def test_a_dead_server_is_not_redialled_every_turn(env):
    """The regression this fix could easily have shipped: a broken server turning every
    single turn into a connection timeout. It is attempted once, then left alone until
    something about it actually changes."""
    manager, state = env["manager"], env["state"]
    _write_config(
        state,
        {"gone": {"type": "http", "url": "https://gone.test/mcp", "enabled": True}},
    )
    engine = _Engine()
    manager._engines["s1"] = engine

    await manager.arm_mcp("s1")  # attempts, fails, records
    assert env["calls"] == ["gone"]
    assert _mcp_names(engine) == []

    for _ in range(4):
        await manager.arm_mcp("s1")
    assert env["calls"] == ["gone"]  # still one attempt, not five


@pytest.mark.asyncio
async def test_a_repaired_server_is_picked_up_without_a_new_session(env):
    """The other half of not-retrying: when the user fixes a server and connects it from
    the Connectors page, the live connection appears — and that alone must re-arm the open
    session, because nothing in the config file changed."""
    manager, state = env["manager"], env["state"]
    _write_config(
        state,
        {"flaky": {"type": "http", "url": "https://flaky.test/mcp", "enabled": True}},
    )
    engine = _Engine()
    manager._engines["s1"] = engine
    await manager.arm_mcp("s1")
    assert _mcp_names(engine) == []

    # The Connectors page's Connect succeeds: a live connection now exists.
    env["tools"]["flaky"] = ["ping"]
    env["live"][("", "flaky")] = _Conn(["ping"])
    manager._mcp_errors.pop("flaky", None)

    await manager.arm_mcp("s1")
    assert _mcp_names(engine) == ["mcp__flaky__ping"]


@pytest.mark.asyncio
async def test_a_remote_server_cannot_take_a_local_tool_name(env):
    """Tool names come from a REMOTE peer. Sanitised MCP names are prefixed so this cannot
    happen today, but "cannot happen today" is not a boundary — a shadowed `write_file`
    would be a remote redefinition of a local tool, chosen by picking a string."""
    manager = env["manager"]
    engine = _Engine()
    write_file = _local("write_file")
    engine.registry.register(write_file)
    manager._engines["s1"] = engine

    added, removed, refused = engine.registry.sync_group(MCP_GROUP, [_remote("write_file")])
    assert refused == ["write_file"]
    assert added == [] and removed == []
    assert engine.registry.get("write_file").func is write_file
    assert engine.registry.get("write_file").group is None


# -- the paths that never had MCP at all ---------------------------------------------


@pytest.mark.asyncio
async def test_a_session_with_no_engine_yet_gets_its_tools_to_pass_along(env):
    """The build path still works the old way — the callables come BACK, for `get_engine`
    to register — because at that moment there is no engine to attach them to."""
    manager = env["manager"]
    tools = await manager.arm_mcp("brand-new")
    assert sorted(t.__name__ for t in tools) == [
        "mcp__docs__fetch",
        "mcp__docs__search",
    ]
    # And they carry their origin, so a later resync can find them without name-sniffing.
    assert all(getattr(t, "__tool_group__", None) == MCP_GROUP for t in tools)


@pytest.mark.asyncio
async def test_a_dropped_engine_re_arms_from_scratch(env):
    """Rebind / promotion / a moved folder all evict the engine. The armed signature must
    not outlive it, or the rebuilt engine would be told nothing had changed."""
    manager = env["manager"]
    engine = _Engine()
    manager._engines["s1"] = engine
    await manager.arm_mcp("s1")
    assert manager._mcp_armed.get("s1")

    manager._engines.pop("s1")
    tools = await manager.arm_mcp("s1")
    assert len(tools) == 2  # returned for the rebuild, not silently skipped


# -- the panel that could not show them ----------------------------------------------


def test_the_sources_list_names_mcp_servers(env):
    """The rail is the surface a person checks to be sure. It listed connectors only, so a
    raw MCP server could not appear in it however correctly it was working."""
    manager = env["manager"]
    view = manager.session_connections_view("s1")
    rows = {r["connector"]: r for r in view["connected"]}

    assert "docs" in rows, "an enabled MCP server must appear as a source"
    assert rows["docs"]["kind"] == "mcp"
    # No per-session mute exists for MCP; the row must not offer one.
    assert rows["docs"]["manageable"] is False


def test_a_connected_server_reports_the_same_tool_count_as_the_connectors_page(env):
    manager = env["manager"]
    env["live"][("", "docs")] = _Conn(["search", "fetch"])
    rows = {r["connector"]: r for r in manager.session_connections_view("s1")["connected"]}
    assert rows["docs"]["detail"] == "2 tools"


def test_an_unreachable_server_is_not_described_as_working(env):
    manager = env["manager"]
    manager._mcp_errors["docs"] = "connection refused"
    rows = {r["connector"]: r for r in manager.session_connections_view("s1")["connected"]}
    assert rows["docs"]["detail"] == "not connected"


def test_connector_rows_still_say_what_they_are(env):
    """The new field must not arrive by making the old rows ambiguous."""
    manager = env["manager"]
    for row in manager.session_connections_view("s1")["connected"]:
        assert row["kind"] in {"connector", "mcp"}
        if row["kind"] == "connector":
            assert "manageable" not in row  # connectors keep their toggle


def test_a_broken_config_does_not_blank_the_panel(env, monkeypatch):
    """A panel that goes empty on an unrelated error teaches people to distrust it."""
    manager = env["manager"]

    def _boom(*_a, **_k):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(manager, "_mcp_plan", _boom)
    view = manager.session_connections_view("s1")
    assert isinstance(view["connected"], list)  # connectors still listed


# -- registry mechanics --------------------------------------------------------------


def test_sync_group_leaves_other_tools_alone():
    registry = ToolRegistry()
    registry.register(_local("read_file"))
    one, two = _remote("mcp__a__one"), _remote("mcp__a__two")

    registry.sync_group("mcp", [one, two])
    assert set(registry.names()) == {"read_file", "mcp__a__one", "mcp__a__two"}

    added, removed, refused = registry.sync_group("mcp", [one])
    assert added == [] and removed == ["mcp__a__two"] and refused == []
    assert set(registry.names()) == {"read_file", "mcp__a__one"}


def test_a_group_member_keeps_its_schema_and_contract_on_resync():
    """Re-registering must not quietly downgrade a tool to a signature-derived schema —
    an MCP tool's schema comes from the server, and `**kwargs` would erase it."""
    registry = ToolRegistry()
    one = _remote("mcp__a__one")
    registry.sync_group("mcp", [one])
    registry.sync_group("mcp", [one])
    spec = registry.get("mcp__a__one")
    assert spec.schema["function"]["name"] == "mcp__a__one"
    assert spec.group == "mcp"


def test_the_group_travels_with_the_callable():
    """The manager never says "group=mcp" when building — the tool carries it. One place
    to be right, instead of one place per caller to remember."""
    registry = ToolRegistry()
    registry.register_all([_remote("mcp__a__one")])
    assert registry.get("mcp__a__one").group == MCP_GROUP


# -- MCP may be broken; it may not cost a session -------------------------------------


@pytest.mark.asyncio
async def test_an_unreadable_config_does_not_swallow_the_turn(env, monkeypatch):
    """Arming now runs in front of EVERY turn, and the WS turn path is a bare try/finally —
    so anything raising here would drop the user's message with nothing shown. The rule the
    per-server connect already follows applies to the whole step: record it, never block."""
    manager = env["manager"]
    engine = _Engine()
    manager._engines["s1"] = engine

    def _boom(*_a, **_k):
        raise ValueError("mcp.json is not what it claims to be")

    monkeypatch.setattr(manager, "_mcp_plan", _boom)
    assert await manager.arm_mcp("s1") == []  # returned, not raised


@pytest.mark.asyncio
async def test_a_broken_config_does_not_block_a_session_being_built(env, monkeypatch):
    """Same rule on the build path: a session must still open."""
    manager = env["manager"]

    def _boom(*_a, **_k):
        raise ValueError("unreadable")

    monkeypatch.setattr(manager, "_mcp_plan", _boom)
    assert await manager.arm_mcp("brand-new") == []


# -- the session's toolset is no longer fixed at build time ---------------------------


class _Registry(ToolRegistry):
    """A registry whose names can change between iterations, as arming MCP makes them."""


class _RecordingLog:
    """`_note_tool_changes` only ever calls `append`; NULL_LOG keeps nothing, and what this
    test is about is precisely what got kept."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict]] = []

    def append(self, type: str, **data) -> None:
        self.entries.append((type, data))


def _bare_engine(log=None):
    """`__new__` on purpose: a real TurnEngine needs a provider and a workspace, and these
    tests are about one method. The cost is that every attribute it touches is set here."""
    from harness.loop import TurnEngine

    engine = TurnEngine.__new__(TurnEngine)
    engine.messages = []
    engine.log = log or NULL_LOG
    engine._turn_no = 1
    engine._step_no = 1
    engine.model = "m"
    engine.registry = ToolRegistry()
    engine._tool_signature = None
    engine._system_logged = None
    return engine


def test_a_change_in_the_toolset_is_recorded_in_the_log():
    """Arming MCP mid-conversation swaps what the agent can do, and a reader asking "when
    did it get that?" needs somewhere to look. That somewhere is the request/header — it
    carries the whole schema set, so two consecutive entries answer both what appeared and
    what went away."""
    log = _RecordingLog()
    engine = _bare_engine(log)
    engine.registry.register(_local("read_file"))

    engine._note_tool_changes()
    assert [t for t, _ in log.entries] == ["request/header"]
    assert log.entries[0][1]["reason"] == "initial"

    engine.registry.register(_remote("mcp__docs__search"))
    engine._note_tool_changes()
    assert len(log.entries) == 2
    kind, data = log.entries[1]
    assert kind == "request/header" and data["reason"] == "tools"
    assert {t["function"]["name"] for t in data["tools"]} == {
        "read_file",
        "mcp__docs__search",
    }

    engine._note_tool_changes()
    assert len(log.entries) == 2, "an unchanged set must not stamp a header every turn"


def test_a_removed_tool_is_recorded_too():
    """The header is the current set, not a diff, so a removal has to produce one as well —
    otherwise the last header keeps claiming a tool the agent no longer has."""
    log = _RecordingLog()
    engine = _bare_engine(log)
    engine.registry.register(_remote("mcp__docs__search"))
    engine._tool_signature = frozenset({"mcp__docs__search", "mcp__docs__fetch"})

    engine._note_tool_changes()
    assert [t for t, _ in log.entries] == ["request/header"]
    assert {t["function"]["name"] for t in log.entries[0][1]["tools"]} == {
        "mcp__docs__search"
    }


def test_loading_a_deferred_tool_leaves_the_toolset_and_the_log_alone():
    """Loading makes a tool callable by its name and puts nothing on the wire, so no
    header is written. One written anyway would tell the cache proofs the tool list
    changed when the request did not. The schema the model reads is the describe result, a
    `tool/result` row the log already holds."""
    log = _RecordingLog()
    engine = _bare_engine(log)
    engine.registry.register(_local("read_file"))
    deferred = _remote("mcp__docs__search")
    deferred.__tool_defer__ = True
    engine.registry.register(deferred)
    engine.registry.set_progressive_discovery(True)
    # The bridge is registered whenever the mode is on (`build_engine`); without it nothing is deferred.
    from harness.tools.discovery import discovery_tools

    engine.registry.register_all(discovery_tools(engine.registry))

    engine._note_tool_changes()
    assert "mcp__docs__search" not in {t["function"]["name"] for t in log.entries[0][1]["tools"]}

    assert engine.registry.load("mcp__docs__search")
    engine._note_tool_changes()
    assert len(log.entries) == 1, log.entries
    assert "mcp__docs__search" in engine.registry.directly_callable()
    assert "mcp__docs__search" not in {t["function"]["name"] for t in engine.registry.schemas()}


def test_the_toolset_change_stays_out_of_the_transcript():
    """It used to append a `tools_changed` notice next to the header. The transcript
    renderer had no branch for that kind, so it landed in the catch-all error branch and
    every mid-session MCP connect replayed as an error with a Retry button (owner-hit
    2026-09-10). The log entry above is the record; the transcript gets nothing."""
    engine = _bare_engine()
    engine.registry.register(_local("read_file"))
    engine._note_tool_changes()
    engine.registry.register(_remote("mcp__docs__search"))
    engine._note_tool_changes()

    assert engine.messages == []


def test_an_old_marker_still_does_not_consume_a_retry():
    """Sessions persisted before the removal still carry the notice. It was ambient then -
    a change in the world, not an outcome - and it has to stay ambient now, or those
    transcripts lose the Retry button on a real error underneath it."""
    from harness.loop import TurnEngine

    engine = TurnEngine.__new__(TurnEngine)
    engine.messages = [
        {"role": "notice", "kind": "error", "text": "provider fell over"},
        {"role": "notice", "kind": "tools_changed", "text": "Tools changed: +mcp__x__y"},
    ]
    assert engine._tail_is_retriable_error() is True


async def test_builtin_sidecars_run_with_the_session_workspace(tmp_path):
    """A relative save from the browser mcp must land in the SESSION's workspace, not
    in whatever directory the server process was launched from (the repo, on a dev box).
    The arm path pins the built-ins' cwd to the session workspace; configured servers
    keep whatever their entry says."""

    # Re-enable the built-ins this file's fixture clears (they are the subject here).
    import harness.extensions.mcp.config as cfg

    orig = cfg.builtin_mcp_servers
    monkeypatch_builtin = {
        "browser": {"command": "pw", "args": ["x"]},
        "computer-use": {"command": sys.executable, "args": ["cua_server.py"]},
    }
    cfg.builtin_mcp_servers = lambda *a, **k: monkeypatch_builtin
    try:
        manager = SessionManager(data_dir=tmp_path / "data")
        ws = tmp_path / "ws"
        ws.mkdir()
        await manager.prepare_mcp_tools("s1", workspace=str(ws))
        plan = {
            s.name: s
            for s in manager._mcp_plan("s1", ws, "code")
        }
        assert plan["browser"].cwd == str(ws)
        assert plan["computer-use"].cwd == str(ws)
    finally:
        cfg.builtin_mcp_servers = orig


# -- per-session servers (the built-in browser) --------------------------------------


@pytest.mark.asyncio
async def test_a_per_session_server_gets_one_connection_per_session(tmp_path, monkeypatch):
    """Browser ownership: the built-in browser is per SESSION, so two live
    sessions spawn two independent connections (own tabs/login) and a deleted session
    releases only its own."""
    from harness.extensions.mcp.config import MCPServerDef

    state = tmp_path / "state"
    monkeypatch.setenv("REEVE_STATE_DIR", str(state))
    manager = SessionManager(data_dir=tmp_path / "data")
    server = MCPServerDef(
        name="browser", transport="stdio", command="x", per_session=True
    )
    monkeypatch.setattr(
        "harness.interface.server.manager.mcp_plan.load_mcp_servers", lambda *a, **k: [server]
    )
    scopes: list[str] = []
    live: dict[tuple[str, str], _Conn] = {}

    async def _ensure(s, *, scope: str = "", interactive: bool = False):
        scopes.append(scope)
        conn = _Conn(["navigate"])
        live[(scope, s.name)] = conn
        return conn

    monkeypatch.setattr(manager.mcp, "ensure", _ensure)
    monkeypatch.setattr(manager.mcp, "_conns", live)

    await manager.prepare_mcp_tools("s1")
    await manager.prepare_mcp_tools("s2")
    # The second session is told the browser's tools from the list the first connect
    # answered with, so its own connection is made beside the turn, not in
    # front of it.
    await asyncio.sleep(0)
    assert scopes == ["s1", "s2"]
    assert set(live) == {("s1", "browser"), ("s2", "browser")}
    assert manager.mcp.connected("browser", scope="s1")
    assert manager.mcp.connected("browser", scope="s2")

    manager.mcp.forget_scope("s1")
    assert not manager.mcp.connected("browser", scope="s1")
    assert manager.mcp.connected("browser", scope="s2")


@pytest.mark.asyncio
async def test_a_dead_session_browser_is_redialled_before_the_other_one(tmp_path, monkeypatch):
    """Liveness is judged in the owning scope: session A's live browser must not make
    session B's dead one look armed."""
    from harness.extensions.mcp.config import MCPServerDef

    state = tmp_path / "state"
    monkeypatch.setenv("REEVE_STATE_DIR", str(state))
    engine = _Engine()
    manager = SessionManager(data_dir=tmp_path / "data")
    manager._engines["sB"] = engine
    server = MCPServerDef(
        name="browser", transport="stdio", command="x", per_session=True
    )
    monkeypatch.setattr(
        "harness.interface.server.manager.mcp_plan.load_mcp_servers", lambda *a, **k: [server]
    )
    dialled: list[str] = []

    async def _ensure(s, *, scope: str = "", interactive: bool = False):
        dialled.append(scope)
        return _Conn(["navigate"])

    # Session A's connection is live; session B's was never dialled.
    live = {("sA", "browser"): _Conn(["navigate"])}
    monkeypatch.setattr(manager.mcp, "ensure", _ensure)
    monkeypatch.setattr(manager.mcp, "_conns", live)

    await manager.arm_mcp("sB")
    assert dialled == ["sB"]
    assert _mcp_names(engine) == ["mcp__browser__navigate"]


async def test_a_slow_server_does_not_hold_the_turn_and_joins_later(tmp_path, monkeypatch):
    # The GUI review's U6: a first start that fetched `@playwright/mcp` held the turn at
    # "Working…" with nothing sent. The arm now has one budget; the connect keeps going
    # in the background and its tools arrive on a later arm.
    import asyncio
    import time
    from types import SimpleNamespace

    from harness.extensions.mcp.config import MCPServerDef
    from harness.interface.server import SessionManager
    from harness.interface.server.manager import mcp_arming as mcp_mixin

    mgr = SessionManager(data_dir=tmp_path / "data")
    server = MCPServerDef(name="slow", transport="stdio", command="x")
    monkeypatch.setattr("harness.interface.server.manager.mcp_plan.load_mcp_servers", lambda *a, **k: [server])
    monkeypatch.setattr(mcp_mixin, "MCP_ARM_BUDGET_S", 0.2)
    started = asyncio.Event()

    async def ensure(s, **kw):
        started.set()
        await asyncio.sleep(0.6)
        return SimpleNamespace(tools=[])

    monkeypatch.setattr(mgr.mcp, "ensure", ensure)
    before = time.monotonic()
    assert await mgr.prepare_mcp_tools("s1", workspace=str(tmp_path)) == []
    assert time.monotonic() - before < 0.5  # the turn was not held for the connect
    assert mgr.pop_mcp_failures("s1") == []  # still starting is not a failure

    await asyncio.sleep(0.6)  # the shielded connect finishes in the background
    pending = mgr._mcp_pending[("", "slow")]
    assert pending.done() and pending.exception() is None
