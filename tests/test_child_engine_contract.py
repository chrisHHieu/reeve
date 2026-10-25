"""What a child session is promised by the harness that runs it.

Found by measuring the real session store on 2026-10-04 (129 child sessions, 161 `task` calls) against
the subagent contract and the maker/checker rule.
Three places where the mechanism broke what the model-facing text says:

  - a FOREGROUND call was turned into a background one after 120 s. 29 of ~120 foreground calls came back
    `running`; the parent then touched the child's files in 11 and edited while a `verify` child was still
    checking in 9. A foreground call is waited for, to its own rail, and a child still working there is
    handed over (never abandoned) with a notice that says there is no result yet;
  - a denial-first child (`explore`, `verify`) lost every tool the parent had deferred: the ruleset removed
    the discovery bridge and kept the allowed tools, so `verify` was told to inspect the changes with
    `git_diff` and was never offered it (13 of 13; the git tools have since gone to the shell).
    A registry with no bridge defers nothing;
  - the todo nudge fired in children that hold no `todo_write` (36 of 36 child nudges). A nudge names a
    tool only when the engine holds it, and a child that does hold it plans on its own list.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import threading

import pytest

from harness.loop.nudges import TODO_NUDGE_AFTER, TODO_NUDGE_TEXT
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.interface.server.manager import SessionManager, session_roots
from harness.session.sessions import SessionRecord
from harness.tools import ToolRegistry
from harness.tools.contract import contract_of
from harness.tools.task import BACKGROUND_MOVED, BACKGROUND_STARTED
from harness.tools.task_agents import ALLOW, DEFAULTS, AgentInfo, evaluate, merge_rules, resolve_agents

SUBAGENT_MARK = "You are a subagent working on one task"


class _Recording(ProviderClient):
    """Answers every request with `reply(request_no, messages)` and keeps what each was offered."""

    def __init__(self, reply):
        self.reply = reply
        self.requests: list[dict] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append(
            {
                "tools": [t["function"]["name"] for t in (tools or [])],
                "messages": [dict(m) for m in messages],
                "model": model,
            }
        )
        return self.reply(len(self.requests), messages)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text: str) -> AssistantTurn:
    return AssistantTurn(text=text, finish_reason="stop")


def _repo(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    return workspace


def _parent(mgr: SessionManager, workspace, sid: str = "parent-1") -> SessionRecord:
    record = SessionRecord(
        session_id=sid,
        workspace=str(workspace),
        model="gpt-5.5",
        mode="interactive",
        messages=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
        title="t",
        agent="reeve",
    )
    mgr.session_store.save(record)
    # A live parent engine: the child's registry is a filtered copy of it.
    mgr.get_engine(sid, agent="reeve", workspace=str(workspace))
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


# -- a denial-first child is offered what its ruleset allows ---------------------------


@pytest.mark.parametrize("agent_name", ["verify", "explore"])
def test_a_denial_first_child_is_offered_every_tool_its_prompt_names(tmp_path, monkeypatch, agent_name):
    """With discovery engaged in the parent (the real sessions' state), the child's `*: deny` removes the
    bridge. The tools its ruleset allows must then be on the wire, or its own prompt points at nothing."""
    monkeypatch.setattr("harness.tools.discovery.DISCOVERY_MIN_CHARS", 0)
    provider = _Recording(lambda n, messages: _text("VERDICT: PASS"))
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    workspace = _repo(tmp_path)
    parent = _parent(mgr, workspace)
    parent_engine = mgr.get_engine(parent.session_id)
    assert parent_engine.registry.progressive_discovery, "the parent must be the deferring kind"
    assert parent_engine.registry.deferred_names(), "the parent defers built-ins"

    out = _spawn(mgr, parent.session_id, agent_name=agent_name)

    assert out["status"] == "completed", out
    offered = set(provider.requests[-1]["tools"])
    assert "tool_search" not in offered  # denial-first: no bridge to get a deferred tool back
    prompt = resolve_agents(None)[agent_name].prompt
    # Names no child may hold are not what a child's prompt means ("outside the task" is English).
    known = set(parent_engine.registry.names()) - session_roots._CHILD_NEVER_TOOLS
    named = {name for name in known if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", prompt)}
    assert named, "the prompt names tools; the test would be vacuous otherwise"
    assert named <= offered, f"{agent_name} is told to use {sorted(named - offered)} and is not offered them"
    # Every tool its ruleset allows is on the wire, deferred in the parent or not.
    rules = resolve_agents(None)[agent_name].permission
    allowed = {name for name in known if evaluate(name, "", rules) == "allow"}
    assert allowed and allowed <= offered, sorted(allowed - offered)


def test_deferral_still_holds_while_the_bridge_is_registered():
    """The control: the rule is about a MISSING bridge, not about deferral."""

    def tool_search(query: str = "") -> str:
        """Search tools."""
        return ""

    def cold(path: str = "") -> str:
        """A cold tool."""
        return ""

    def warm(path: str = "") -> str:
        """A warm tool."""
        return ""

    registry = ToolRegistry()
    registry.register_all([tool_search, cold, warm])
    registry.mark_deferred(["cold"])
    registry.set_progressive_discovery(True)
    assert registry.directly_callable() == {"tool_search", "warm"}

    registry.remove("tool_search")
    assert registry.directly_callable() == {"cold", "warm"}
    assert {s["function"]["name"] for s in registry.schemas()} == {"cold", "warm"}


# -- a nudge names a tool only when the engine holds it ---------------------------------


def _busy_then_done(n, messages):
    """One step of enough tool calls to pass the nudge threshold, then the report."""
    if n == 1:
        return AssistantTurn(
            tool_calls=[
                # Distinct arguments: identical calls are the repeat guard's business, not this test's.
                ToolCall(id=f"c{i}", name="grep", arguments={"pattern": f"needle{i}"})
                for i in range(TODO_NUDGE_AFTER)
            ],
            finish_reason="tool_calls",
        )
    return _text("the final report")


def _nudged(provider: _Recording) -> bool:
    return any(
        m.get("content") == TODO_NUDGE_TEXT for request in provider.requests for m in request["messages"]
    )


def test_a_child_with_no_todo_tool_is_never_nudged_toward_one(tmp_path):
    provider = _Recording(_busy_then_done)
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    parent = _parent(mgr, _repo(tmp_path))

    out = _spawn(mgr, parent.session_id, agent_name="general")

    assert out["status"] == "completed", out
    assert len(provider.requests) == 2
    assert "todo_write" not in provider.requests[0]["tools"]
    assert not _nudged(provider)


def test_a_child_that_holds_the_todo_tool_is_nudged_and_plans_on_its_own_list(tmp_path, monkeypatch):
    """The control for the test above (same path, the tool allowed), and the owner rule: the child's
    `todo_write` must not write the parent's list."""
    planner = AgentInfo(name="planner", permission=merge_rules(DEFAULTS, [("todo_write", "*", ALLOW)]))
    monkeypatch.setattr(session_roots, "resolve_agents", lambda config: {"planner": planner})

    def reply(n, messages):
        if n == 1:
            return _busy_then_done(1, messages)
        if n == 2:
            return AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="todo_write",
                        arguments={"todos": [{"content": "the child's own step", "status": "in_progress"}]},
                    )
                ],
                finish_reason="tool_calls",
            )
        return _text("the final report")

    provider = _Recording(reply)
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    parent = _parent(mgr, _repo(tmp_path))
    parent_engine = mgr.get_engine(parent.session_id)

    out = _spawn(mgr, parent.session_id, agent_name="planner")

    assert out["status"] == "completed", out
    assert "todo_write" in provider.requests[0]["tools"]
    assert _nudged(provider)
    child_engine = mgr.get_engine(out["task_id"])
    assert "the child's own step" in str(child_engine.todo.items)
    assert "the child's own step" not in str(parent_engine.todo.items)


