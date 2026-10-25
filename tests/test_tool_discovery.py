"""Progressive tool discovery — wire, bridge, intake unwrap, settings.

Native sessions publish every schema, and MCP servers make that number grow without
bound (measured: 104 tools = 23,928 tokens/request, 62 of them MCP). When the session
enables the mode, tools that declare `__tool_defer__` leave the wire and are reached
through `tool_search`/`tool_describe`/`tool_call`. Pinned here:

- the wire and the panel mirror each other (deferred schemas stop being billed);
- the mode is one-shot and refused under code mode (it is part of the cached prefix);
- search is lexical, bounded, deterministic; describe carries the parameters;
- `tool_call` is unwrapped BEFORE the propose event/approval/audit, so every consumer
  sees the real tool while history keeps what the model actually called;
- a direct call to a deferred tool is refused with the bridge named (announced surface
  equals callable surface, the same rule as code mode);
- the setting is prefs-backed, default on (defers only past `DISCOVERY_MIN_CHARS`),
  applied on the next session build.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from harness.loop import ApprovalOutcome, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import ToolCall
from harness.interface.server.app import create_app
from harness.interface.server.manager import SessionManager
from harness.tools import ToolRegistry
from harness.tools.contract import READ_ONLY_CAPS, ToolContract, attach_contract
from harness.tools.discovery import (
    DISCOVERY_MIN_CHARS,
    TOOL_CALL,
    TOOL_DESCRIBE,
    TOOL_SEARCH,
    discovery_tools,
    rewrite_bridge_call,
)

DEFERRED = "mcp__demo__echo"
CATALOG_DEFERRED = "mcp__github__create_issue"


def _schema(name, *, description="does a thing", properties=None, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": list(required),
            },
        },
    }


def _fn(name, *, defer=False, description="does a thing", result=None):
    def fn(**kwargs):
        return {"echoed": kwargs} if result is None else result

    fn.__name__ = name
    fn.__tool_schema__ = _schema(name, description=description)
    if defer:
        fn.__tool_defer__ = True
    attach_contract(fn, ToolContract(capabilities=READ_ONLY_CAPS))
    return fn


def _wire(reg: ToolRegistry) -> set[str]:
    return {s["function"]["name"] for s in reg.schemas()}


# -- the wire --------------------------------------------------------------------------


def test_deferred_tools_leave_the_wire_when_the_bridge_is_registered():
    reg = ToolRegistry()
    reg.register_all([_fn("read_file"), _fn(DEFERRED, defer=True)])
    assert _wire(reg) == {"read_file", DEFERRED}
    assert reg.deferred_names() == [DEFERRED]

    reg.set_progressive_discovery(True)
    reg.register_all(discovery_tools(reg))
    assert _wire(reg) == {"read_file", TOOL_SEARCH, TOOL_DESCRIBE, TOOL_CALL}
    # `all_schemas` is the SDK/panel source and stays complete.
    assert {s["function"]["name"] for s in reg.all_schemas()} == {
        "read_file",
        DEFERRED,
        TOOL_SEARCH,
        TOOL_DESCRIBE,
        TOOL_CALL,
    }


def test_set_progressive_discovery_is_one_shot():
    reg = ToolRegistry()
    reg.set_progressive_discovery(True)
    try:
        reg.set_progressive_discovery(False)
    except ValueError as exc:
        assert "fixed" in str(exc)
    else:  # pragma: no cover - the raise is the point
        raise AssertionError("a second set_progressive_discovery was accepted")
    assert reg.progressive_discovery is True


def test_filtered_carries_the_mode_and_unlocks():
    """A subagent's copy inherits the mode (and the bridge specs); `build_engine` rebinds
    the bridge to the child's own registry, which is why the lock is released here."""
    reg = ToolRegistry()
    reg.register_all([_fn("read_file"), _fn(DEFERRED, defer=True)])
    reg.set_progressive_discovery(True)
    reg.register_all(discovery_tools(reg))

    copy = reg.filtered()
    assert copy.progressive_discovery is True
    assert copy.deferred_names() == [DEFERRED]
    assert TOOL_CALL in _wire(copy)
    copy.set_progressive_discovery(True)  # the new registry's own first declaration


