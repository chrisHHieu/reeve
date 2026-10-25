"""Stage-1 workflow foundation: per-session child caps + structured child output.

The cap closes the managed path's unbounded fan-out (`subagent_max_parallel`), and
`output_schema` makes a `task` child's answer a validated JSON object: the child COMMITS
it through the `capture_result` tool (a rejected object is a retryable tool error inside
its own turn), the last-text parse + one corrective retry remain the fallback, and a
child that settles without either fails the delegation.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class ScriptedProvider(ProviderClient):
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return self.turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, monkeypatch, turns) -> tuple[SessionManager, ScriptedProvider]:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    provider = ScriptedProvider(turns)
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    parent = SessionRecord(
        session_id="parent-1",
        workspace=str(mgr.default_workspace or tmp_path),
        model="gpt-5.6",
        mode="interactive",
        messages=[{"role": "user", "content": "hi"}],
        title="p",
        agent="reeve",
    )
    mgr.session_store.save(parent)
    return mgr, provider


@pytest.fixture(autouse=True)
def _a_short_wait_for_a_slot(monkeypatch):
    """The real limit is the call's rail (14 minutes): no test should be able to sit in it."""
    from harness.interface.server.manager import session_roots

    monkeypatch.setattr(session_roots, "SLOT_WAIT_SECONDS", 5.0)


#: A program's call (a workflow's `agent()`): it needs the child's answer as its return value, so
#: past the cap it still waits for a slot instead of being queued.
PROGRAM_CALL = "call-1:code:1"


def _spawn(mgr, description="one more", **kw):
    return mgr.spawn_subagent(
        parent_id="parent-1", description=description, prompt="p", agent_name="general", **kw
    )


def test_a_background_call_past_the_cap_is_refused_before_a_child_exists(tmp_path, monkeypatch):
    mgr, _ = _manager(tmp_path, monkeypatch, [])
    mgr._live_children["parent-1"] = {"a", "b", "c", "d"}  # default cap is 4

    started = time.monotonic()
    out = asyncio.run(_spawn(mgr, background=True))
    assert time.monotonic() - started < 2.0  # refused at once: its caller is not waiting
    assert "subagent_max_parallel" in out["error"] and "4" in out["error"]
    # Its caller chose not to wait, and is told what waiting would take.
    assert "leave `background` off" in out["error"]
    # Nothing was created and the slots are untouched.
    assert mgr._live_children["parent-1"] == {"a", "b", "c", "d"}


def test_a_program_call_that_needs_its_answer_waits_for_a_slot(tmp_path, monkeypatch):
    """15 calls of one fan-out of 19 were refused with "wait for one to finish"
    and nothing to wait on. A call that needs the child's answer as its return value (a
    program's, a subagent's own) waits for a slot."""
    mgr, _ = _manager(tmp_path, monkeypatch, [AssistantTurn(text="done", finish_reason="stop")])
    mgr._live_children["parent-1"] = {"a", "b", "c", "d"}

    async def run():
        spawn = asyncio.ensure_future(_spawn(mgr, origin=PROGRAM_CALL))
        await asyncio.sleep(0.15)
        assert not spawn.done()  # waiting, not refused
        assert len(mgr._live_children["parent-1"]) == 4  # and holding nothing yet
        mgr._release_child_slot("parent-1", "a")
        return await asyncio.wait_for(spawn, 20)

    out = asyncio.run(run())
    assert out["status"] == "completed"
    assert mgr._live_children["parent-1"] == {"b", "c", "d"}  # it gave its own slot back