# -- a foreground call is waited for ---------------------------------------------------


def test_the_hand_over_fires_before_the_engine_stops_waiting(tmp_path):
    """Two modules, one ordering: the manager hands a child over at `FOREGROUND_WAIT_SECONDS`, the engine
    stops waiting for the `task` call at its contract budget. If the budget came first the child would
    run on with nobody to receive its result — and a wait shorter than a real check (the old 120 s) turns
    the answer the caller asked for into a notice."""
    mgr = SessionManager(workspace=tmp_path, provider=_Recording(lambda n, m: _text("x")))
    parent = _parent(mgr, _repo(tmp_path))
    spec = mgr.get_engine(parent.session_id).registry.get("task")
    budget_ms = contract_of(spec.func).timeout_ms

    assert session_roots.FOREGROUND_WAIT_SECONDS * 1000 < budget_ms
    # 27 of the 29 real children that were backgrounded at 120 s finished within 15 minutes.
    assert session_roots.FOREGROUND_WAIT_SECONDS >= 900


def test_a_route_pinned_child_is_handed_over_like_any_other(tmp_path, monkeypatch):
    """It used to be exempt from the timer; at the rail an exemption only means an orphan."""
    monkeypatch.setattr(session_roots, "FOREGROUND_WAIT_SECONDS", 0.1)
    release = threading.Event()

    def reply(n, messages):
        release.wait(10)
        return _text("the final report")

    mgr = SessionManager(workspace=tmp_path, provider=_Recording(reply))
    parent = _parent(mgr, _repo(tmp_path))

    async def main():
        result = await asyncio.wait_for(
            mgr.spawn_subagent(
                parent_id=parent.session_id,
                description="Investigate the cache",
                prompt="find where the cache lives",
                agent_name="general",
                model="gpt-5.5-mini",
            ),
            30,
        )
        release.set()
        for _ in range(500):
            if not mgr._background_tasks:
                break
            await asyncio.sleep(0.01)
        return result

    result = asyncio.run(main())

    assert result["status"] == "running", result
    assert BACKGROUND_MOVED in result["report"]
    assert BACKGROUND_STARTED not in result["report"]