def test_schemas_by_group_mirrors_the_wire():
    """The context panel measures the request that is about to be sent: a deferred tool
    stops being counted exactly when it leaves the wire."""
    reg = ToolRegistry()
    mcp = _fn(DEFERRED, defer=True)
    mcp.__tool_group__ = "mcp"
    reg.register_all([_fn("read_file"), mcp])
    assert any(group == "mcp" for group, _ in reg.schemas_by_group())

    reg.set_progressive_discovery(True)
    # The bridge is registered whenever the mode is on (`build_engine`); without it nothing is deferred.
    from harness.tools.discovery import discovery_tools

    reg.register_all(discovery_tools(reg))
    assert all(group != "mcp" for group, _ in reg.schemas_by_group())


# -- search / describe -----------------------------------------------------------------


def _catalog_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all(
        [
            _fn("mcp__github__create_issue", defer=True, description="Create an issue in a repo"),
            _fn("mcp__jira__create_ticket", defer=True, description="Create a Jira ticket"),
            _fn("read_file"),
        ]
    )
    reg.set_progressive_discovery(True)
    reg.register_all(discovery_tools(reg))
    return reg


def _search(reg, **kwargs):
    return reg.execute(TOOL_SEARCH, kwargs)


def test_search_ranks_name_matches_and_bounds_the_limit():
    reg = _catalog_registry()
    result = _search(reg, query="create issue")
    assert result["matches"][0]["name"] == "mcp__github__create_issue"
    assert result["matches"][0]["server"] == "github"
    assert result["total"] == 2

    limited = _search(reg, query="create", limit=1)
    assert len(limited["matches"]) == 1


def test_search_with_no_query_lists_the_catalog():
    reg = _catalog_registry()
    result = _search(reg)
    assert {m["name"] for m in result["matches"]} == {
        "mcp__github__create_issue",
        "mcp__jira__create_ticket",
    }
    assert "tool_describe" in result["hint"]


def test_search_never_returns_eager_tools():
    reg = _catalog_registry()
    assert _search(reg, query="read_file")["matches"] == []
    assert "already" in _search(reg, query="read_file")["hint"]


def test_describe_returns_parameters_and_loads_the_tool():
    # Describing a deferred tool makes its own name callable — the bridge's
    # two-level call shape was the one models got wrong. It stays off the wire.
    reg = _catalog_registry()
    assert CATALOG_DEFERRED not in _wire(reg)
    result = reg.execute(TOOL_DESCRIBE, {"name": CATALOG_DEFERRED})
    assert result["deferred"] is True
    assert result["parameters"]["type"] == "object"
    assert "Loaded" in result["hint"] and "directly" in result["hint"]
    assert CATALOG_DEFERRED not in _wire(reg)
    assert CATALOG_DEFERRED in reg.directly_callable()
    again = reg.execute(TOOL_DESCRIBE, {"name": CATALOG_DEFERRED})
    assert "already loaded" in again["hint"]


def test_loading_only_grows_and_only_takes_deferred_tools():
    reg = _catalog_registry()
    assert reg.load("read_file") is False  # eager: nothing to load
    assert reg.load("nope") is False  # unknown
    assert reg.load(CATALOG_DEFERRED) is True
    assert reg.load(CATALOG_DEFERRED) is False  # once
    before = _wire(reg)
    reg.execute(TOOL_SEARCH, {"query": "issue"})  # a search does not load
    assert _wire(reg) == before
    # A child's copy keeps what the parent loaded, as far as it can call it.
    assert CATALOG_DEFERRED in reg.filtered().loaded
    assert CATALOG_DEFERRED not in reg.filtered(exclude={CATALOG_DEFERRED}).loaded