def test_a_call_past_the_cap_is_queued_at_once_and_starts_when_a_slot_frees(tmp_path, monkeypatch):
    """Seven calls, four slots: the four children ran past the call's rail and went to the
    background still holding their slots, and the three calls waiting for one failed after
    fourteen minutes with nothing written (owner, 2026-10-21). A call past the cap is queued
    now: it returns at once, says so, and its child starts in the background when a slot
    frees, its result injected into the parent like any background child's."""
    from harness.interface.server.manager.background_status import background_context

    mgr, _ = _manager(tmp_path, monkeypatch, [AssistantTurn(text="the DEPLOYMENT doc", finish_reason="stop")])
    mgr._live_children["parent-1"] = {"a", "b", "c", "d"}
    frames: list[dict] = []
    delivered: list[tuple[str, str, dict]] = []

    async def deliver(session_id, text, source=None, **_kw):
        delivered.append((session_id, text, source or {}))

    monkeypatch.setattr(mgr, "deliver_to_session", deliver)

    async def run():
        started = time.monotonic()
        out = await _spawn(mgr, description="Rewrite DEPLOYMENT doc", on_metadata=frames.append)
        assert time.monotonic() - started < 1.0  # returned, not waiting
        assert out["status"] == "queued" and out["task_id"] == ""
        assert "queued" in out["report"] and "at most 4 at once" in out["report"]
        # The row is told it is in line, and that it outlives the call (a background row).
        assert frames == [{"waiting": {"running": 4, "limit": 4}, "background": True}]
        # Each turn tells the model what is still queued, so it does not start the work again.
        assert "Rewrite DEPLOYMENT doc" in background_context(mgr, "parent-1")
        assert len(mgr._live_children["parent-1"]) == 4  # holding nothing while it waits

        mgr._release_child_slot("parent-1", "a")
        for _ in range(400):
            if delivered:
                break
            await asyncio.sleep(0.05)

    asyncio.run(run())
    created = [frame for frame in frames[1:] if frame.get("session_id")]
    assert created and created[0]["background"] is True  # it ran as a background child
    (parent, envelope, source), = delivered
    assert parent == "parent-1" and source.get("synthetic") == "background_task"
    assert "the DEPLOYMENT doc" in envelope and "Background task completed: Rewrite DEPLOYMENT doc" in envelope
    assert mgr._queued_spawns.get("parent-1") == []  # out of line
    assert mgr._live_children["parent-1"] == {"b", "c", "d"}  # and its slot given back
    assert "Rewrite DEPLOYMENT doc" not in background_context(mgr, "parent-1")


def test_stop_takes_a_queued_call_out_of_line_and_nothing_starts(tmp_path, monkeypatch):
    mgr, provider = _manager(tmp_path, monkeypatch, [AssistantTurn(text="never", finish_reason="stop")])
    mgr._live_children["parent-1"] = {"a", "b", "c", "d"}
    delivered: list = []

    async def deliver(*args, **kwargs):
        delivered.append(args)

    monkeypatch.setattr(mgr, "deliver_to_session", deliver)

    async def run():
        out = await _spawn(mgr)
        assert out["status"] == "queued"
        mgr.cancel_session("parent-1")
        await asyncio.sleep(0.05)
        mgr._release_child_slot("parent-1", "a")  # a slot frees after the Stop
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert mgr._queued_spawns.get("parent-1") == []
    assert provider.calls == 0 and delivered == []
    assert mgr._live_children["parent-1"] == {"b", "c", "d"}  # its reservation was never taken


def test_a_call_with_a_free_slot_is_never_told_it_waited(tmp_path, monkeypatch):
    mgr, _ = _manager(tmp_path, monkeypatch, [AssistantTurn(text="done", finish_reason="stop")])
    frames: list[dict] = []
    out = asyncio.run(asyncio.wait_for(_spawn(mgr, on_metadata=frames.append), 20))
    assert out["status"] == "completed"
    assert not any("waiting" in frame for frame in frames)


def test_one_freed_slot_starts_one_waiting_call_and_the_cap_holds(tmp_path, monkeypatch):
    turns = [AssistantTurn(text="done", finish_reason="stop") for _ in range(3)]
    mgr, provider = _manager(tmp_path, monkeypatch, turns)
    mgr._live_children["parent-1"] = {"a", "b", "c", "d"}
    seen = []
    complete = provider.complete

    def watched(**kw):
        seen.append(len(mgr._live_children.get("parent-1", ())))
        return complete(**kw)

    provider.complete = watched
    # With a client listening, announcing the new child really suspends the spawn, between
    # the moment it got its slot and the moment the child holds it. That gap is where a
    # second waiting call would take the same slot.
    announce = mgr._announce_session_event

    async def announced(event):
        await asyncio.sleep(0.05)
        return await announce(event)

    monkeypatch.setattr(mgr, "_announce_session_event", announced)

    async def run():
        spawns = [asyncio.ensure_future(_spawn(mgr, description=f"w{i}", origin=PROGRAM_CALL)) for i in range(3)]
        await asyncio.sleep(0.15)
        mgr._release_child_slot("parent-1", "a")
        return await asyncio.wait_for(asyncio.gather(*spawns), 30)

    outs = asyncio.run(run())
    assert [o["status"] for o in outs] == ["completed"] * 3
    # Three children ran, one at a time through the one free slot: never five at once.
    assert len(seen) == 3 and max(seen) == 4
    assert mgr._live_children["parent-1"] == {"b", "c", "d"}


def test_a_wait_that_outlasts_its_limit_ends_with_the_reason_and_holds_nothing(tmp_path, monkeypatch):
    from harness.interface.server.manager import session_roots

    monkeypatch.setattr(session_roots, "SLOT_WAIT_SECONDS", 0.2)
    mgr, _ = _manager(tmp_path, monkeypatch, [])
    mgr._live_children["parent-1"] = {"a", "b", "c", "d"}

    async def run():
        return await asyncio.wait_for(_spawn(mgr, origin=PROGRAM_CALL), 4)

    out = asyncio.run(run())
    assert "subagent_max_parallel" in out["error"] and "none finished in" in out["error"]
    assert "background" not in out["error"]
    assert mgr._live_children["parent-1"] == {"a", "b", "c", "d"}


