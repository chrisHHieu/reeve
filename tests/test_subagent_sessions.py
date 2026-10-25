"""Subagent child sessions — a `task` child runs through the SAME turn driver.

What must hold: `SessionManager.spawn_subagent` creates (or resumes)
a real child session, its engine is built by `build_engine` with the agent definition's
prompt/model/steps, its tools are filtered by the merged ruleset, and its turn is driven
by the shared `_drive_turn` — so the child's messages persist in its OWN conversation
and a `task` call through the manager returns the same envelope the thread harness does.
The `mark_idle` lifecycle hooks must stay out of a child's way: it is not a workspace
conversation.

Step C adds the EVENT surface the GUI renders instead of polling: `session_created` /
`session_status` on the app-wide stream (`/ws/events`, so every client can list a
child), and `tool_metadata` on the PARENT's session stream while the child's tools run
(`↳ <tool> <title>`). These tests pin the payloads, their order, and the best-effort
rule: a raising collector can never fail a child run.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.llm.providers.base import TokenUsage
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord
from harness.tools.task import BACKGROUND_MOVED, BACKGROUND_STARTED


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)
        self.calls: list[dict] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append({"model": model, "messages": messages})
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text: str, usage: TokenUsage | None = None) -> AssistantTurn:
    return AssistantTurn(text=text, finish_reason="stop", usage=usage)


def _tool_turn(name: str, args: dict, call_id: str = "call_1") -> AssistantTurn:
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=args)],
        finish_reason="tool_calls",
    )


def _collector():
    events: list[dict] = []

    async def cb(msg):
        events.append(msg)

    return events, cb


def _manager(tmp_path, provider) -> SessionManager:
    return SessionManager(workspace=tmp_path, provider=provider)


def _parent(mgr: SessionManager, sid: str = "parent-1") -> SessionRecord:
    record = SessionRecord(
        session_id=sid,
        workspace=str(mgr.default_workspace or "."),
        model="gpt-5.5",
        mode="interactive",
        messages=[
            {"role": "user", "content": "the parent's first question"},
            {"role": "assistant", "content": "the parent's first answer"},
        ],
        title="a long conversation",
        agent="reeve",
    )
    mgr.session_store.save(record)
    return record


def _spawn(mgr, parent_id, **kwargs):
    return asyncio.run(
        mgr.spawn_subagent(
            parent_id=parent_id,
            description=kwargs.pop("description", "Investigate the cache"),
            prompt=kwargs.pop("prompt", "find where the cache lives"),
            agent_name=kwargs.pop("agent_name", "general"),
            **kwargs,
        )
    )


# -- the child is a session with its own conversation --------------------------------


def test_a_spawn_creates_a_child_session_in_its_own_conversation(tmp_path):
    mgr = _manager(tmp_path, ScriptedProvider([_text("the final report")]))
    parent = _parent(mgr)

    out = _spawn(mgr, parent.session_id)

    child = mgr.session_store.load(out["task_id"])
    assert child is not None
    assert child.kind == "subagent"
    assert child.parent_session == parent.session_id
    assert child.title == "Investigate the cache (@general subagent)"
    # The exchange lives in the CHILD's conversation...
    contents = [m.get("content") for m in child.messages]
    assert "find where the cache lives" in contents
    assert "the final report" in contents
    # ...and the parent's own conversation is untouched.
    parent_after = mgr.session_store.load(parent.session_id)
    parent_contents = [m.get("content") for m in parent_after.messages]
    assert "find where the cache lives" not in parent_contents
    assert "the final report" not in parent_contents


def test_the_return_shape_is_the_report_status_and_tokens(tmp_path):
    provider = ScriptedProvider(
        [_text("the findings", usage=TokenUsage(input=120, output=30))]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)

    out = _spawn(mgr, parent.session_id)

    child = mgr.session_store.load(out["task_id"])
    assert out["task_id"] == child.session_id
    assert out["status"] == "completed"
    # The report is the child's LAST assistant text, raw here — the task tool wraps it.
    assert out["report"] == "the findings"
    assert out["tokens"] == 150


def test_unknown_task_id_is_refused_with_the_exact_string(tmp_path):
    mgr = _manager(tmp_path, ScriptedProvider([]))
    parent = _parent(mgr)
    out = _spawn(mgr, parent.session_id, task_id="ghost")
    assert out == {"error": "Unknown task_id: ghost"}


def test_an_unknown_agent_is_refused_with_the_standard_words(tmp_path):
    mgr = _manager(tmp_path, ScriptedProvider([]))
    parent = _parent(mgr)
    out = _spawn(mgr, parent.session_id, agent_name="nope")
    assert out == {"error": "Unknown agent type: nope is not a valid agent type"}


# -- the task tool's manager path --------------------------------------------------


def test_the_task_tool_spawns_through_the_manager(tmp_path):
    """End to end: a parent session's `task` call runs the spawner (the manager's sync
    bridge), which drives a child session on a private loop because this manager has no
    server loop. The parent reads the same envelope the thread path returns, and the
    child's transcript is a real session row."""
    parent_provider = ScriptedProvider(
        [
            _tool_turn(
                "task",
                {
                    "description": "delegate the lookup",
                    "prompt": "find where the cache lives",
                    "subagent_type": "general",
                },
            ),
            _text("the child findings"),
            _text("delegated and done"),
        ]
    )
    mgr = _manager(tmp_path, parent_provider)
    parent = _parent(mgr, "parent-tool")

    asyncio.run(mgr.deliver_to_session(parent.session_id, "please delegate"))

    children = mgr.children_of(parent.session_id)
    assert len(children) == 1
    child = mgr.session_store.load(children[0])
    assert child.kind == "subagent"
    contents = [m.get("content") for m in child.messages]
    assert "find where the cache lives" in contents
    assert "the child findings" in contents
    # The parent's transcript carries the returned envelope and
    # its own final answer — the child ran as a SEPARATE session, not inline.
    parent_after = mgr.session_store.load(parent.session_id)
    assert parent_after.messages[-1]["content"] == "delegated and done"
    tool_results = [m.get("content") for m in parent_after.messages if m.get("role") == "tool"]
    assert any('<task id=' in str(c) and 'state="completed"' in str(c) for c in tool_results)


# -- resume keeps the same child across a rebuild -----------------------------------


def test_resuming_a_task_continues_its_conversation(tmp_path):
    provider = ScriptedProvider([_text("first pass"), _text("second pass")])
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)

    first = _spawn(mgr, parent.session_id)
    second = _spawn(
        mgr, parent.session_id, task_id=first["task_id"], prompt="now check X"
    )

    assert second["task_id"] == first["task_id"]
    assert second["report"] == "second pass"
    child = mgr.session_store.load(second["task_id"])
    contents = [m.get("content") for m in child.messages]
    assert "find where the cache lives" in contents
    assert "first pass" in contents
    assert "now check X" in contents
    assert "second pass" in contents
    # The rebuilt engine kept the child's own transcript as its history.
    assert provider.calls[1]["messages"][0]["role"] == "system"
    assert any(m.get("content") == "first pass" for m in provider.calls[1]["messages"])


# -- lifecycle guards: a child is not a workspace conversation ----------------------


def test_a_child_gets_no_generated_title_or_memory_artifacts(tmp_path):
    """`mark_idle` fires at the end of every turn — including a child's. Title generation is skipped
    title generation for `parentID` sessions, and the memory/goal hooks are for
    workspace conversations, so none of them may touch a child."""
    provider = ScriptedProvider(
        [_text("one"), _text("two"), _text("a generated title")]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)

    first = _spawn(mgr, parent.session_id)
    # A second turn: still a child, still no title of its own.
    _spawn(mgr, parent.session_id, task_id=first["task_id"], prompt="second question")

    child_id = first["task_id"]
    state = mgr.session_store.title_state(child_id)
    assert state["auto_title"] in (None, "")
    assert mgr.session_store.load(child_id).title == (
        "Investigate the cache (@general subagent)"
    )
    assert child_id not in mgr._autotitle_attempts
    assert child_id not in mgr._autotitle_inflight


# -- the child's tools are its ruleset ----------------------------------------------


def test_a_child_ruleset_decides_without_asking_a_person(tmp_path):
    """The same assertion style as `test_task.py::test_a_ruleset_allow_runs_without_asking`,
    against the engine `spawn_subagent` actually built: explore's wildcard deny REMOVED
    the write tools, an allowed call runs with no prompt, and a denied one refuses
    instead of asking."""
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)

    out = _spawn(mgr, parent.session_id, agent_name="explore")

    engine = mgr._engines[out["task_id"]]
    assert "write_file" not in engine.registry.names()
    assert "task" not in engine.registry.names()
    allowed = engine.permissions.evaluate("web_search", {"query": "flagship models"})
    assert allowed.allowed and not allowed.needs_user
    refused = engine.permissions.evaluate(
        "write_file", {"path": "a.py", "content": "x"}
    )
    assert not refused.allowed and not refused.needs_user


# -- F1: the child's registry is the parent session's, filtered ----------------------


def test_filtered_registry_copies_the_specs_and_leaves_the_parent_alone():
    """`ToolRegistry.filtered` is how a child holds the session's tools without the
    parent's registry object ever being touched: ONE shared registry is filtered
    per request; here an engine owns a registry, so a
    copy is the model of "same registry, per-agent filter"."""
    from harness.tools import ToolRegistry

    def alpha(x: int = 1) -> int:
        """Alpha."""
        return x

    def beta() -> str:
        """Beta."""
        return "b"

    registry = ToolRegistry()
    registry.register(alpha)
    registry.register(beta)

    copy = registry.filtered(exclude={"beta"})
    assert copy.names() == ["alpha"]
    # Same ToolSpec object — func/schema/contract/metadata/group/policy all travel.
    assert copy.get("alpha") is registry.get("alpha")
    # The parent's registry itself is untouched.
    assert registry.names() == ["alpha", "beta"]
    assert copy.progressive_discovery == registry.progressive_discovery


def test_a_child_inherits_the_parent_sessions_tools_but_never_the_doors(tmp_path):
    """The child's registry is the parent ENGINE's copy — an MCP-ish tool the
    parent merged in, plus skills, cross over — while the spawn tool and the interactive
    doors are removed by name, even though the ruleset is `*: allow`."""

    def mcp_echo(text: str = "") -> dict:
        """Echo text through a fake MCP server."""
        return {"echo": text}

    def task(description: str = "") -> dict:
        """A parent-only spawn tool the copy must drop."""
        return {}

    from harness.config.config import global_config_path

    # `request_tool` is opt-in: turned on here so the door it opens is in the parent.
    global_config_path().parent.mkdir(parents=True, exist_ok=True)
    global_config_path().write_text('[features]\nadd = ["tool_request"]\n', encoding="utf-8")
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)
    parent_engine = mgr.get_engine(parent.session_id, extra_tools=[mcp_echo, task])
    assert {
        "mcp_echo",
        "task",
        "ask_user",
        "request_tool",
        "propose_plan",
    } <= set(parent_engine.registry.names())

    out = _spawn(mgr, parent.session_id)

    names = set(mgr._engines[out["task_id"]].registry.names())
    assert "mcp_echo" in names
    # Skills are part of the session surface a child keeps (they were persona-only before).
    assert "load_skill" in names and "save_skill" in names
    assert not (
        {"task", "ask_user", "request_tool", "propose_plan"} & names
    )
    # The parent engine keeps its own registry: the child holds a COPY.
    assert "task" in parent_engine.registry.names()
    assert "mcp_echo" in parent_engine.registry.names()