def test_loading_needs_progressive_discovery():
    reg = ToolRegistry()
    reg.register_all([_fn(DEFERRED, defer=True)])
    assert reg.load(DEFERRED) is False


def test_describe_an_eager_tool_says_it_is_already_available():
    reg = _catalog_registry()
    result = reg.execute(TOOL_DESCRIBE, {"name": "read_file"})
    assert result["deferred"] is False
    assert "already on your tool list" in result["hint"]


def test_describe_an_unknown_name_suggests_close_names():
    reg = _catalog_registry()
    result = reg.execute(TOOL_DESCRIBE, {"name": "github_issue"})
    assert "not a registered tool" in result["error"]
    assert "mcp__github__create_issue" in result["similar"]


# -- the unwrap ------------------------------------------------------------------------


def test_rewrite_unwraps_name_and_arguments_in_place():
    reg = _catalog_registry()
    call = ToolCall(
        id="c1", name=TOOL_CALL, arguments={"name": CATALOG_DEFERRED, "arguments": {"text": "hi"}}
    )
    assert rewrite_bridge_call(reg, call) is None
    assert call.name == CATALOG_DEFERRED
    assert call.arguments == {"text": "hi"}


def test_rewrite_accepts_a_json_string_arguments():
    reg = _catalog_registry()
    call = ToolCall(
        id="c1", name=TOOL_CALL, arguments={"name": CATALOG_DEFERRED, "arguments": '{"text": "hi"}'}
    )
    assert rewrite_bridge_call(reg, call) is None
    assert call.arguments == {"text": "hi"}


def test_rewrite_reports_missing_name_unknown_tool_and_self_call():
    reg = _catalog_registry()
    missing = ToolCall(id="c1", name=TOOL_CALL, arguments={})
    assert "tool_search" in rewrite_bridge_call(reg, missing)

    unknown = ToolCall(id="c2", name=TOOL_CALL, arguments={"name": "nope"})
    assert "not a registered tool" in rewrite_bridge_call(reg, unknown)

    itself = ToolCall(id="c3", name=TOOL_CALL, arguments={"name": TOOL_CALL})
    error = rewrite_bridge_call(reg, itself)
    assert "cannot call itself" in error
    # The refusal shows the shape to use, so a retry can be right.
    assert '"arguments"' in error and '"name"' in error


def test_rewrite_unwraps_a_call_the_model_wrapped_in_the_bridge_itself():
    # Session b3a8491e-9db (2026-09-28): the model sent this exact shape four times, once
    # after reading tool_describe, and each was refused as a self-call. The real tool and
    # its parameters are right there one level down.
    reg = _catalog_registry()
    for arguments in (
        {"arguments": {"name": CATALOG_DEFERRED, "arguments": {"text": "hi"}}, "name": TOOL_CALL},
        {"name": TOOL_CALL, "arguments": {"arguments": {"text": "hi"}, "name": CATALOG_DEFERRED}},
    ):
        call = ToolCall(id="c1", name=TOOL_CALL, arguments=arguments)
        assert rewrite_bridge_call(reg, call) is None
        assert call.name == CATALOG_DEFERRED
        assert call.arguments == {"text": "hi"}

    # Deeper nesting is not guessed at.
    twice = ToolCall(
        id="c2",
        name=TOOL_CALL,
        arguments={"name": TOOL_CALL, "arguments": {"name": TOOL_CALL, "arguments": {}}},
    )
    assert "cannot call itself" in rewrite_bridge_call(reg, twice)


def test_rewrite_leaves_an_ordinary_call_alone():
    reg = _catalog_registry()
    call = ToolCall(id="c1", name="read_file", arguments={"path": "x"})
    assert rewrite_bridge_call(reg, call) is None
    assert call.name == "read_file"


# -- the engine seam -------------------------------------------------------------------