def test_a_spawn_refused_after_its_slot_was_reserved_gives_it_back(tmp_path, monkeypatch):
    mgr, _ = _manager(tmp_path, monkeypatch, [])
    mgr._live_children["parent-1"] = {"a", "b", "c"}
    monkeypatch.setattr(mgr, "try_mark_running", lambda session_id: False)

    out = asyncio.run(_spawn(mgr))
    assert out["status"] == "error: this task is already running"
    assert mgr._live_children["parent-1"] == {"a", "b", "c"}


def test_a_follow_up_into_a_child_that_holds_a_slot_does_not_queue_for_another(tmp_path, monkeypatch):
    from harness.interface.server.manager import session_roots

    monkeypatch.setattr(session_roots, "SLOT_WAIT_SECONDS", 0.2)
    turns = [AssistantTurn(text="first", finish_reason="stop"), AssistantTurn(text="second", finish_reason="stop")]
    mgr, _ = _manager(tmp_path, monkeypatch, turns)
    first = asyncio.run(_spawn(mgr))
    assert first["status"] == "completed"
    # The cap is full, and this child is one of the four.
    mgr._live_children["parent-1"] = {"a", "b", "c", first["task_id"]}

    again = asyncio.run(_spawn(mgr, task_id=first["task_id"]))
    assert again.get("status") == "completed" and again["report"] == "second"
    assert mgr._live_children["parent-1"] == {"a", "b", "c"}


def test_a_settled_child_returns_its_slot(tmp_path, monkeypatch):
    mgr, _ = _manager(tmp_path, monkeypatch, [AssistantTurn(text="done", finish_reason="stop")])

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="slot check",
            prompt="p",
            agent_name="general",
        )
    )
    assert out["status"] == "completed"
    assert mgr._live_children == {}


def test_a_valid_structured_answer_becomes_the_report(tmp_path, monkeypatch):
    mgr, provider = _manager(
        tmp_path,
        monkeypatch,
        [
            AssistantTurn(text="sorry, prose", finish_reason="stop"),
            AssistantTurn(text='{"answer": 1}', finish_reason="stop"),
        ],
    )

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="structured",
            prompt="answer the thing",
            agent_name="general",
            output_schema=SCHEMA,
        )
    )
    assert out["status"] == "completed"
    assert out["report"] == '{"answer": 1}'
    assert provider.calls == 2, "the invalid first answer gets exactly one retry"
    assert mgr._live_children == {}


def test_an_invalid_answer_fails_after_the_retry(tmp_path, monkeypatch):
    mgr, provider = _manager(
        tmp_path,
        monkeypatch,
        [
            AssistantTurn(text="still prose", finish_reason="stop"),
            AssistantTurn(text="not json either", finish_reason="stop"),
        ],
    )

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="structured",
            prompt="answer the thing",
            agent_name="general",
            output_schema=SCHEMA,
        )
    )
    assert out["status"].startswith("error: structured output invalid after a retry")
    assert provider.calls == 2
    assert mgr._live_children == {}


def test_a_committed_capture_is_the_report(tmp_path, monkeypatch):
    """The child commits through the tool: the report is the captured object even though
    the child's last message is only a confirmation sentence."""
    mgr, provider = _manager(
        tmp_path,
        monkeypatch,
        [
            AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="capture_result",
                        arguments={"value": {"answer": 7}},
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantTurn(text="Committed.", finish_reason="stop"),
        ],
    )

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="capture",
            prompt="answer the thing",
            agent_name="general",
            output_schema=SCHEMA,
        )
    )
    assert out["status"] == "completed"
    assert out["report"] == '{"answer": 7}'
    assert provider.calls == 2, "one capture, one closing sentence — no corrective retry"


def test_a_rejected_capture_is_retried_inside_the_same_turn(tmp_path, monkeypatch):
    """A wrong object is a tool error the child can fix immediately; the host never has
    to spend a corrective prompt on it."""
    mgr, provider = _manager(
        tmp_path,
        monkeypatch,
        [
            AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="capture_result",
                        arguments={"value": {"answer": "seven"}},
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="c2",
                        name="capture_result",
                        arguments={"value": {"answer": 7}},
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantTurn(text="Fixed.", finish_reason="stop"),
        ],
    )

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="capture retry",
            prompt="answer the thing",
            agent_name="general",
            output_schema=SCHEMA,
        )
    )
    assert out["status"] == "completed"
    assert out["report"] == '{"answer": 7}'
    assert provider.calls == 3