def test_the_moved_notice_says_there_is_no_result_and_not_to_redo_the_work():
    """The text a caller reads when it asked to wait and has to go on without the answer."""
    assert "NOT in this message" in BACKGROUND_MOVED
    assert "redo its work" in BACKGROUND_MOVED
    assert "end your response" in BACKGROUND_MOVED
    assert BACKGROUND_MOVED != BACKGROUND_STARTED


# -- a tool bound to one session stays with it --------------------------------


def test_a_child_holds_no_tool_bound_to_its_parents_session(tmp_path):
    """The child's registry is a copy of the parent's. A tool whose closure holds the PARENT's session —
    its goal, its wakes, its schedule origin, its history — would be the child acting on the parent."""
    provider = _Recording(lambda n, messages: _text("the final report"))
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    parent = _parent(mgr, _repo(tmp_path))
    parent_registry = mgr.get_engine(parent.session_id).registry
    bound = {name for name in parent_registry.names() if parent_registry.get(name).session_bound}
    assert {"goal", "sleep_until", "wake_on", "search_history", "create_scheduled_task"} <= bound, bound

    out = _spawn(mgr, parent.session_id, agent_name="general")

    assert out["status"] == "completed", out
    child_registry = mgr.get_engine(out["task_id"]).registry
    for name in bound:
        assert child_registry.get(name) is not parent_registry.get(name), name
    assert not {"goal", "sleep_until", "wake_on", "create_scheduled_task"} & set(child_registry.names())
    # History is the one a child is entitled to — its own.
    assert child_registry.get("search_history") is not None


def test_a_registry_copy_can_leave_the_session_bound_tools_behind():
    def mine(x: str = "") -> str:
        """Bound by argument."""
        return x

    def tagged(x: str = "") -> str:
        """Bound by the builder's own mark."""
        return x

    def shared(x: str = "") -> str:
        """Anyone's."""
        return x

    tagged.__tool_session_bound__ = True
    registry = ToolRegistry()
    registry.register(mine, session_bound=True)
    registry.register_all([tagged, shared])

    assert set(registry.filtered().names()) == {"mine", "tagged", "shared"}
    assert set(registry.filtered(session_bound=False).names()) == {"shared"}


def test_a_child_that_may_not_delegate_keeps_none_of_tasks_companions(tmp_path):
    """`workflow` and `list_subagent_models` are offered only beside `task` in a root session."""
    provider = _Recording(lambda n, messages: _text("the final report"))
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    parent = _parent(mgr, _repo(tmp_path))
    parent_names = set(mgr.get_engine(parent.session_id).registry.names())
    assert {"task", "workflow", "list_subagent_models"} <= parent_names

    out = _spawn(mgr, parent.session_id, agent_name="general")

    child_names = set(mgr.get_engine(out["task_id"]).registry.names())
    assert not {"task", "workflow", "list_subagent_models"} & child_names