class _Scripted(ProviderClient):
    """Emits the batches it is given, one per complete(); text when the list runs out."""

    def __init__(self, batches, *, tail="done"):
        self._batches = list(batches)
        self._tail = tail
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if not self._batches:
            return AssistantTurn(text=self._tail, finish_reason="stop")
        calls = [
            ToolCall(id=f"c{self.calls}-{i}", name=name, arguments=arguments)
            for i, (name, arguments) in enumerate(self._batches.pop(0))
        ]
        return AssistantTurn(tool_calls=calls, finish_reason="tool_calls")

    def capabilities(self, model):
        return ModelCapabilities()


async def _approve(_request):
    return ApprovalOutcome.ONCE


def _engine(tmp_path, provider) -> TurnEngine:
    reg = ToolRegistry()
    reg.register_all([_fn("read_file"), _fn(DEFERRED, defer=True)])
    reg.set_progressive_discovery(True)
    reg.register_all(discovery_tools(reg))
    permissions = PermissionEngine(workspace_root=tmp_path)
    permissions.mode = Mode.WORKSPACE_WRITE
    return TurnEngine(
        provider=provider,
        registry=reg,
        permissions=permissions,
        model="gpt-5.5",
        approver=_approve,
        max_iterations=6,
    )


def _run(engine):
    async def go():
        return [event async for event in engine.run("do it")]

    return asyncio.run(go())


def _tool_contents(engine):
    return [m["content"] for m in engine.messages if m.get("role") == "tool"]


def test_the_bridge_runs_the_real_tool_through_the_turn(tmp_path):
    provider = _Scripted([[ (TOOL_CALL, {"name": DEFERRED, "arguments": {"text": "hi"}}) ]])
    engine = _engine(tmp_path, provider)
    events = _run(engine)

    proposed = [e for e in events if e.type is EventType.TOOL_PROPOSED]
    assert [e.data["name"] for e in proposed] == [DEFERRED]
    # The TOOL ran: its own result (`{"echoed": {...}}`) came back. This used to assert
    # `"hi" in content`, which the refusal text satisfied too ("t-hi-s session's tool
    # list") — the bridge refused every call and the test stayed green (found live,
    # 2026-09-24). Assert the result, and the absence of the refusal.
    contents = [str(c) for c in _tool_contents(engine)]
    assert any('"echoed"' in c and '"text": "hi"' in c for c in contents), contents
    assert not any("not on this session's tool list" in c for c in contents), contents
    # History keeps what the MODEL called; only the execution was unwrapped.
    assistant = next(m for m in engine.messages if m.get("role") == "assistant" and m.get("tool_calls"))
    assert assistant["tool_calls"][0]["function"]["name"] == TOOL_CALL


def test_a_direct_call_to_a_deferred_tool_is_refused_in_a_turn(tmp_path):
    provider = _Scripted([[ (DEFERRED, {"text": "hi"}) ]])
    engine = _engine(tmp_path, provider)
    _run(engine)

    contents = " ".join(_tool_contents(engine))
    assert "progressive discovery" in contents and "tool_describe" in contents
    assert "echoed" not in contents  # the handler never ran


def test_after_describe_the_tool_is_called_by_its_own_name(tmp_path):
    # Session b3a8491e-9db's path, as it should have gone: describe, then the real name.
    provider = _Scripted(
        [
            [(TOOL_DESCRIBE, {"name": DEFERRED})],
            [(DEFERRED, {"text": "hi"})],
        ]
    )
    engine = _engine(tmp_path, provider)
    _run(engine)

    contents = [str(c) for c in _tool_contents(engine)]
    assert any('"echoed"' in c and '"text": "hi"' in c for c in contents), contents
    assert not any("progressive discovery" in c for c in contents), contents
    # Called by name with its schema off the request.
    assert DEFERRED not in {s["function"]["name"] for s in engine._model_tools()}