def test_primary_tools_removes_a_tool_the_child_ruleset_would_allow(tmp_path, monkeypatch):
    """`primary_tools`: removed from
    every child on top of the ruleset — `web_search` is `*: allow` for `general` and is
    gone anyway; a tool that is not named stays."""
    from harness.config.config import Config

    config = Config(primary_tools=["web_search"])
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)
    mgr.get_engine(parent.session_id)

    out = _spawn(mgr, parent.session_id)

    names = set(mgr._engines[out["task_id"]].registry.names())
    assert "web_search" not in names
    assert "web_fetch" in names  # only the named tool is denied


def test_a_child_rulesets_wildcard_deny_reaches_the_inherited_tools(tmp_path):
    """The merged ruleset still decides the child's copy: explore's `*: deny` removes a
    parent-only extra tool and the write tools, exactly as it does on a fixed set."""

    def mcp_write(text: str = "") -> dict:
        """A fake MCP write tool."""
        return {"ok": text}

    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)
    parent_engine = mgr.get_engine(parent.session_id, extra_tools=[mcp_write])
    assert "mcp_write" in parent_engine.registry.names()

    out = _spawn(mgr, parent.session_id, agent_name="explore")

    names = set(mgr._engines[out["task_id"]].registry.names())
    assert "mcp_write" not in names
    assert "write_file" not in names
    assert {"grep", "read_file", "run_shell"} <= names


def test_an_agent_that_speaks_about_todo_write_gets_it_back(tmp_path, monkeypatch):
    """`child_permission`'s rule: `todo_write` is denied in a child unless the agent's own
    ruleset mentions it — and then the inherited registry's copy keeps it."""
    from harness.config.config import Config

    config = Config(agents={"planner": {"permission": {"todo_write": "allow"}}})
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)
    parent_engine = mgr.get_engine(parent.session_id)
    assert "todo_write" in parent_engine.registry.names()

    out = _spawn(mgr, parent.session_id, agent_name="planner")

    names = set(mgr._engines[out["task_id"]].registry.names())
    assert "todo_write" in names
    # `task` and the interactive doors are still never-tools, ruleset or not.
    assert "task" not in names and "ask_user" not in names


def test_a_childs_program_may_call_only_its_own_tools(tmp_path, monkeypatch):
    """A child keeps `run_code`, and what its program may call is the CHILD's filtered
    registry, not the parent's: a program naming a tool only the parent holds is refused
    before it starts (the schema itself names no tools)."""
    from harness.config.config import Config

    def mcp_echo(text: str = "") -> dict:
        """Echo text through a fake MCP server."""
        return {"echo": text}

    config = Config(primary_tools=["mcp_echo"])
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)
    parent_engine = mgr.get_engine(parent.session_id, extra_tools=[mcp_echo])
    from harness.tools.code_mode import UNAVAILABLE
    from harness.tools.code_names import check_names

    program = "print(tools.mcp_echo(text='x'))"
    assert check_names(program, set(parent_engine.registry.names()) - UNAVAILABLE) == ("", [])

    out = _spawn(mgr, parent.session_id)

    engine = mgr._engines[out["task_id"]]
    assert engine.registry.get("run_code") is not None
    refusal, unknown = check_names(program, set(engine.registry.names()) - UNAVAILABLE)
    assert unknown == ["mcp_echo"] and "Nothing ran" in refusal
    assert check_names("tools.read_file(path='a')", set(engine.registry.names()) - UNAVAILABLE) == ("", [])


# -- F2: the agent's knobs land on the child engine ---------------------------------


def test_agent_knobs_land_on_the_child_engine(tmp_path, monkeypatch):
    """: temperature/top_p are provider settings, `variant`
    is the thinking level, and unknown config keys ride `options` LAST — so they can
    override a named knob. The child row records the model it actually ran on."""
    from harness.config.config import Config

    config = Config(
        agents={
            "tuned": {
                "model": "deepseek:chat",
                "temperature": 0.3,
                "top_p": 0.9,
                "variant": "high",
                "color": "#ff8800",
                # `seed` is not a schema key: it must survive as an option.
                "seed": 7,
                "options": {"max_tokens": 4321, "temperature": 0.7},
            }
        }
    )
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)
    mgr.get_engine(parent.session_id)

    out = _spawn(mgr, parent.session_id, agent_name="tuned")

    engine = mgr._engines[out["task_id"]]
    assert engine.model == "deepseek:chat"
    assert engine.thinking_level == "high"
    assert engine.model_settings["reasoning_effort"] == "high"
    assert engine.model_settings["top_p"] == 0.9
    # `options` is merged last: the explicit option wins over the named knob.
    assert engine.model_settings["temperature"] == 0.7
    assert engine.model_settings["max_tokens"] == 4321
    assert engine.model_settings["seed"] == 7
    assert mgr.session_store.load(out["task_id"]).model == "deepseek:chat"


def test_a_model_less_agent_forwards_the_callers_thinking_level(tmp_path, monkeypatch):
    """: with no model of its own the child inherits the caller's MODEL
    and its VARIANT; an agent that names a model does not inherit the caller's level."""
    from harness.config.config import Config

    config = Config(
        agents={
            "owned": {"model": "deepseek:chat"},
            "free": {"description": "no model of its own"},
        }
    )
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    mgr = _manager(tmp_path, ScriptedProvider([_text("one"), _text("two")]))
    parent = _parent(mgr)
    parent.thinking = "low"
    mgr.session_store.save(parent)
    parent_engine = mgr.get_engine(parent.session_id)
    assert parent_engine.thinking_level == "low"

    free = _spawn(mgr, parent.session_id, agent_name="free")
    free_engine = mgr._engines[free["task_id"]]
    assert free_engine.model == "gpt-5.5"  # the caller's model, as before
    assert free_engine.thinking_level == "low"  # ...and now its variant

    owned = _spawn(mgr, parent.session_id, agent_name="owned")
    owned_engine = mgr._engines[owned["task_id"]]
    assert owned_engine.model == "deepseek:chat"
    assert owned_engine.thinking_level is None


def test_a_resume_records_the_model_the_child_actually_ran_on(tmp_path, monkeypatch):
    """`setAgentModel`: the child row is rewritten when a resume
    resolves a different model than the one creation recorded."""
    from harness.config.config import Config

    config = Config()
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    mgr = _manager(tmp_path, ScriptedProvider([_text("first"), _text("second")]))
    parent = _parent(mgr)

    first = _spawn(mgr, parent.session_id)
    assert mgr.session_store.load(first["task_id"]).model == "gpt-5.5"

    config.agents = {"general": {"model": "gpt-5.6-sol"}}
    second = _spawn(mgr, parent.session_id, task_id=first["task_id"], prompt="again")

    assert second["task_id"] == first["task_id"]
    assert mgr.session_store.load(first["task_id"]).model == "gpt-5.6-sol"


def test_the_task_tool_returns_the_managers_running_envelope(tmp_path):
    """The tool's manager branch (D): `background=True` reaches the spawner,
    and the manager's already-rendered running envelope comes back verbatim — the thread
    path's running shape, not a `state="completed"` wrapper around it."""
    from harness.tools.task import render_output, task_tools

    calls: list[dict] = []

    def spawner(**kwargs):
        calls.append(kwargs)
        return {
            "task_id": "child-1",
            "status": "running",
            "report": render_output(
                session_id="child-1", state="running", text=BACKGROUND_STARTED
            ),
        }

    tools = task_tools(
        workspace=tmp_path,
        provider=ScriptedProvider([]),
        model="gpt-5.5",
        session_id="parent-1",
        wake_store=object(),
        spawner=spawner,
    )
    task = next(t for t in tools if getattr(t, "__name__", "") == "task")

    result = task(
        description="Investigate the cache",
        prompt="find where the cache lives",
        subagent_type="general",
        background=True,
    )

    assert result == {
        "task_id": "child-1",
        "status": "running",
        "report": render_output(
            session_id="child-1", state="running", text=BACKGROUND_STARTED
        ),
    }
    assert calls and calls[0]["background"] is True
    assert calls[0]["parent_id"] == "parent-1"


# -- step C: the child's life cycle on the app-wide stream ---------------------------


def test_a_spawn_announces_the_child_before_its_first_turn_event(tmp_path):
    """`session_created` goes to EVERY client (/ws/events), not only the parent's
    viewers — a sibling window must be able to list the child the moment it exists —
    and it must arrive before the child's first turn event, which is why the test
    subscribes to the child from inside the announcement (what the GUI will do)."""
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)
    events: list[dict] = []

    async def cb(msg):
        events.append(msg)
        if msg["type"] == "session_created":
            # The GUI's move on `session_created`: open the child's live view.
            mgr.register_session_client(msg["data"]["session_id"], cb)

    mgr.register_event_client(cb)

    out = _spawn(mgr, parent.session_id)

    created = [e for e in events if e["type"] == "session_created"]
    assert len(created) == 1
    assert created[0]["data"] == {
        "session_id": out["task_id"],
        "parent_session": parent.session_id,
        "title": "Investigate the cache (@general subagent)",
        "agent": "general",
        "model": "gpt-5.5",  # inherited from the parent — general declares none
        "kind": "subagent",
    }
    types = [e["type"] for e in events]
    assert types.index("session_created") < types.index("turn_start")
    # The child's own events arrived on its session stream, which only existed from the
    # registration inside the announcement onward.
    assert "assistant_message" in types and "turn_done" in types


def test_a_child_session_status_is_busy_then_idle(tmp_path):
    """The sibling list's mark: busy on the claim, idle when the turn settles. Only the
    child emits these (parents keep today's stream), so the list is exactly the child's
    two transitions and nothing else."""
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)
    events, cb = _collector()
    mgr.register_event_client(cb)

    out = _spawn(mgr, parent.session_id)

    statuses = [
        (e["data"]["session_id"], e["data"]["status"])
        for e in events
        if e["type"] == "session_status"
    ]
    assert statuses == [(out["task_id"], "busy"), (out["task_id"], "idle")]


# -- step C: the parent's task row goes live -----------------------------------------