def test_a_capture_committed_on_the_corrective_retry_is_used(tmp_path, monkeypatch):
    """The fallback retry can also end in a commit: the capture wins over the text."""
    mgr, provider = _manager(
        tmp_path,
        monkeypatch,
        [
            AssistantTurn(text="still prose", finish_reason="stop"),
            AssistantTurn(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="capture_result",
                        arguments={"value": {"answer": 2}},
                    )
                ],
                finish_reason="tool_calls",
            ),
            AssistantTurn(text="Committed.", finish_reason="stop"),
        ],
    )

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="late capture",
            prompt="answer the thing",
            agent_name="general",
            output_schema=SCHEMA,
        )
    )
    assert out["status"] == "completed"
    assert out["report"] == '{"answer": 2}'
    assert provider.calls == 3


def test_a_bad_schema_is_refused_at_the_manager_too(tmp_path, monkeypatch):
    mgr, _ = _manager(tmp_path, monkeypatch, [])
    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="bad schema",
            prompt="p",
            agent_name="general",
            output_schema={"type": "array", "items": {"type": "string"}},
        )
    )
    assert "object-rooted" in out["error"]


def test_a_dead_run_stops_only_its_own_children(tmp_path, monkeypatch):
    mgr, _ = _manager(tmp_path, monkeypatch, [])
    recorded: list[str] = []

    def _cancel(sid: str, out: list[str]) -> None:
        # `out` is the caller's own list; `recorded` is this test's witness that the
        # only child stopped was the one belonging to the call under test.
        out.append(sid)
        recorded.append(sid)

    monkeypatch.setattr(mgr, "_cancel_session_tree", _cancel)
    mgr._child_call_origins["child-a"] = "w1"
    mgr._child_call_origins["child-b"] = "w2"

    assert mgr.cancel_call_children("w1") == ["child-a"]
    assert recorded == ["child-a"], "only the called run's child was stopped"
    assert mgr.cancel_call_children("w9") == []
    assert recorded == ["child-a"]


def test_external_cli_children_refuse_a_schema(tmp_path, monkeypatch):
    from harness.orchestration import subagent_external as ext
    from harness.tools import task_agents

    mgr, _ = _manager(tmp_path, monkeypatch, [])
    monkeypatch.setattr(
        task_agents,
        "external_agent_infos",
        lambda: {
            "codex": task_agents.AgentInfo(
                name="codex",
                description="external codex",
                mode=task_agents.MODE_SUBAGENT,
                provider="codex",
                native=True,
            )
        },
    )
    monkeypatch.setattr(ext, "preflight", lambda provider: None)

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id="parent-1",
            description="external structured",
            prompt="p",
            agent_name="codex",
            output_schema=SCHEMA,
        )
    )
    assert "not supported for external" in out["error"]


def test_the_model_is_told_how_many_subagents_run_at_once_and_what_happens_past_that(tmp_path):
    """The cap lived only in a refusal, so a model asked for seven at once and learned the
    limit from three failures (owner, 2026-10-21). The tool's description now states it, as
    the session's config sets it, and says what a call past it gets."""
    from harness.tools.task import task_tools

    def described(**kwargs) -> str:
        tools = task_tools(workspace=tmp_path, provider=ScriptedProvider([]), model="gpt-5.6", **kwargs)
        task = next(tool for tool in tools if tool.__tool_schema__["function"]["name"] == "task")
        return task.__tool_schema__["function"]["description"]

    queued = described(config={"subagent_max_parallel": 6}, spawner=lambda **_kw: {})
    assert "at most 6 subagents at once" in queued and "queued" in queued
    # Without a manager (the CLI's thread path) a call past the cap is refused, and says so.
    refused = described(config={"subagent_max_parallel": 3})
    assert "at most 3 subagents at once; a call past that is refused" in refused
    # No cap, nothing claimed.
    assert "subagents at once" not in described(config={"subagent_max_parallel": 0})


def test_the_task_tool_hands_the_model_the_queued_answer_not_a_finished_one(tmp_path):
    """A queued call has not started: read as a finished result, its envelope would tell the
    model the work was done."""
    from harness.tools.task import task_tools

    queued = {"task_id": "", "status": "queued", "report": '<task id="" state="queued">in line</task>'}
    tools = task_tools(
        workspace=tmp_path, provider=ScriptedProvider([]), model="gpt-5.6",
        session_id="parent-1", spawner=lambda **_kw: queued,
    )
    task = next(tool for tool in tools if tool.__tool_schema__["function"]["name"] == "task")
    out = task(description="Rewrite docs", prompt="p", subagent_type="general")
    assert out == {"task_id": "", "status": "queued", "report": queued["report"]}