def test_a_tool_reached_through_the_bridge_is_loaded(tmp_path):
    provider = _Scripted([[(TOOL_CALL, {"name": DEFERRED, "arguments": {"text": "hi"}})]])
    engine = _engine(tmp_path, provider)
    _run(engine)
    assert DEFERRED in engine.registry.loaded
    assert engine._direct_call_refusal(DEFERRED) is None


def test_a_bridge_call_to_an_unknown_tool_answers_without_running_anything(tmp_path):
    provider = _Scripted([[ (TOOL_CALL, {"name": "nope"}) ]])
    engine = _engine(tmp_path, provider)
    _run(engine)

    assert "not a registered tool" in " ".join(_tool_contents(engine))


def test_the_engine_refuses_a_direct_call_to_a_deferred_tool(tmp_path):
    engine = _engine(tmp_path, _Scripted([]))
    refusal = engine._direct_call_refusal(DEFERRED)
    assert refusal is not None and "tool_describe" in refusal and "tool_call" in refusal
    assert engine._direct_call_refusal("read_file") is None


def test_a_childs_bridge_searches_only_what_that_child_can_call(tmp_path):
    """The bridge closures must be rebound to the child's own registry: a copied closure
    would surface tools the child's ruleset removed, and the child would be told to call
    something it cannot reach."""
    from harness.compose import build_engine
    from harness.modes.agents.chat import chat_agent

    parent = ToolRegistry()
    parent.register_all([_fn(DEFERRED, defer=True), _fn("mcp__demo__secret", defer=True)])
    parent.set_progressive_discovery(True)
    parent.register_all(discovery_tools(parent))

    child = parent.filtered(exclude={DEFERRED})
    engine = build_engine(
        agent=chat_agent(), workspace=tmp_path, provider=object(), base_registry=child
    )
    result = engine.registry.execute(TOOL_SEARCH, {})
    assert {m["name"] for m in result["matches"]} == {"mcp__demo__secret"}


# -- settings --------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))


def test_settings_default_on(client):
    # On by default since the 2026-09-24 audit (tests/test_discovery_catalog.py pins why).
    assert client.get("/v1/settings").json()["tool_discovery"] is True


def test_settings_roundtrip(client):
    r = client.post("/v1/settings/tool-discovery", json={"enabled": True}).json()
    assert r["ok"] and r["tool_discovery"] is True
    assert client.get("/v1/settings").json()["tool_discovery"] is True
    client.post("/v1/settings/tool-discovery", json={"enabled": False})
    assert client.get("/v1/settings").json()["tool_discovery"] is False


def test_settings_persist_across_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    TestClient(create_app(SessionManager(data_dir=data_dir))).post(
        "/v1/settings/tool-discovery", json={"enabled": True}
    )
    reborn = SessionManager(data_dir=data_dir)
    assert reborn.tool_discovery() is True


def test_a_plain_coding_session_defers_its_situational_tools(tmp_path):
    """A build with no MCP still carries the terminal, git, history and workflow schemas, more
    than three times the bridge; they leave the wire and the core loop stays on it. At the old
    16k bar a session without MCP sent every one of them on every request."""
    from harness.compose import build_engine
    from harness.modes.agents.reeve import reeve_agent

    engine = build_engine(agent=reeve_agent(), workspace=tmp_path, provider=object(), tool_discovery=True)
    assert engine.registry.progressive_discovery is True
    sent = {schema["function"]["name"] for schema in engine.registry.schemas()}
    assert TOOL_SEARCH in sent
    assert {"terminal_open", "git_log", "workflow", "search_history"} & sent == set()
    assert {"read_file", "replace_in_file", "grep", "run_shell", "task", "run_code"} <= sent
    assert "lsp" not in engine.registry.deferred_names()