def test_the_parent_row_carries_the_childs_live_tool_metadata(tmp_path):
    """Tool metadata: while the child runs, its tools are pushed
    to the PARENT's stream as `tool_metadata` frames carrying the task call's id, so a
    client can attach them to the running row. The settlement is the task's own
    `tool_finished` (whose presentation names the child session) — no metadata frame
    arrives after it, which is what keeps the row from being re-marked running."""
    provider = ScriptedProvider(
        [
            _tool_turn(
                "task",
                {
                    "description": "delegate the lookup",
                    "prompt": "find where the cache lives",
                    "subagent_type": "general",
                },
                call_id="call_task",
            ),
            _tool_turn("grep", {"pattern": "cache"}, call_id="call_grep"),
            _text("the child report"),
            _text("delegated and done"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-meta")
    events, cb = _collector()
    mgr.register_session_client(parent.session_id, cb)

    async def dead(_msg):
        raise RuntimeError("this parent view is gone")

    mgr.register_session_client(parent.session_id, dead)

    asyncio.run(mgr.deliver_to_session(parent.session_id, "please delegate"))

    child_id = mgr.children_of(parent.session_id)[0]
    frames = [e for e in events if e["type"] == "tool_metadata"]
    assert frames, "the parent must see the child's live metadata"
    # Every frame is addressed to the running `task` call, never to the child's own view.
    assert {f["data"]["call_id"] for f in frames} == {"call_task"}
    # Creation announces the child with a zero count, the spawn's wall-clock start, the
    # child row's model and the foreground marker (P2 part metadata).
    creation = frames[0]["data"]["metadata"]
    assert creation["session_id"] == child_id
    assert creation["parent_session_id"] == parent.session_id
    assert creation["title"] == "delegate the lookup"
    assert creation["toolcalls"] == 0
    assert isinstance(creation["started_at"], float) and creation["started_at"] > 0
    assert creation["model"] == "gpt-5.5"
    assert creation["background"] is False
    assert "job_id" not in creation
    live = [
        f["data"]["metadata"]
        for f in frames
        if f["data"]["metadata"].get("tool")
    ]
    assert len(live) == 1
    assert live[0]["session_id"] == child_id
    assert live[0]["tool"] == "grep"
    assert live[0]["toolcalls"] == 1
    assert live[0]["phase"] == "start"
    # The title is grep's own presentation summary, not the raw arguments JSON.
    assert live[0]["title"] == "Search for “cache”"
    # Settlement: the task result names the child session, and nothing re-marks the row
    # running after that.
    finished = [
        i
        for i, e in enumerate(events)
        if e["type"] == "tool_finished" and e["data"].get("name") == "task"
    ]
    assert finished
    assert events[finished[-1]]["data"]["presentation"]["run_id"] == child_id
    assert max(i for i, e in enumerate(events) if e["type"] == "tool_metadata") < finished[-1]


class _Streaming(ScriptedProvider):
    """Each scripted turn arrives the way a streaming route sends it: thinking, then the
    sentence in pieces, then the whole turn."""

    def stream(self, *, model, messages, tools=None, **settings):
        from harness.llm.providers import StreamChunk

        turn = self.complete(model=model, messages=messages, tools=tools, **settings)
        if turn.reasoning:
            for piece in (turn.reasoning[:3], turn.reasoning[3:]):
                yield StreamChunk(reasoning_delta=piece)
        if turn.text:
            for piece in (turn.text[:3], turn.text[3:6], turn.text[6:]):
                yield StreamChunk(text_delta=piece)
        yield StreamChunk(turn=turn)


def _child_frames(events: list[dict]) -> list[dict]:
    return [e["data"]["metadata"] for e in events if e["type"] == "tool_metadata"]


def test_the_parent_row_is_told_what_the_child_has_spent_so_far(tmp_path):
    """A child's spend used to reach its row only with the result, so a child that
    had been running for ten minutes showed no number at all. After each of its steps the row
    is told the total so far — the same fold the result reports at the end."""
    provider = ScriptedProvider(
        [
            _tool_turn(
                "task",
                {
                    "description": "delegate the lookup",
                    "prompt": "find where the cache lives",
                    "subagent_type": "general",
                },
                call_id="call_task",
            ),
            AssistantTurn(
                tool_calls=[ToolCall(id="call_grep", name="grep", arguments={"pattern": "cache"})],
                finish_reason="tool_calls",
                usage=TokenUsage(input=100, output=10),
            ),
            _text("the child report", usage=TokenUsage(input=200, output=20)),
            _text("delegated and done"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-spend")
    events, cb = _collector()
    mgr.register_session_client(parent.session_id, cb)

    asyncio.run(mgr.deliver_to_session(parent.session_id, "please delegate"))

    frames = _child_frames(events)
    spent = [frame["tokens"] for frame in frames if "tokens" in frame]
    assert spent == [110, 330]  # cumulative, one frame per step that spent something
    # The first figure is on the row BEFORE the tool of that step runs: the child may sit in
    # that call for minutes, and that is when a reader looks.
    order = [
        "tokens" if "tokens" in frame else "tool" if frame.get("tool") else ""
        for frame in frames
    ]
    assert [mark for mark in order if mark] == ["tokens", "tool", "tokens"]
    # The last figure is what the result reports, so the row does not jump when it settles.
    done = next(
        e["data"] for e in events
        if e["type"] == "tool_finished" and e["data"].get("name") == "task"
    )
    assert '"tokens": 330' in done["result_preview"]


def test_the_parent_row_is_told_when_the_child_thinks_and_when_it_writes(tmp_path):
    """Between its tools a child is thinking or writing. One frame per change of phase —
    never one per streamed piece — and a tool call ends the phase, so the next thought is
    announced again."""
    provider = _Streaming(
        [
            _tool_turn(
                "task",
                {
                    "description": "delegate the lookup",
                    "prompt": "find where the cache lives",
                    "subagent_type": "general",
                },
                call_id="call_task",
            ),
            AssistantTurn(
                reasoning="where would a cache live",
                tool_calls=[ToolCall(id="call_grep", name="grep", arguments={"pattern": "cache"})],
                finish_reason="tool_calls",
            ),
            AssistantTurn(
                reasoning="found it, now say so",
                text="the child report, written out",
                finish_reason="stop",
            ),
            _text("delegated and done"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-phase")
    events, cb = _collector()
    mgr.register_session_client(parent.session_id, cb)

    asyncio.run(mgr.deliver_to_session(parent.session_id, "please delegate"))

    frames = _child_frames(events)
    told = [
        frame["phase"] if frame.get("phase") in ("thinking", "writing") else "tool"
        for frame in frames
        if frame.get("phase")
    ]
    assert told == ["thinking", "tool", "thinking", "writing"]


def test_the_settle_frame_carries_the_childs_elapsed(tmp_path):
    """P2: the settled `↳ N toolcalls · duration` line needs the child's elapsed, and the
    child's timestamps do not ride the `task` part. The manager therefore measures the
    duration as a task card does — last assistant stamp minus first user stamp
 — and pushes it on a settle `tool_metadata` frame before the
    tool result lands."""

    class _Slow(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            time.sleep(0.05)
            return super().complete(
                model=model, messages=messages, tools=tools, **settings
            )

    provider = _Slow(
        [
            _tool_turn("task", {
                "description": "delegate the lookup",
                "prompt": "find where the cache lives",
                "subagent_type": "general",
            }, call_id="call_task"),
            _text("the child report"),
            _text("delegated and done"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-duration")
    events, cb = _collector()
    mgr.register_session_client(parent.session_id, cb)

    asyncio.run(mgr.deliver_to_session(parent.session_id, "please delegate"))

    frames = [e for e in events if e["type"] == "tool_metadata"]
    settle = frames[-1]["data"]["metadata"]
    assert settle["session_id"] == mgr.children_of(parent.session_id)[0]
    assert settle["toolcalls"] == 0
    assert isinstance(settle["ended_at"], float) and settle["ended_at"] > 0
    # The child slept 50ms between its user message and its answer, so the transcript pair
    # reports that span (not the ~0 a tool-call wall clock would show on a warm path).
    assert settle["duration_ms"] >= 40
    # The settle frame is last: nothing decorates the row after the call finished.
    finished = [
        i
        for i, e in enumerate(events)
        if e["type"] == "tool_finished" and e["data"].get("name") == "task"
    ]
    assert finished
    assert max(i for i, e in enumerate(events) if e["type"] == "tool_metadata") < finished[-1]


def test_a_background_spawn_carries_job_id_and_background(tmp_path):
    """Part metadata for a detached task: `{model, background: true, jobId}`. It rides the first frame; the GUI may not draw these
    yet, but a client that addresses the job needs them on the wire."""
    provider = ScriptedProvider([_text("the background report"), _text("acknowledged")])
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-job")
    seen: list[dict] = []

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="find where the cache lives",
            agent_name="general",
            background=True,
            on_metadata=seen.append,
        )
        await _wait_background(mgr)
        return out

    out = asyncio.run(main())

    creation = seen[0]
    assert creation["session_id"] == out["task_id"]
    assert creation["parent_session_id"] == parent.session_id
    assert creation["title"] == "Investigate the cache"
    assert creation["background"] is True
    assert creation["job_id"] == out["task_id"]
    assert creation["model"] == "gpt-5.5"


def test_a_childs_retry_is_pushed_to_the_parent_row(tmp_path, monkeypatch):
    """P2: while the child's provider call is going again, the parent's row must say so —
    `↳ Retrying (attempt N) · <message>`. The child engine's
    `retrying` event is the source; a `retry: null` frame clears it once the same request
    produces something."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))

    class _FlakyOnce(ScriptedProvider):
        def __init__(self, turns):
            super().__init__(turns)
            self.attempts = 0

        def complete(self, *, model, messages, tools=None, **settings):
            self.attempts += 1
            # Call 1 is the PARENT's tool turn; call 2 is the CHILD's first provider
            # call, which dies mid-stream once so the retry path is exercised.
            if self.attempts == 2:
                raise type("APIConnectionError", (Exception,), {})("the wire died")
            return super().complete(
                model=model, messages=messages, tools=tools, **settings
            )

    provider = _FlakyOnce(
        [
            _tool_turn("task", {
                "description": "delegate the lookup",
                "prompt": "find where the cache lives",
                "subagent_type": "general",
            }, call_id="call_task"),
            _text("the child report"),
            _text("delegated and done"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-retry")
    events, cb = _collector()
    mgr.register_session_client(parent.session_id, cb)

    asyncio.run(mgr.deliver_to_session(parent.session_id, "please delegate"))

    metas = [
        e["data"]["metadata"]
        for e in events
        if e["type"] == "tool_metadata"
    ]
    retrying = [m for m in metas if m.get("retry")]
    assert len(retrying) == 1
    assert retrying[0]["retry"]["attempt"] == 1
    assert "the wire died" in retrying[0]["retry"]["message"]
    # The clear frame arrives after it (the answer landed), and never before the retry.
    cleared = [i for i, m in enumerate(metas) if "retry" in m and m["retry"] is None]
    raised = [i for i, m in enumerate(metas) if m.get("retry")]
    assert cleared and min(cleared) > max(raised)


def test_children_view_lists_direct_children_with_their_status(tmp_path):
    """`GET /v1/sessions/{id}/children` (P3): direct `kind="subagent"` children only, as
    `{session_id, title, agent, status}`, with `status` from the manager's running set —
    the same fact `session_status` frames carry. A grandchild and a fork are not children
    of the queried session."""
    mgr = _manager(tmp_path, ScriptedProvider([]))
    parent = _parent(mgr, "parent-children")
    child = mgr.create_child_session(parent.session_id, "Investigate the cache", "explore", "m")
    grandchild = mgr.create_child_session(child, "go deeper", "general", "m")
    # A FORK shares `parent_session` as lineage but its kind is "root": it is an
    # independent session and must never appear as a child.
    mgr.fork_session(parent.session_id)

    idle = mgr.children_view(parent.session_id)
    assert [row["session_id"] for row in idle] == [child]
    assert idle[0] == {
        "session_id": child,
        "title": "Investigate the cache (@explore subagent)",
        "agent": "explore",
        "status": "idle",
    }

    mgr.mark_running(child)
    assert mgr.children_view(parent.session_id)[0]["status"] == "busy"
    mgr.mark_idle(child)
    assert mgr.children_view(parent.session_id)[0]["status"] == "idle"
    # The grandchild is a child of the child, never of the root.
    assert [row["session_id"] for row in mgr.children_view(child)] == [grandchild]


def test_a_long_task_report_is_truncated_for_the_model_and_spilled(tmp_path):
    """A task result over 2 000
    lines or 50 KiB is truncated for the parent MODEL with the full text spilled to the
    session's existing store and the delegate-flavoured hint. The child's own transcript
    keeps the whole report — that is the durable record, not the envelope."""
    report = "\n".join(f"finding {i}: the cache lives in module_{i}" for i in range(2500))

    provider = ScriptedProvider(
        [
            _tool_turn("task", {
                "description": "delegate the lookup",
                "prompt": "find where the cache lives",
                "subagent_type": "general",
            }, call_id="call_task"),
            _text(report),
            _text("delegated and done"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-truncate")

    asyncio.run(mgr.deliver_to_session(parent.session_id, "please delegate"))

    child_id = mgr.children_of(parent.session_id)[0]
    parent_after = mgr.session_store.load(parent.session_id)
    tool_results = [
        str(m.get("content"))
        for m in parent_after.messages
        if m.get("role") == "tool"
    ]
    envelope = next((c for c in tool_results if '<task_result>' in c), "")
    assert envelope, "the parent must have read the task's envelope"
    assert "delegate to save context" in envelope
    assert "Do NOT read the full file yourself" in envelope
    assert "finding 0:" in envelope and "finding 2499:" in envelope
    assert "omitted from the middle" in envelope
    # The spill locator names a real file under the session's own roots, and that file
    # holds the complete model-facing result (the full report).
    marker = envelope.split("the complete output is at ", 1)[1].split(" …", 1)[0]
    spilled = Path(marker)
    assert spilled.is_file()
    assert report in spilled.read_text(encoding="utf-8")
    # The child's own transcript stays whole: only the model-facing copy was bounded.
    child = mgr.session_store.load(child_id)
    assert report in [m.get("content") for m in child.messages]


def test_the_background_completion_injection_is_not_truncated(tmp_path):
    """The completion injected into the parent
    conversation carries the child's raw report. Only a foreground tool RESULT is bounded
    — the injection is read by the parent model through history, not as a tool result."""
    report = "\n".join(f"finding {i}: the cache lives in module_{i}" for i in range(2500))
    provider = ScriptedProvider([_text(report)])
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-bg-truncate")

    async def main():
        await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="find where the cache lives",
            agent_name="general",
            background=True,
        )
        await _wait_background(mgr)

    asyncio.run(main())

    injected = _injected(mgr, parent.session_id)
    assert len(injected) == 1
    content = str(injected[0]["content"])
    assert "finding 2499:" in content
    assert "delegate to save context" not in content
    assert "omitted from the middle" not in content


def test_a_raising_collector_cannot_break_a_spawn(tmp_path):
    """Best-effort means end to end: a dead app-wide socket AND a metadata observer that
    raises must leave the child unharmed — the spawn still returns the report."""
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)

    async def dead(_msg):
        raise RuntimeError("socket is dead")

    def dead_metadata(_metadata):
        raise RuntimeError("collector is dead")

    mgr.register_event_client(dead)  # session_created / session_status path
    mgr.register_session_client(parent.session_id, dead)  # turn-event path

    out = _spawn(mgr, parent.session_id, on_metadata=dead_metadata)

    assert out["status"] == "completed"
    assert out["report"] == "the report"
    assert mgr.session_store.load(out["task_id"]).messages[-1]["content"] == "the report"


# -- step D: background jobs, completion injection, recursive cancel -----------------


def _injected(mgr: SessionManager, parent_id: str) -> list[dict]:
    """The parent's transcript messages the background writer tagged synthetic."""
    record = mgr.session_store.load(parent_id)
    return [
        m
        for m in (record.messages if record else [])
        if isinstance(m.get("source"), dict)
        and m["source"].get("synthetic") == "background_task"
    ]


async def _wait_background(mgr: SessionManager, *, timeout: float = 15.0) -> None:
    """Wait until every background job in flight has ended, its result delivered.

    A job leaves the registry BEFORE its result is injected (a message the parent
    sends from the turn that result opens must resume the child, not join a job that is
    over), so an empty registry no longer means the injection is done. A bare test manager
    runs each job on its own thread (`_detach_background`), and the thread ends after the
    injection: those threads are what this waits for.
    """
    deadline = time.monotonic() + timeout
    threads = [t for t in list(mgr._background_tasks.values()) if isinstance(t, threading.Thread)]
    while (
        mgr._background_tasks or mgr._background_parents or any(t.is_alive() for t in threads)
    ) and time.monotonic() < deadline:
        threads += [
            t for t in list(mgr._background_tasks.values())
            if isinstance(t, threading.Thread) and t not in threads
        ]
        await asyncio.sleep(0.01)


def test_a_background_spawn_returns_running_and_injects_the_envelope(tmp_path):
    """The spawn answers with the SAME running envelope the thread
    branch returns, the child is a real session, and when its turn ends the finished
    envelope is delivered into the PARENT's conversation: model-facing `content`, and a
    display-only `source` sidecar tagged synthetic (a `synthetic: true` text
    part) so a client can skip the user bubble while the model still reads the result."""
    provider = ScriptedProvider(
        [
            _text("the background report"),
            _text("acknowledged the report"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="find where the cache lives",
            agent_name="general",
            background=True,
        )
        assert out["status"] == "running"
        assert 'state="running"' in out["report"]
        assert BACKGROUND_STARTED in out["report"]
        child_id = out["task_id"]
        # The child session exists the moment the spawn returns, and the registry holds
        # it while the turn is in flight.
        assert mgr.session_store.load(child_id).kind == "subagent"
        assert child_id in mgr._background_tasks
        assert mgr._background_parents[child_id] == parent.session_id
        await _wait_background(mgr)
        return out

    out = asyncio.run(main())
    child_id = out["task_id"]

    # Completion clears every registry view — the task, its parent link and its loop.
    assert mgr._background_tasks == {}
    assert mgr._background_parents == {}
    assert mgr._background_loops == {}

    injected = _injected(mgr, parent.session_id)
    assert len(injected) == 1
    # The sidecar is display-only: which child, and how it ended, for the line a surface
    # draws where the parent's next turn starts.
    assert injected[0]["source"]["synthetic"] == "background_task"
    assert injected[0]["source"]["session_id"] == child_id
    assert injected[0]["source"]["title"] == "Investigate the cache"
    assert injected[0]["source"]["state"] == "completed"
    content = injected[0]["content"]
    assert f'<task id="{child_id}" state="completed">' in content
    assert "Background task completed: Investigate the cache" in content
    assert "the background report" in content
    # The MODEL read it too: the parent's follow-up request carried the envelope (the
    # `source` sidecar is display-only and is stripped from every provider feed).
    assert any(
        f'<task id="{child_id}" state="completed">' in str(m.get("content") or "")
        for call in provider.calls
        for m in call["messages"]
    )


def test_a_failing_background_child_injects_the_error_envelope(tmp_path):
    """A crashed child is a result too: the parent gets `state="error"` with the failure
    summary, the same text the thread harness would report."""

    class _Exploding(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            # The child's prompt rides `api_content` (the prompt plus the frozen live
            # context), so match by containment rather than equality.
            if any(
                "find where the cache lives" in str(m.get("content"))
                for m in messages
                if isinstance(m, dict)
            ):
                raise RuntimeError("the provider exploded")
            return super().complete(
                model=model, messages=messages, tools=tools, **settings
            )

    provider = _Exploding([_text("acknowledged")])
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="find where the cache lives",
            agent_name="general",
            background=True,
        )
        await _wait_background(mgr)
        return out

    out = asyncio.run(main())

    injected = _injected(mgr, parent.session_id)
    assert len(injected) == 1
    content = injected[0]["content"]
    assert f'<task id="{out["task_id"]}" state="error">' in content
    assert "Background task failed: Investigate the cache" in content
    assert "the provider exploded" in content


def test_cancel_session_stops_a_background_child(tmp_path):
    """The Stop path (`harness/interface/server/app.py`, the WS `interrupt` frame) goes through
    `cancel_session`: the detached child's turn is interrupted through its live engine,
    the child session ends idle, its turn outcome is `interrupted` — not `completed` —
    and a stopped job injects NOTHING into the parent."""
    release = threading.Event()

    class _Hanging(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            release.wait(10)
            return _text("arrived after the stop")

    mgr = _manager(tmp_path, _Hanging([]))
    parent = _parent(mgr)
    events, cb = _collector()
    mgr.register_event_client(cb)

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="find where the cache lives",
            agent_name="general",
            background=True,
        )
        child_id = out["task_id"]
        # Let the detached task build the engine and start the provider call.
        for _ in range(500):
            if child_id in mgr._engines and mgr.is_running(child_id):
                break
            await asyncio.sleep(0.01)
        assert mgr.is_running(child_id)

        stopped = mgr.cancel_session(parent.session_id)

        await _wait_background(mgr)
        release.set()
        await asyncio.sleep(0.05)
        return out, stopped

    out, stopped = asyncio.run(main())
    child_id = out["task_id"]

    assert stopped["interrupted"] == [child_id]
    assert not mgr.is_running(child_id)
    statuses = [
        (e["data"]["session_id"], e["data"]["status"])
        for e in events
        if e["type"] == "session_status"
    ]
    assert statuses == [(child_id, "busy"), (child_id, "idle")]
    # The interrupted turn is persisted as the engine's notice: the outcome was
    # `interrupted`, not a completed run whose report was withheld.
    child = mgr.session_store.load(child_id)
    assert child.messages[-1]["role"] == "notice"
    assert child.messages[-1]["kind"] == "interrupted"
    assert _injected(mgr, parent.session_id) == []


def test_cancel_session_walks_grandchildren(tmp_path):
    """`cancel_session` is recursive over `kind="subagent"` links: a Stop on the root
    reaches every running descendant, grandchildren included (a transitive
    BackgroundJob cancel)."""
    mgr = _manager(tmp_path, ScriptedProvider([]))
    parent = _parent(mgr)
    child = mgr.create_child_session(parent.session_id, "one", "general", "m")
    grandchild = mgr.create_child_session(child, "two", "general", "m")
    bystander = mgr.create_child_session("someone-else", "three", "general", "m")

    class _Stub:
        def __init__(self) -> None:
            self.stops = 0

        def request_interrupt(self) -> None:
            self.stops += 1

    engines = {
        parent.session_id: _Stub(),
        child: _Stub(),
        grandchild: _Stub(),
        bystander: _Stub(),
    }
    mgr._engines.update(engines)
    for sid in engines:
        mgr.mark_running(sid)

    out = mgr.cancel_session(parent.session_id)

    assert out["interrupted"] == [parent.session_id, child, grandchild]
    assert [engines[sid].stops for sid in engines] == [1, 1, 1, 0]


def test_the_registry_never_leaks_a_foreground_child_or_a_finished_one(tmp_path):
    """Bookkeeping is for background children only: a foreground spawn never touches the
    registry, and a completed background child removes itself — no leaked entries."""
    provider = ScriptedProvider(
        [
            _text("first"),
            _text("second"),
            _text("acknowledged"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)

    first = _spawn(mgr, parent.session_id)
    assert first["status"] == "completed"
    assert mgr._background_tasks == {} and mgr._background_parents == {}

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="find where the cache lives",
            agent_name="general",
            background=True,
        )
        await _wait_background(mgr)
        return out

    asyncio.run(main())
    assert mgr._background_tasks == {}
    assert mgr._background_parents == {}
    assert mgr._background_loops == {}
    assert mgr._cancel_requested == set()


# -- P1: the session-scoped permission broker ----------------------------------------


def _frames(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


def _ask_config():
    """A config whose `general` agent turns `run_shell` into a ruleset `ask` (the default
    DEFAULTS end in `*: allow`), so a child's shell call must reach the broker."""
    from harness.config.config import Config

    return Config(agents={"general": {"permission": {"run_shell": "ask"}}})


def test_a_ruleset_allow_never_emits_permission_asked(tmp_path):
    """The F-fix, pinned: a child whose ruleset ALLOWS a tool runs it with no prompt.
    The engine's `rule_decider` returns `allow` before any approver is consulted, and
    the broker must stay empty — the P1 wiring must not turn every child tool into an
    ask."""
    provider = ScriptedProvider(
        [
            _tool_turn(
                "write_file",
                {"path": "allowed.txt", "content": "ran"},
                call_id="call_write",
            ),
            _text("the report"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)
    events, cb = _collector()
    mgr.register_event_client(cb)
    mgr.register_session_client(parent.session_id, cb)

    out = _spawn(mgr, parent.session_id)

    assert out["status"] == "completed"
    assert _frames(events, "permission_asked") == []
    assert mgr._permission_pending == {}
    assert (tmp_path / "allowed.txt").read_text() == "ran"


def test_a_ruleset_ask_parks_on_the_broker_and_once_lets_it_run(tmp_path, monkeypatch):
    """`permission_asked` names the CHILD session, its routing parent and the matched
    value; replying `once` resolves the parked future, the call runs and the child's turn
    completes with its normal result shape."""
    config = _ask_config()
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    provider = ScriptedProvider(
        [
            _tool_turn(
                "run_shell",
                {"command": "echo ZEBRA_MARKER | tee marker.txt"},
                call_id="call_shell",
            ),
            _text("the report"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)
    # Two collectors on purpose: the frame goes to the PARENT's session stream (the
    # viewed ancestor's dock) AND app-wide (the toast while elsewhere), and each target
    # must receive it exactly once.
    session_events, session_cb = _collector()
    app_events, app_cb = _collector()
    mgr.register_session_client(parent.session_id, session_cb)
    mgr.register_event_client(app_cb)

    async def main():
        task = asyncio.create_task(
            mgr.spawn_subagent(
                parent_id=parent.session_id,
                description="Investigate the cache",
                prompt="find where the cache lives",
                agent_name="general",
            )
        )
        for _ in range(1000):
            if _frames(session_events, "permission_asked"):
                break
            await asyncio.sleep(0.01)
        asked = _frames(session_events, "permission_asked")
        assert len(asked) == 1
        assert len(_frames(app_events, "permission_asked")) == 1
        assert _frames(app_events, "permission_asked")[0]["data"] == asked[0]["data"]
        child_id = mgr.children_of(parent.session_id)[0]
        data = asked[0]["data"]
        # The frame's shape, documented in `permission_broker.py`.
        assert data["session_id"] == child_id
        assert data["parent_session"] == parent.session_id
        assert data["permission"] == "run_shell"
        assert data["patterns"] == ["echo ZEBRA_MARKER | tee marker.txt"]
        assert data["always"] == ["echo ZEBRA_MARKER | tee marker.txt"]  # no standing candidate for exec
        assert data["metadata"] == {}
        assert data["tool"] == {"call_id": "call_shell"}
        assert mgr.pending_permissions() == [
            {
                "request_id": data["request_id"],
                "session_id": child_id,
                "parent_session": parent.session_id,
                "permission": "run_shell",
                "patterns": ["echo ZEBRA_MARKER | tee marker.txt"],
                "metadata": {},
            }
        ]
        assert mgr.reply_permission(data["request_id"], "once") == {"ok": True}
        out = await asyncio.wait_for(task, 30)
        return out, data["request_id"], child_id

    out, request_id, child_id = asyncio.run(main())

    assert out["status"] == "completed"
    assert out["report"] == "the report"
    assert mgr._permission_pending == {}
    child = mgr.session_store.load(out["task_id"])
    tool_results = " ".join(
        str(m.get("content")) for m in child.messages if m.get("role") == "tool"
    )
    assert "ZEBRA_MARKER" in tool_results  # the allowed call actually ran
    expected = {"request_id": request_id, "session_id": child_id, "reply": "once"}
    assert [e["data"] for e in _frames(session_events, "permission_replied")] == [
        expected
    ]
    assert [e["data"] for e in _frames(app_events, "permission_replied")] == [expected]


def test_a_reject_with_feedback_reaches_the_child_as_the_standard_string(
    tmp_path, monkeypatch
):
    """The exact `CorrectedError` string is what the
    child's model reads as the tool error — not our user-decline paragraph."""
    config = _ask_config()
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    provider = ScriptedProvider(
        [
            _tool_turn(
                "run_shell",
                {"command": "echo ZEBRA_MARKER | tee marker.txt"},
                call_id="call_shell",
            ),
            _text("understood — I will not run it"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)
    events, cb = _collector()
    mgr.register_event_client(cb)

    async def main():
        task = asyncio.create_task(
            mgr.spawn_subagent(
                parent_id=parent.session_id,
                description="Investigate the cache",
                prompt="find where the cache lives",
                agent_name="general",
            )
        )
        for _ in range(1000):
            if _frames(events, "permission_asked"):
                break
            await asyncio.sleep(0.01)
        rid = _frames(events, "permission_asked")[0]["data"]["request_id"]
        assert mgr.reply_permission(rid, "reject", "do not run that") == {"ok": True}
        return await asyncio.wait_for(task, 30)

    out = asyncio.run(main())

    assert out["status"] == "completed"
    child = mgr.session_store.load(out["task_id"])
    tool_results = " ".join(
        str(m.get("content")) for m in child.messages if m.get("role") == "tool"
    )
    assert (
        "The user rejected permission to use this specific tool call with the "
        "following feedback: do not run that"
    ) in tool_results
    assert "ZEBRA_MARKER" not in tool_results  # it never ran


def test_always_resolves_a_second_pending_request_with_the_same_pattern(tmp_path):
    """`always` mints a session grant and auto-resolves the sibling asks its patterns
    cover — and only within the SAME session: another session's
    identical ask stays parked (a documented difference). A later ask
    covered by the grant answers without parking and without a frame."""
    mgr = _manager(tmp_path, ScriptedProvider([]))

    async def main():
        first = mgr.request_permission(
            session_id="child-a",
            parent_id="root-1",
            permission="run_shell",
            patterns=["echo hi"],
            always=["echo hi"],
        )
        second = mgr.request_permission(
            session_id="child-a",
            parent_id="root-1",
            permission="run_shell",
            patterns=["echo hi"],
            always=["echo hi"],
        )
        other = mgr.request_permission(
            session_id="child-b",
            parent_id="root-1",
            permission="run_shell",
            patterns=["echo hi"],
            always=["echo hi"],
        )
        assert len(mgr.pending_permissions()) == 3
        rid = mgr.pending_permissions()[0]["request_id"]
        assert mgr.reply_permission(rid, "always") == {"ok": True}
        assert (await first).allowed and (await second).allowed
        assert other.done() is False
        assert [p["session_id"] for p in mgr.pending_permissions()] == ["child-b"]

        events, cb = _collector()
        mgr.register_event_client(cb)
        covered = mgr.request_permission(
            session_id="child-a",
            parent_id="root-1",
            permission="run_shell",
            patterns=["echo hi"],
            always=["echo hi"],
        )
        assert (await covered).allowed
        assert _frames(events, "permission_asked") == []
        assert [p["session_id"] for p in mgr.pending_permissions()] == ["child-b"]
        mgr.reply_permission(
            mgr.pending_permissions()[0]["request_id"], "reject"
        )

    asyncio.run(main())


def test_reject_cascades_to_the_sessions_other_pending_requests(tmp_path):
    """The reject rule: denying one ask denies
    every other ask still parked in the same session — the others with the PLAIN reject
    string, never this reply's feedback."""
    mgr = _manager(tmp_path, ScriptedProvider([]))

    async def main():
        first = mgr.request_permission(
            session_id="child-a",
            parent_id="root-1",
            permission="run_shell",
            patterns=["one"],
        )
        second = mgr.request_permission(
            session_id="child-a",
            parent_id="root-1",
            permission="run_shell",
            patterns=["two"],
        )
        elsewhere = mgr.request_permission(
            session_id="child-b",
            parent_id="root-1",
            permission="run_shell",
            patterns=["one"],
        )
        rid = mgr.pending_permissions()[0]["request_id"]
        assert mgr.reply_permission(rid, "reject", "no shells") == {"ok": True}
        first_out = await first
        second_out = await second
        assert first_out.reply == "reject" and second_out.reply == "reject"
        assert first_out.denial_message.endswith("feedback: no shells")
        assert second_out.denial_message == (
            "The user rejected permission to use this specific tool call."
        )
        assert elsewhere.done() is False
        assert [p["session_id"] for p in mgr.pending_permissions()] == ["child-b"]
        mgr.reply_permission(mgr.pending_permissions()[0]["request_id"], "reject")

    asyncio.run(main())


def test_a_spawn_ask_is_emitted_with_the_subagent_type_as_pattern(
    tmp_path, monkeypatch
):
    """`task` spawn ask (P1): when the caller's ruleset evaluates
    `task.<type>` to `ask`, the tool parks on the broker BEFORE spawning —
    `patterns: [subagent_type]`, `always: ["*"]`, metadata `{description,
    subagent_type}`. The ask goes to the CALLER's session (a root has no parent), and
    `once` lets the spawn proceed."""
    from harness.config.config import Config

    # The `task` permission lives on the CALLER's effective ruleset. The caller here is
    # the agent, so the config declares a table for it (a config agent entry
    # ADDS to `resolve_agents`'s map) with the `task` ask — the same way a person would
    # gate spawns.
    config = Config(agents={"reeve": {"permission": {"task": "ask"}}})
    monkeypatch.setattr("harness.compose.assembly.load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    parent_provider = ScriptedProvider(
        [
            _tool_turn(
                "task",
                {
                    "description": "delegate the lookup",
                    "prompt": "find where the cache lives",
                    "subagent_type": "general",
                },
                call_id="call_task",
            ),
            _text("the child findings"),
            _text("delegated and done"),
        ]
    )
    mgr = _manager(tmp_path, parent_provider)
    parent = _parent(mgr, "parent-spawn-ask")
    # The caller is a root, so the parent stream IS its own session stream; both targets
    # must carry the ask (and each exactly once).
    session_events, session_cb = _collector()
    app_events, app_cb = _collector()
    mgr.register_session_client(parent.session_id, session_cb)
    mgr.register_event_client(app_cb)

    replies: list[dict] = []

    def auto_reply() -> None:
        # The parent turn blocks on a worker thread here, so the reply must come from
        # another thread — the same position the real HTTP route is in.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            asked = _frames(session_events, "permission_asked")
            if asked:
                replies.append(
                    mgr.reply_permission(asked[0]["data"]["request_id"], "once")
                )
                return
            time.sleep(0.01)

    thread = threading.Thread(target=auto_reply, daemon=True)
    thread.start()
    asyncio.run(mgr.deliver_to_session(parent.session_id, "please delegate"))
    thread.join(5)

    asked = _frames(session_events, "permission_asked")
    assert len(asked) == 1
    assert len(_frames(app_events, "permission_asked")) == 1
    data = asked[0]["data"]
    assert data["permission"] == "task"
    assert data["patterns"] == ["general"]
    assert data["always"] == ["*"]
    assert data["metadata"] == {
        "description": "delegate the lookup",
        "subagent_type": "general",
    }
    assert data["session_id"] == parent.session_id
    assert data["parent_session"] is None  # a root session routes to itself
    assert data["tool"] == {"call_id": "call_task"}
    assert replies == [{"ok": True}]
    # The spawn proceeded: the child exists and the parent read its envelope + answer.
    children = mgr.children_of(parent.session_id)
    assert len(children) == 1
    parent_after = mgr.session_store.load(parent.session_id)
    assert parent_after.messages[-1]["content"] == "delegated and done"
    assert mgr._permission_pending == {}


def test_cancel_session_resolves_a_pending_permission_request(
    tmp_path, monkeypatch
):
    """A Stop while an ask is parked must not leak: the child's future resolves, the
    registry empties, and the `permission_replied` frame clears the dock."""
    config = _ask_config()
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    provider = ScriptedProvider(
        [_tool_turn("run_shell", {"command": "echo held | tee held.txt"}, call_id="call_shell")]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)
    events, cb = _collector()
    mgr.register_event_client(cb)

    async def main():
        task = asyncio.create_task(
            mgr.spawn_subagent(
                parent_id=parent.session_id,
                description="Investigate the cache",
                prompt="find where the cache lives",
                agent_name="general",
            )
        )
        for _ in range(1000):
            if _frames(events, "permission_asked"):
                break
            await asyncio.sleep(0.01)
        asked = _frames(events, "permission_asked")
        assert len(asked) == 1
        child_id = asked[0]["data"]["session_id"]
        assert mgr._permission_pending != {}
        stopped = mgr.cancel_session(parent.session_id)
        assert stopped["interrupted"] == [child_id]
        assert mgr._permission_pending == {}  # no dangling future
        out = await asyncio.wait_for(task, 30)
        return out, asked[0]["data"]["request_id"]

    out, rid = asyncio.run(main())

    assert out["status"] == "interrupted"
    assert mgr._permission_pending == {}
    assert _frames(events, "permission_replied") == [
        {
            "type": "permission_replied",
            "data": {"request_id": rid, "session_id": out["task_id"], "reply": "reject"},
        }
    ]


# -- P6: promoting a foreground task to background -----------------------------------


def test_promotion_marks_the_parent_row_background_and_keeps_the_child_running(tmp_path):
    """P6: promotion to background (
   ): while the spawn awaits the child, a promote resolves it with the
    `state="running"` envelope — with `BACKGROUND_MOVED`, the notice for a caller that asked
    to wait and has no result yet — the
    child keeps working, and the parent row gets the `{background: true, job_id}` metadata
    frame that turns its card into `(background)`. On completion the finished envelope is
    injected into the parent EXACTLY once (the step-D writer, not a foreground result)."""
    release = threading.Event()

    class _Hanging(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append({"model": model, "messages": messages})
            release.wait(10)
            return _text("the final report")

    mgr = _manager(tmp_path, _Hanging([]))
    parent = _parent(mgr, "parent-promote")
    seen: list[dict] = []

    async def main():
        spawn = asyncio.create_task(
            mgr.spawn_subagent(
                parent_id=parent.session_id,
                description="Investigate the cache",
                prompt="find where the cache lives",
                agent_name="general",
                on_metadata=seen.append,
            )
        )
        for _ in range(500):
            ids = mgr.children_of(parent.session_id)
            if ids and mgr.is_running(ids[0]):
                break
            await asyncio.sleep(0.01)
        child_id = mgr.children_of(parent.session_id)[0]
        assert mgr.is_running(child_id)

        out = mgr.promote_subagent(parent.session_id)

        assert out == {
            "ok": True,
            "session_id": parent.session_id,
            "promoted": [child_id],
        }
        result = await asyncio.wait_for(spawn, 30)
        assert result["status"] == "running"
        assert f'<task id="{child_id}" state="running">' in result["report"]
        assert BACKGROUND_MOVED in result["report"]
        assert BACKGROUND_STARTED not in result["report"]
        # The child did not stop when the spawn returned: same session, same engine.
        assert mgr.is_running(child_id)
        assert child_id in mgr._background_tasks
        assert mgr._background_parents[child_id] == parent.session_id
        assert mgr._promotable == {}
        # The promotion frame is the row's fact (`ctx.metadata` on `onPromote`).
        promotion = [m for m in seen if m.get("background") is True]
        assert promotion and promotion[-1]["job_id"] == child_id
        assert promotion[-1]["session_id"] == child_id

        release.set()
        await _wait_background(mgr)
        return child_id

    child_id = asyncio.run(main())

    assert mgr._background_tasks == {}
    assert mgr._background_parents == {}
    assert mgr._background_loops == {}
    injected = _injected(mgr, parent.session_id)
    assert len(injected) == 1
    assert f'<task id="{child_id}" state="completed">' in injected[0]["content"]
    assert "the final report" in injected[0]["content"]
    # The child's own transcript survived the promotion whole.
    child = mgr.session_store.load(child_id)
    assert child.kind == "subagent"
    assert "the final report" in [m.get("content") for m in child.messages]


def test_a_promoted_foreground_tool_returns_running_to_the_parent_model(tmp_path):
    """P6 end to end: the parent's `task` call is
    parked on a foreground child; promoting the session releases the tool call with the
    running envelope, so the parent's turn CONTINUES while the child works. The child's
    completion then lands as the injected envelope in the parent's conversation."""
    release = threading.Event()
    child_started = threading.Event()

    class _SlowChild(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            # The child engine is the one whose system prompt is the child base prompt
            # (`CHILD_BASE_PROMPT`); the parent stays scripted.
            if messages and "You are a subagent" in str(messages[0].get("content") or ""):
                self.calls.append({"model": model, "messages": messages})
                child_started.set()
                release.wait(10)
                return _text("the child findings")
            if not self._turns:
                # Autotitle / memory extraction share this provider; their calls are not
                # the material under test and must not fail a turn.
                return _text("")
            return super().complete(
                model=model, messages=messages, tools=tools, **settings
            )

    provider = _SlowChild(
        [
            _tool_turn(
                "task",
                {
                    "description": "delegate the lookup",
                    "prompt": "find where the cache lives",
                    "subagent_type": "general",
                },
                call_id="call_task",
            ),
            _text("delegated and running"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-promote-e2e")

    async def main():
        # The app's lifespan makes the manager's server loop known (`start_gateway`);
        # `_spawn_subagent_sync` then hands the child's turn to it, so a promoted child
        # keeps running after the tool bridge returns. Without it the bridge falls back
        # to a private `asyncio.run` that closes with the spawn — mirror the app here.
        mgr._loop = asyncio.get_running_loop()
        turn = asyncio.create_task(
            mgr.deliver_to_session(parent.session_id, "please delegate")
        )
        for _ in range(1000):
            if child_started.is_set() and mgr.children_of(parent.session_id):
                break
            await asyncio.sleep(0.01)
        assert child_started.is_set()
        child_id = mgr.children_of(parent.session_id)[0]
        assert mgr.is_running(child_id)

        assert mgr.promote_subagent(parent.session_id)["ok"] is True
        # The parent's turn ends NOW — it is not waiting for the child any more.
        await asyncio.wait_for(turn, 30)
        assert mgr.is_running(child_id)

        release.set()
        await _wait_background(mgr)
        return child_id

    child_id = asyncio.run(main())

    parent_after = mgr.session_store.load(parent.session_id)
    contents = [str(m.get("content")) for m in parent_after.messages]
    tool_results = [
        str(m.get("content")) for m in parent_after.messages if m.get("role") == "tool"
    ]
    assert any(
        f'<task id="{child_id}" state="running">' in c and BACKGROUND_MOVED in c
        for c in tool_results
    )
    # The parent's turn after the tool result was the ordinary continuation — it did not
    # wait for the child, which is the whole point of the promotion.
    assert "delegated and running" in contents
    injected = _injected(mgr, parent.session_id)
    assert len(injected) == 1
    assert f'<task id="{child_id}" state="completed">' in injected[0]["content"]
    assert "the child findings" in injected[0]["content"]


def test_promoting_with_nothing_running_is_a_noop(tmp_path):
    """The endpoint answers
    `{ok: false, error}` — a no-op a route can echo, never an exception."""
    mgr = _manager(tmp_path, ScriptedProvider([]))
    parent = _parent(mgr)
    assert mgr.promote_subagent(parent.session_id) == {
        "ok": False,
        "error": "no running foreground task to promote",
    }


def test_a_child_that_finished_before_the_promote_is_untouched(tmp_path):
    """The race's other side: a foreground child that already returned is never promoted —
    the spawn's entry left `_promotable` with the result, promote answers `{ok: false}`,
    and nothing is injected (the tool result was the delivery)."""
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report")]))
    parent = _parent(mgr)

    out = _spawn(mgr, parent.session_id)
    assert out["status"] == "completed"
    assert mgr._promotable == {}
    assert mgr.promote_subagent(parent.session_id)["ok"] is False
    assert mgr._background_tasks == {}
    assert _injected(mgr, parent.session_id) == []
    completed = mgr.session_store.load(out["task_id"])
    assert "the report" in [m.get("content") for m in completed.messages]


# -- P5: subagent_depth (config + nested `task`) -------------------------------------


def test_depth_zero_removes_the_task_tool_from_the_session(tmp_path, monkeypatch):
    """A depth of `0` disables delegation: with `subagent_depth = 0` the `task` tool is
    never registered for the session, so the model cannot propose it at all. The direct
    call refusal is pinned in `test_task.py` (for a host that registers it by hand)."""
    from harness.config.config import Config

    config = Config(subagent_depth=0)
    monkeypatch.setattr("harness.compose.assembly.load_config", lambda *a, **k: config)
    mgr = _manager(tmp_path, ScriptedProvider([]))
    parent = _parent(mgr)

    engine = mgr.get_engine(parent.session_id)

    assert "task" not in engine.registry.names()
    # Everything else the persona declares is untouched — only delegation is off.
    assert "read_file" in engine.registry.names()


def test_depth_two_lets_an_opted_in_agent_spawn_a_grandchild(tmp_path, monkeypatch):
    """`subagent_depth = 2` + an agent whose OWN ruleset mentions `task`
    (`child_permission`'s opt-in): the parent spawns `nested`, the nested child holds
    `task` and spawns a grandchild, and the grandchild — at the limit — does not."""
    from harness.config.config import Config

    config = Config(
        subagent_depth=2,
        agents={
            "nested": {
                "description": "Delegates one more level",
                "permission": {"task": "allow"},
            }
        },
    )
    monkeypatch.setattr("harness.compose.assembly.load_config", lambda *a, **k: config)
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.load_config", lambda *a, **k: config
    )
    provider = ScriptedProvider(
        [
            _tool_turn(
                "task",
                {
                    "description": "delegate",
                    "prompt": "spawn a nested one",
                    "subagent_type": "nested",
                },
                call_id="call_parent",
            ),
            _tool_turn(
                "task",
                {
                    "description": "go deeper",
                    "prompt": "deep work please",
                    "subagent_type": "general",
                },
                call_id="call_child",
            ),
            _text("the grandchild report"),
            _text("the child report"),
            _text("delegated and done"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-depth")

    asyncio.run(mgr.deliver_to_session(parent.session_id, "please delegate"))

    child_id = mgr.children_of(parent.session_id)[0]
    grandchild_id = mgr.children_of(child_id)[0]
    # Both levels were real child sessions with their own transcripts.
    grandchild = mgr.session_store.load(grandchild_id)
    assert grandchild.kind == "subagent"
    assert "deep work please" in [m.get("content") for m in grandchild.messages]
    # The nested child kept `task` (depth 1 < 2, agent opted in)...
    assert "task" in mgr._engines[child_id].registry.names()
    # ...and the grandchild (depth 2) did not, so the chain stops exactly at the limit.
    assert "task" not in mgr._engines[grandchild_id].registry.names()

    # A spawn attempted FROM the grandchild refuses with the standard sentence, read
    # off the real ancestry walk (caller at depth 2, limit 2).
    from harness.tools.task import task_tools
    from harness.tools.task_agents import depth_limit_message

    tools = task_tools(
        workspace=tmp_path,
        provider=ScriptedProvider([]),
        model="gpt-5.5",
        session_id=grandchild_id,
        config=config,
        session_depth=lambda: mgr.session_depth(grandchild_id),
    )
    deeper = next(t for t in tools if getattr(t, "__name__", "") == "task")
    assert deeper(description="deeper", prompt="p", subagent_type="general") == {
        "error": depth_limit_message(2)
    }


def test_session_depth_walks_child_links_and_stops_at_a_fork(tmp_path):
    """The walker behind the depth check: `kind="subagent"` hops only. A FORK shares the
    `parent_session` column but is a visible root, so it reads as depth 0 —
    otherwise every fork would lose delegation under the default limit."""
    mgr = _manager(tmp_path, ScriptedProvider([]))
    parent = _parent(mgr, "root-depth")
    child = mgr.create_child_session(parent.session_id, "one", "general", "m")
    grandchild = mgr.create_child_session(child, "two", "general", "m")
    fork = mgr.fork_session(parent.session_id)["session_id"]

    assert mgr.session_depth(parent.session_id) == 0
    assert mgr.session_depth(child) == 1
    assert mgr.session_depth(grandchild) == 2
    assert mgr.session_depth(fork) == 0
    assert mgr.session_depth("ghost") == 0


def test_a_second_spawn_to_a_busy_session_is_dropped(tmp_path):
    """A spawn (or resume) aimed at a session that is already running does
    NOT start a second turn. `spawn_subagent` claims through `try_mark_running`, the
    claim fails, and the tool receives the `already running` error result — the same
    one-turn-at-a-time rule the WS `user_message` path pins in `test_server.py`."""
    mgr = _manager(tmp_path, ScriptedProvider([]))
    parent = _parent(mgr)
    child = mgr.create_child_session(parent.session_id, "one", "general", "m")
    assert mgr.try_mark_running(child) is True

    out = _spawn(mgr, parent.session_id, task_id=child, prompt="again")

    assert out == {
        "task_id": child,
        "status": "error: this task is already running",
        "report": "",
        "tokens": 0,
    }
    # The running turn still owns the claim; a second claim cannot steal it.
    assert mgr.is_running(child)
    assert mgr.try_mark_running(child) is False
    mgr.mark_idle(child)
    assert mgr.is_running(child) is False


# -- P4: the server half of @-mentions -----------------------------------------------


def test_mention_instructions_validate_names_and_build_the_standard_text(tmp_path):
    """`GET`-less: the manager's mention resolver is what the WS `user_message` frame
    calls. Names are validated against the mentionable set (`!hidden && mode !==
    "primary"`), and the instruction is the synthetic text,
    with the caller-deny clause when the caller's own ruleset denies `task.<name>`."""
    from harness.tools.task_agents import mention_instruction

    mgr = _manager(tmp_path, ScriptedProvider([]))
    plan = mgr.mention_instructions(None, "reeve", ["explore"])
    assert plan == {"ok": True, "text": mention_instruction("explore")}

    bad = mgr.mention_instructions(None, "reeve", ["ghost"])
    assert bad == {
        "ok": False,
        "error": "Unknown agent type: ghost is not a valid agent type",
    }


def test_mention_instructions_filter_primaries_and_hidden_agents(tmp_path, monkeypatch):
    from harness.config.config import Config

    config = Config(
        agents={
            "boss": {"mode": "primary", "description": "A primary"},
            "secret": {"mode": "all", "hidden": True, "description": "Hidden"},
        }
    )
    monkeypatch.setattr(
        "harness.interface.server.manager.subagent_catalog.load_config", lambda *a, **k: config
    )
    mgr = _manager(tmp_path, ScriptedProvider([]))

    for name in ("boss", "secret"):
        assert mgr.mention_instructions(None, "reeve", [name]) == {
            "ok": False,
            "error": f"Unknown agent type: {name} is not a valid agent type",
        }


def test_mention_instructions_carry_the_denied_caller_clause(tmp_path, monkeypatch):
    """The caller's DENY is what adds "Invoked by user; guaranteed to exist." — the
    mention bypasses the ask, so the model is the one that must be told the agent exists."""
    from harness.config.config import Config
    from harness.tools.task_agents import mention_instruction

    config = Config(agents={"reeve": {"permission": {"task": "deny"}}})
    monkeypatch.setattr(
        "harness.interface.server.manager.subagent_catalog.load_config", lambda *a, **k: config
    )
    mgr = _manager(tmp_path, ScriptedProvider([]))

    plan = mgr.mention_instructions(None, "reeve", ["explore"])
    assert plan["ok"] is True
    assert plan["text"] == mention_instruction("explore", denied=True)


# -- A child of a remote session runs remotely --------------------


def test_a_child_of_a_remote_parent_runs_in_the_remote_world(tmp_path):
    """The child inherits the parent's environment binding, and `_run_child_turn` builds
    its engine over the same host — not a LocalWorld rooted at a POSIX path."""
    from harness.orchestration.remote_env import RemoteWorld
    from harness.orchestration.remote_environments import RemoteEnvironmentStore
    from harness.testing.fake_ssh import FakeRemoteHost, FakeSsh

    remote_root = "/home/dev/project"
    fake = FakeSsh(host=FakeRemoteHost(directories={remote_root}))
    mgr = _manager(tmp_path, ScriptedProvider([_text("the final report")]))
    store = RemoteEnvironmentStore(tmp_path / "remote-environments.json", runner=fake)
    mgr.remote_environments = store
    profile = store.create({"name": "Build server", "host": "dev.example.com", "user": "dev"})

    mgr.session_store.save(
        SessionRecord(
            session_id="parent-r",
            workspace=remote_root,
            model="gpt-5.5",
            mode="interactive",
            messages=[
                {"role": "user", "content": "the parent's first question"},
                {"role": "assistant", "content": "the parent's first answer"},
            ],
            title="remote parent",
            agent="reeve",
        )
    )
    assert mgr.set_session_environment("parent-r", profile["id"], remote_root)["ok"]

    out = _spawn(mgr, "parent-r", agent_name="general")
    assert out["status"] == "completed", out
    child = mgr.session_store.load(out["task_id"])
    assert child.environment_id == profile["id"]
    engine = mgr._engines[out["task_id"]]
    assert isinstance(engine.world, RemoteWorld)
    assert engine.world.root == remote_root
    # The child's own commands run on the host, not on this machine.
    assert engine.executor.run("pwd")["output"].strip() == remote_root


# -- fork (inherit_context) + running-chain extension --------------------------------


def test_inherit_context_seeds_the_child_and_drops_attachment_payloads(tmp_path):
    """`task(inherit_context=true)`: the child starts from the
    provider-legal prefix of the parent's transcript, while engine attachments (an image
    the parent looked at) survive as a text note instead of re-sending their bytes on
    every child request."""
    import json

    from harness.session.turns import ATTACHED_BY

    provider = ScriptedProvider([_text("carried on")])
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)
    parent.messages.append(
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}
            ],
            ATTACHED_BY: "call-1",
        }
    )
    mgr.session_store.save(parent)

    out = _spawn(mgr, parent.session_id, prompt="carry on", inherit_context=True)
    assert out["status"] == "completed", out
    child = mgr.session_store.load(out["task_id"])
    assert child.seed_length == 3  # two text turns + the attachment note
    assert child.messages[0] == {
        "role": "user",
        "content": "the parent's first question",
    }
    assert "data:image" not in json.dumps(child.messages)
    assert any(
        "not carried into this fork" in str(m.get("content")) for m in child.messages
    )
    assert any(ATTACHED_BY in m for m in parent.messages)  # the parent is untouched


def test_a_running_background_child_is_extended_not_restarted(tmp_path):
    """Extending a background job: a `task_id` naming a child whose job is still going but
    which is not inside a turn (here the turn is faked, so nothing can read a message mid-turn)
    queues the prompt as a follow-up turn on the SAME job (`QUEUED`), and exactly
    one completion injection covers both turns at the end."""
    from harness.tools.task_message import QUEUED

    provider = ScriptedProvider([_text("acknowledged the report")])
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)
    calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    async def fake_child_turn(*, child_id, parent_id, prompt, agent, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            started.set()
            await asyncio.to_thread(release.wait, 10.0)
            return {"status": "completed", "report": "first report", "tokens": 0}
        return {"status": "completed", "report": "second report", "tokens": 0}

    mgr._run_child_turn = fake_child_turn  # type: ignore[method-assign]

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="first prompt",
            agent_name="general",
            background=True,
        )
        child_id = out["task_id"]
        await asyncio.to_thread(started.wait, 10.0)
        updated = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="second prompt",
            agent_name="general",
            task_id=child_id,
        )
        assert updated["status"] == "running"
        assert QUEUED in updated["report"]
        release.set()
        await _wait_background(mgr)
        return child_id

    asyncio.run(main())
    assert calls == ["first prompt", "second prompt"]

    injected = _injected(mgr, parent.session_id)
    assert len(injected) == 1
    assert "Background task completed: Investigate the cache" in injected[0]["content"]
    assert "second report" in injected[0]["content"]
    assert "first report" not in injected[0]["content"]
    assert mgr._background_followups == {}



# -- the hand-over at the rail and the fan-out cap --------------------------


def test_a_child_past_the_rail_is_handed_over_and_the_caller_is_told_it_has_no_result(
    tmp_path, monkeypatch
):
    """A foreground child that outlives the call's rail returns the running envelope without
    anyone promoting it, and its completion takes the same injection path. The notice is the
    moved one: the caller asked to wait and must not read "running" as "work elsewhere"."""
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.FOREGROUND_WAIT_SECONDS", 0.1
    )
    release = threading.Event()

    class _Hanging(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append({"model": model, "messages": messages})
            release.wait(10)
            return _text("the final report")

    mgr = _manager(tmp_path, _Hanging([]))
    parent = _parent(mgr, "parent-auto")
    seen: list[dict] = []

    async def main():
        spawn = asyncio.create_task(
            mgr.spawn_subagent(
                parent_id=parent.session_id,
                description="Investigate the cache",
                prompt="find where the cache lives",
                agent_name="general",
                on_metadata=seen.append,
            )
        )
        result = await asyncio.wait_for(spawn, 30)
        assert result["status"] == "running"
        assert BACKGROUND_MOVED in result["report"]
        assert BACKGROUND_STARTED not in result["report"]
        child_id = result["task_id"]
        assert mgr.is_running(child_id)
        assert child_id in mgr._background_tasks
        frame = [m for m in seen if m.get("background") is True]
        assert frame and frame[-1].get("auto") is True
        release.set()
        await _wait_background(mgr)
        return child_id

    child_id = asyncio.run(main())
    injected = _injected(mgr, parent.session_id)
    assert len(injected) == 1
    assert f'<task id="{child_id}" state="completed">' in injected[0]["content"]
    assert "the final report" in injected[0]["content"]


def test_the_fanout_cap_refuses_after_the_per_turn_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.MAX_SUBAGENTS_PER_TURN", 2
    )
    mgr = _manager(tmp_path, ScriptedProvider([_text("a"), _text("b"), _text("c")]))
    parent = _parent(mgr, "parent-cap")
    mgr.get_engine(parent.session_id, agent="reeve", workspace=str(tmp_path))

    first = _spawn(mgr, parent.session_id, description="one", prompt="one")
    second = _spawn(mgr, parent.session_id, description="two", prompt="two")
    third = _spawn(mgr, parent.session_id, description="three", prompt="three")

    assert first["status"] == "completed"
    assert second["status"] == "completed"
    assert "fan-out limit" in third.get("error", "")


def test_an_agents_options_reach_the_child_engines_model_settings(tmp_path, monkeypatch):
    """A per-definition output cap ALREADY works: `AgentInfo.options` merges into the
    child engine's `model_settings` LAST (the manager and the thread harness share the
    rule), so a config-declared agent with `options = { max_tokens = N }` caps its own
    child without new plumbing. Pinned so a refactor cannot drop it."""
    from harness.interface.server.manager import session_roots
    from harness.tools.task_agents import AgentInfo

    captured: dict = {}

    def fake_build_engine(*, agent, **kwargs):
        captured["model_settings"] = dict(kwargs.get("model_settings") or {})
        raise RuntimeError("captured before the child turn")

    monkeypatch.setattr(session_roots, "build_engine", fake_build_engine)
    monkeypatch.setattr(
        session_roots,
        "resolve_agents",
        lambda config: {"capped": AgentInfo(name="capped", options={"max_tokens": 1234})},
    )
    mgr = _manager(tmp_path, ScriptedProvider([_text("x")]))
    parent = _parent(mgr, "parent-options")

    out = _spawn(mgr, parent.session_id, agent_name="capped")

    assert str(out.get("status") or "").startswith("error") or "error" in out, out
    assert captured["model_settings"]["max_tokens"] == 1234


def _slow_child(release_after: float):
    """A child whose one reply takes `release_after` seconds — past a shortened timer."""

    class _Slow(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append({"model": model, "messages": messages})
            time.sleep(release_after)
            return _text("the final report")

    return _Slow([])


def test_a_program_s_spawn_waits_for_the_answer_past_the_timer(tmp_path, monkeypatch):
    """Orchestration audit O1: a workflow's `agent()` is a program's call. Backgrounded, it
    received the "running in the background" notice as the child's answer and the real
    report later opened an unasked parent turn."""
    monkeypatch.setattr("harness.interface.server.manager.session_roots.FOREGROUND_WAIT_SECONDS", 0.1)
    mgr = _manager(tmp_path, _slow_child(0.6))
    parent = _parent(mgr, "parent-program")

    result = _spawn(mgr, parent.session_id, origin="call_run:code:0")

    assert result["status"] == "completed"
    assert "the final report" in result["report"]
    assert BACKGROUND_STARTED not in result["report"]
    assert _injected(mgr, parent.session_id) == []


def test_a_subagent_s_own_spawn_waits_for_the_answer_past_the_timer(tmp_path, monkeypatch):
    """O1, nested: a child that backgrounds ITS child ends its turn with a "waiting"
    report, and that becomes the root's answer."""
    monkeypatch.setattr("harness.interface.server.manager.session_roots.FOREGROUND_WAIT_SECONDS", 0.1)
    mgr = _manager(tmp_path, _slow_child(0.6))
    root = _parent(mgr, "parent-root")
    middle = mgr.create_child_session(root.session_id, "Investigate", "general", "m")
    assert mgr.session_depth(middle) == 1

    result = _spawn(mgr, middle)

    assert result["status"] == "completed"
    assert "the final report" in result["report"]
    assert _injected(mgr, middle) == []


def test_a_refused_spawn_does_not_spend_the_fanout_cap(tmp_path, monkeypatch):
    """Orchestration audit O2: two mistyped agent names used to exhaust a cap of two, so
    the valid spawn after them was refused for the rest of the turn."""
    monkeypatch.setattr("harness.interface.server.manager.session_roots.MAX_SUBAGENTS_PER_TURN", 2)
    mgr = _manager(tmp_path, ScriptedProvider([_text("a"), _text("b")]))
    parent = _parent(mgr, "parent-typo")
    mgr.get_engine(parent.session_id, agent="reeve", workspace=str(tmp_path))

    typo = [_spawn(mgr, parent.session_id, agent_name="genral") for _ in range(2)]
    first = _spawn(mgr, parent.session_id, description="one", prompt="one")
    second = _spawn(mgr, parent.session_id, description="two", prompt="two")
    third = _spawn(mgr, parent.session_id, description="three", prompt="three")

    assert all("Unknown agent type" in t.get("error", "") for t in typo)
    assert (first["status"], second["status"]) == ("completed", "completed")
    assert "fan-out limit" in third.get("error", "")


def test_the_parent_gets_the_files_a_child_changed_beside_its_report(tmp_path):
    """Orchestration audit O3: the parent had only the child's own word. The child's
    receipt now rides the result, and the envelope the parent model reads lists it."""
    from harness.tools.task import changes_of, render_output

    provider = ScriptedProvider(
        [
            _tool_turn("write_file", {"path": "made.txt", "content": "one\ntwo\n"}, "w1"),
            _text("I created made.txt"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-o3")

    out = _spawn(mgr, parent.session_id)

    assert out["status"] == "completed"
    assert out["changed_files"] == [
        {"path": str(tmp_path / "made.txt"), "change": "created", "added": 2, "removed": 0}
    ]
    envelope = render_output(session_id="t", state="completed", text="r", changes=changes_of(out))
    assert "<changes>" in envelope and f"created {tmp_path / 'made.txt'} (+2)" in envelope


def test_a_child_that_changed_nothing_says_so(tmp_path):
    """The block that exposes "I fixed it" from a child that wrote nothing."""
    from harness.tools.task import changes_of

    mgr = _manager(tmp_path, ScriptedProvider([_text("all fixed")]))
    parent = _parent(mgr, "parent-o3-none")

    out = _spawn(mgr, parent.session_id)

    assert out["changed_files"] == []
    assert "This task changed no files." in changes_of(out)


def test_two_children_of_one_turn_changing_one_file_are_flagged(tmp_path):
    """Orchestration audit O4: children share the parent's folder, and the later write to a
    file silently replaced the earlier one."""
    provider = ScriptedProvider(
        [
            _tool_turn("write_file", {"path": "shared.txt", "content": "first\n"}, "w1"),
            _text("first done"),
            # The second child reads first, as the read-before-edit gate requires.
            _tool_turn("read_file", {"path": "shared.txt"}, "r2"),
            _tool_turn("write_file", {"path": "shared.txt", "content": "second\n"}, "w2"),
            _text("second done"),
        ]
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr, "parent-o4")
    mgr.get_engine(parent.session_id, agent="reeve", workspace=str(tmp_path))

    first = _spawn(mgr, parent.session_id, description="one")
    second = _spawn(mgr, parent.session_id, description="two")

    assert "overlaps" not in first
    assert second["overlaps"] == [
        {"path": str(tmp_path / "shared.txt"), "task_id": first["task_id"]}
    ]


def test_changed_files_fold_every_turn_of_the_child():
    """A structured-output correction runs a second turn; the first turn's edits count."""
    from harness.orchestration.child_changes import changed_files

    messages = [
        {"role": "notice", "kind": "changes", "changes": {
            "created": ["/w/a.py"], "stats": [{"path": "/w/a.py", "added": 3, "removed": 0}]}},
        {"role": "assistant", "content": "x"},
        {"role": "notice", "kind": "changes", "changes": {
            "overwritten": ["/w/a.py", "/w/b.py"],
            "stats": [{"path": "/w/a.py", "added": 1, "removed": 1},
                      {"path": "/w/b.py", "added": 2, "removed": 5}]}},
        {"role": "notice", "kind": "error", "text": "not a receipt"},
    ]
    assert changed_files(messages) == [
        {"path": "/w/a.py", "change": "created", "added": 4, "removed": 1},
        {"path": "/w/b.py", "change": "modified", "added": 2, "removed": 5},
    ]


def test_a_background_child_past_its_clock_is_stopped_and_reported(tmp_path, monkeypatch):
    """Orchestration audit O7: a background child had no clock on the manager path. Past
    it, the child is stopped and the parent is told the job failed — with what it found,
    not the silence a person's Stop gets."""
    monkeypatch.setattr(
        "harness.interface.server.manager.session_roots.BACKGROUND_TIMEOUT_MINUTES", 0.3 / 60
    )
    release = threading.Event()

    class _Stalls(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            self.calls.append({"model": model, "messages": messages})
            if len(self.calls) == 1:
                return AssistantTurn(
                    text="found 2 of 5 so far",
                    tool_calls=[ToolCall(id="l1", name="list_files", arguments={"path": "."})],
                    finish_reason="tool_calls",
                )
            release.wait(10)
            return _text("too late")

    mgr = _manager(tmp_path, _Stalls([]))
    parent = _parent(mgr, "parent-clock")
    # Stop cannot cut a blocking `complete()` short; the stalled call returns after the
    # 0.3 s clock has already fired, so the test does not wait out the full stall.
    threading.Timer(1.0, release.set).start()

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Survey the modules",
            prompt="survey",
            agent_name="general",
            background=True,
        )
        assert out["status"] == "running"
        await _wait_background(mgr)
        release.set()

    asyncio.run(main())
    injected = _injected(mgr, parent.session_id)
    assert len(injected) == 1
    assert 'state="error"' in injected[0]["content"]
    assert "timed out after" in injected[0]["content"]
    assert "found 2 of 5 so far" in injected[0]["content"]


def test_a_background_child_s_tokens_are_carried_to_the_parent(tmp_path, monkeypatch):
    """O7: a foreground child's tokens ride its tool result into the parent's budget; a
    background child's had no carrier at all."""
    usage = TokenUsage(input=700, output=50)
    mgr = _manager(tmp_path, ScriptedProvider([_text("the report", usage=usage)]))
    parent = _parent(mgr, "parent-carry")
    engine = mgr.get_engine(parent.session_id, agent="reeve", workspace=str(tmp_path))
    carried: list = []
    monkeypatch.setattr(engine, "carry_delegated", lambda run_id, tokens: carried.append((run_id, tokens)))

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="d",
            prompt="p",
            agent_name="general",
            background=True,
        )
        await _wait_background(mgr)
        return out["task_id"]

    child_id = asyncio.run(main())
    assert carried and carried[0][0] == child_id and carried[0][1] >= 750


def test_the_parent_stops_its_own_background_child_and_keeps_what_it_found(tmp_path):
    """`task(task_id=…, stop=true)`. The child is interrupted through the same tree
    the Stop button pulls; its words so far come back as the stop's answer, a queued follow-up
    never runs, and nothing is injected later. Another session's task is not this one's to stop."""
    release = threading.Event()

    class _WorksThenHangs(ScriptedProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            if not any(m.get("role") == "tool" for m in messages):
                return AssistantTurn(
                    text="So far: the cache lives in harness/cache.py.",
                    tool_calls=[ToolCall(id="c1", name="no_such_tool", arguments={})],
                    finish_reason="tool_calls",
                )
            release.wait(10)
            return _text("arrived after the stop")

    mgr = _manager(tmp_path, _WorksThenHangs([]))
    parent = _parent(mgr)
    other = _parent(mgr, "parent-2")

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id,
            description="Investigate the cache",
            prompt="find where the cache lives",
            agent_name="general",
            background=True,
        )
        child_id = out["task_id"]
        for _ in range(500):
            record = mgr.session_store.load(child_id)
            if any(m.get("role") == "tool" for m in (record.messages if record else [])):
                break
            await asyncio.sleep(0.01)
        refused = await asyncio.to_thread(
            mgr._spawn_subagent_sync, parent_id=other.session_id, task_id=child_id, stop=True
        )
        with mgr._subagent_lock:
            mgr._background_followups.setdefault(child_id, []).append(("more", "also check redis", None, None, None))
        stopped = await asyncio.to_thread(
            mgr._spawn_subagent_sync, parent_id=parent.session_id, task_id=child_id, stop=True
        )
        await _wait_background(mgr)
        release.set()
        await asyncio.sleep(0.05)
        again = await asyncio.to_thread(
            mgr._spawn_subagent_sync, parent_id=parent.session_id, task_id=child_id, stop=True
        )
        return child_id, refused, stopped, again

    child_id, refused, stopped, again = asyncio.run(main())

    assert "not a task this session started" in refused["error"]
    assert stopped["status"] == "stopped" and stopped["task_id"] == child_id
    assert 'state="stopped"' in stopped["report"]
    assert "the cache lives in harness/cache.py" in stopped["report"]
    assert not mgr.is_running(child_id)
    assert _injected(mgr, parent.session_id) == []
    child = mgr.session_store.load(child_id)
    assert "also check redis" not in json.dumps(child.messages)
    assert "not running in the background" in again["error"]