def test_a_build_with_little_to_defer_stays_eager(tmp_path, caplog):
    """"On" means "defer when it pays": below the bar the bridge would cost more than it saves."""
    from harness.tools.discovery import deferred_payload_chars
    from harness.compose.features.discovery import apply_tool_discovery

    registry = ToolRegistry()
    registry.register_all([_fn("terminal_open"), _fn("read_file")])
    with caplog.at_level("INFO"):
        apply_tool_discovery(registry, True, None)
    assert deferred_payload_chars(registry) < DISCOVERY_MIN_CHARS
    assert registry.progressive_discovery is False
    assert any("deferrable schemas are small" in record.message for record in caplog.records)


def test_the_deferred_built_ins_are_situational_and_never_interactive():
    """The deferred built-ins are chosen by their role in a coding product (amended
    2026-10-10), never the core loop. An interactive tool is never deferred (the clarify
    lesson), nor a tool a result or a goal round tells the model to call next."""
    from harness.tools.discovery_catalog import COLD_BUILTINS

    never = {"ask_user", "request_directory", "request_tool", "propose_plan", "update_plan",
             "todo_write", "goal", "shell_task_output", "shell_task_kill", "run_shell",
             "read_file", "write_file", "replace_in_file", "grep", "task", "load_skill",
             "auto_memory", "run_code", "lsp", "web_search", "web_fetch", "list_files"}
    assert not COLD_BUILTINS & never


def test_mark_deferred_flags_registered_tools_until_the_mode_locks():
    registry = ToolRegistry()
    registry.register_all([_fn("terminal_open"), _fn("read_file")])
    assert registry.mark_deferred({"terminal_open", "not_registered"}) == ["terminal_open"]
    assert registry.deferred_names() == ["terminal_open"]
    registry.set_progressive_discovery(True)
    assert registry.mark_deferred({"read_file"}) == []  # the wire is fixed once locked


def test_a_built_in_deferred_by_name_runs_when_called_by_that_name(tmp_path):
    # 2026-09-25 audit, W5: the prompt names `terminal_open` and "the git tools", which
    # left the wire with the cold built-ins; every direct call was refused and cost a
    # `tool_search` → `tool_call` detour. Called by its real name, the built-in runs; its
    # schema stays off the wire, and an MCP tool still takes the bridge.
    engine = _engine(tmp_path, _Scripted([[("git_diff", {"path": "a"}), (DEFERRED, {"text": "x"})]]))
    engine.registry._progressive_locked = False
    engine.registry.register(_fn("git_diff"))
    assert engine.registry.mark_deferred(["git_diff"]) == ["git_diff"]
    assert "git_diff" not in _wire(engine.registry)
    _run(engine)

    contents = [str(c) for c in _tool_contents(engine)]
    assert '{"echoed": {"path": "a"}}' in contents, contents  # the built-in ran
    refused = [c for c in contents if "progressive discovery" in c]
    assert len(refused) == 1 and DEFERRED in refused[0]  # the MCP tool did not


# -- a rebuilt registry is the one that was dropped -------------------------


def _called(name, arguments):
    import json

    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        ],
    }


def test_a_rebuilt_registry_loads_what_the_thread_shows_was_loaded():
    """After a restart the registry is new and its loaded set is empty, while the thread
    is full of calls to tools the session had loaded. Those tools have to be callable by
    name again, or the first call by a tool's own name is refused as "not loaded yet" and
    the model describes it again to get back."""
    from harness.tools.discovery import restore_loaded

    thread = [
        {"role": "user", "content": "file an issue"},
        _called(TOOL_DESCRIBE, {"name": "mcp__github__create_issue"}),
        _called("mcp__github__create_issue", {"title": "x"}),
        _called(TOOL_CALL, {"name": "create_ticket", "arguments": {"summary": "y"}}),
    ]
    rebuilt = _catalog_registry()
    assert "mcp__github__create_issue" not in _wire(rebuilt)

    restored = restore_loaded(rebuilt, thread)

    assert restored == ["mcp__github__create_issue", "mcp__jira__create_ticket"]
    assert {"mcp__github__create_issue", "mcp__jira__create_ticket"} <= rebuilt.directly_callable()
    # Callable again by name, and the wire is the one the session had: loads never reach it.
    assert not {"mcp__github__create_issue", "mcp__jira__create_ticket"} & _wire(rebuilt)


def test_restoring_follows_the_rules_the_live_paths_apply():
    from harness.tools.discovery import restore_loaded

    thread = [
        # A call by a tool's own name loads nothing: only describe and the bridge do.
        _called("mcp__jira__create_ticket", {"summary": "y"}),
        # The bridge refused these when they happened, and nothing was loaded then.
        _called(TOOL_CALL, {"name": "no_such_tool", "arguments": {}}),
        _called(TOOL_CALL, {"arguments": {}}),
        # Describing an ordinary tool loads nothing.
        _called(TOOL_DESCRIBE, {"name": "read_file"}),
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": TOOL_DESCRIBE, "arguments": "{not json"}}]},
        {"role": "tool", "content": "mcp__github__create_issue"},
    ]
    rebuilt = _catalog_registry()

    assert restore_loaded(rebuilt, thread) == []
    assert rebuilt.loaded == set()


def test_restoring_does_nothing_where_nothing_is_deferred():
    from harness.tools.discovery import restore_loaded

    eager = ToolRegistry()
    eager.register_all([_fn("mcp__github__create_issue", defer=True)])

    assert restore_loaded(eager, [_called(TOOL_DESCRIBE, {"name": "mcp__github__create_issue"})]) == []
    assert restore_loaded(_catalog_registry(), None) == []


def test_a_rebuilt_engine_accepts_the_call_the_conversation_was_making(tmp_path):
    """The turn-level proof: over a thread that described the tool, an engine built
    fresh runs a call by its own name instead of refusing it."""
    from harness.tools.discovery import restore_loaded

    provider = _Scripted([[(DEFERRED, {"text": "again"})]])
    engine = _engine(tmp_path, provider)
    engine.messages.append(_called(TOOL_DESCRIBE, {"name": DEFERRED}))
    engine.messages.append({"role": "tool", "tool_call_id": "c", "content": "{}"})
    assert engine._direct_call_refusal(DEFERRED) is not None  # the bug, as a rebuilt engine met it

    restore_loaded(engine.registry, engine.messages)
    _run(engine)

    contents = [str(c) for c in _tool_contents(engine)]
    assert any('"echoed": {"text": "again"}' in c for c in contents), contents
    assert not any("progressive discovery" in c for c in contents)


def test_build_engine_gives_a_reopened_session_the_tool_list_it_was_on(tmp_path):
    """Through the real builder: a session whose thread described an MCP tool is rebuilt
    with that tool callable by name, not behind the bridge again, and with the same tool list
    as a fresh build."""
    from harness.compose import build_engine
    from harness.modes.agents.reeve import reeve_agent

    big = "x" * 600  # forty of these clear the bar at which deferral pays
    tools = [_fn(f"mcp__demo__t{i}", defer=True, description=big) for i in range(40)]
    thread = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
        _called(TOOL_DESCRIBE, {"name": "mcp__demo__t7"}),
        {"role": "tool", "tool_call_id": "c", "content": "{}"},
    ]

    fresh = build_engine(
        agent=reeve_agent(), workspace=tmp_path, provider=object(), tool_discovery=True, extra_tools=tools
    )
    reopened = build_engine(
        agent=reeve_agent(),
        workspace=tmp_path,
        provider=object(),
        tool_discovery=True,
        extra_tools=tools,
        messages=thread,
    )

    assert fresh.registry.progressive_discovery is True
    assert "mcp__demo__t7" not in _wire(fresh.registry)
    assert "mcp__demo__t7" in reopened.registry.directly_callable()
    assert "mcp__demo__t8" not in reopened.registry.directly_callable()
    assert _wire(reopened.registry) == _wire(fresh.registry)
