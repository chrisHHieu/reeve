"""A parent talks to a subagent it started.

What must hold: a message reaches the child wherever the child is — into the turn it is in, read
after the step it is on; into its background job's next turn; or, once it has finished, as a resume
whose answer comes back — and none is answered "sent" and then dropped. The case that was dropped
(session 1967dcdb-7d2): the parent messaged a background child from the very turn the child's result
had opened. A child is also called by the name its `task` call gave it, and a new one can be given a
thinking level its model offers.

No network: the provider is scripted, and a parent turn is stood in for where one would run.
"""

from __future__ import annotations

import asyncio
import threading

from harness.interface.server.manager import SessionManager
from harness.interface.server.manager.background_status import background_context
from harness.interface.server.manager.subagent_messages import PARENT_MESSAGE, named_children
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.session.sessions import SessionRecord
from harness.tools.task_message import DELIVERED


class ScriptedProvider(ProviderClient):
    """Answers in order. `hold` makes the first call wait until released, so a test can act
    while the child is inside its turn."""

    def __init__(self, turns, hold: bool = False):
        self._turns = list(turns)
        self.calls: list[list[dict]] = []
        self.inside = threading.Event()
        self.release = threading.Event()
        self._hold = hold

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append([dict(m) for m in messages])
        if self._hold and len(self.calls) == 1:
            self.inside.set()
            self.release.wait(10)
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _text(text: str) -> AssistantTurn:
    return AssistantTurn(text=text, finish_reason="stop")


def _tool_turn(name: str, args: dict) -> AssistantTurn:
    return AssistantTurn(tool_calls=[ToolCall(id="c1", name=name, arguments=args)], finish_reason="tool_calls")


def _manager(tmp_path, provider) -> SessionManager:
    return SessionManager(workspace=tmp_path, provider=provider)


def _parent(mgr: SessionManager, sid: str = "parent-1") -> SessionRecord:
    record = SessionRecord(
        session_id=sid,
        workspace=str(mgr.default_workspace or "."),
        model="gpt-5.5",
        mode="interactive",
        messages=[{"role": "user", "content": "the parent's question"}],
        title="a conversation",
        agent="reeve",
    )
    mgr.session_store.save(record)
    return record


async def _wait_jobs(mgr: SessionManager, timeout: float = 15.0) -> None:
    """Until every background job has ended and delivered its result (its thread is gone)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    threads = [t for t in mgr._background_tasks.values() if isinstance(t, threading.Thread)]
    while (mgr._background_tasks or any(t.is_alive() for t in threads)) and loop.time() < deadline:
        await asyncio.sleep(0.01)


def _contents(mgr: SessionManager, session_id: str) -> list:
    return [m.get("content") for m in mgr.session_store.load(session_id).messages]


def test_a_message_sent_from_the_turn_its_result_opened_resumes_the_child(tmp_path):
    """The reported case. Delivering a background child's result opens the parent's next turn
    and waits for it; the child used to stay registered as running until that turn was over,
    so a message sent in it was queued, answered "sent", and the queue was then cleared."""
    provider = ScriptedProvider([_text("first report"), _text("second report")])
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)
    answers: list[dict] = []

    async def parent_turn(session_id, message, **kwargs):
        # Stands in for the parent's turn the injection opens: its model messages the child.
        child_id = kwargs["source"]["session_id"]
        answers.append(
            await mgr.spawn_subagent(
                parent_id=session_id, description="", prompt="now check the tests too",
                agent_name="", task_id=child_id,
            )
        )
        return "completed"

    mgr.deliver_to_session = parent_turn  # type: ignore[method-assign]

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id, description="Survey the repo", prompt="survey it",
            agent_name="general", background=True,
        )
        await _wait_jobs(mgr)
        return out["task_id"]

    child_id = asyncio.run(main())
    # Not queued into a job that was over: the child was resumed, and its answer came back.
    assert len(answers) == 1
    assert answers[0]["status"] == "completed", answers[0]
    assert answers[0]["report"] == "second report"
    assert _contents(mgr, child_id).count("now check the tests too") == 1
    assert mgr._background_followups == {}


def test_a_message_joins_the_turn_the_child_is_in(tmp_path):
    """While the child works, the message is read after the step it is on — in the same turn,
    not a turn later — and the job still reports once."""
    provider = ScriptedProvider(
        [_tool_turn("list_files", {"path": "."}), _text("done, cache included")], hold=True
    )
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)
    delivered: list[dict] = []
    reports: list[str] = []

    async def parent_turn(session_id, message, **kwargs):
        reports.append(message)
        return "completed"

    mgr.deliver_to_session = parent_turn  # type: ignore[method-assign]

    async def main():
        out = await mgr.spawn_subagent(
            parent_id=parent.session_id, description="Survey the repo", prompt="survey it",
            agent_name="general", background=True,
        )
        await asyncio.to_thread(provider.inside.wait, 10)
        delivered.append(
            await mgr.spawn_subagent(
                parent_id=parent.session_id, description="", prompt="include the cache module",
                agent_name="", task_id=out["task_id"],
            )
        )
        provider.release.set()
        await _wait_jobs(mgr)
        return out["task_id"]

    child_id = asyncio.run(main())
    assert delivered[0]["status"] == "running" and DELIVERED in delivered[0]["report"]
    # The second request of the SAME turn carries it.
    assert any(m.get("content") == "include the cache module" for m in provider.calls[1])
    steered = [m for m in mgr.session_store.load(child_id).messages if m.get("content") == "include the cache module"]
    assert len(steered) == 1 and steered[0].get("_steer") is True
    assert steered[0]["source"]["synthetic"] == PARENT_MESSAGE
    assert len(reports) == 1 and "done, cache included" in reports[0]


def test_a_message_the_turn_missed_opens_a_turn_of_its_own(tmp_path):
    """A message queued after the turn's last read is still in the engine when the turn ends:
    it is taken there and run, never left behind. A stopped turn takes nothing on."""
    mgr = _manager(tmp_path, ScriptedProvider([]))
    driven: list[tuple] = []

    class Outcome:
        def __init__(self, status="completed", error=""):
            self.status, self.error = status, error

    class Engine:
        def __init__(self, pending):
            self.pending = pending

        def take_steering(self):
            pending, self.pending = self.pending, []
            return pending

    async def drive(child_id, engine, message, **kwargs):
        driven.append((message, kwargs.get("source")))
        return Outcome()

    mgr._drive_turn = drive  # type: ignore[method-assign]
    mgr._live_child_turns.add("c1")
    late = [("check the cache", {"synthetic": PARENT_MESSAGE}, None), ("and the tests", None, None)]
    asyncio.run(mgr._read_late_messages("c1", Engine(late), Outcome(), "p1", lambda *a: None))
    assert driven == [("check the cache\n\nand the tests", {"synthetic": PARENT_MESSAGE, "session_id": "p1"})]
    assert "c1" not in mgr._live_child_turns

    driven.clear()
    mgr._live_child_turns.add("c1")
    asyncio.run(
        mgr._read_late_messages("c1", Engine(list(late)), Outcome("interrupted"), "p1", lambda *a: None)
    )
    assert driven == [] and "c1" not in mgr._live_child_turns


def test_a_child_is_called_by_its_name_and_only_by_its_own_parent(tmp_path):
    provider = ScriptedProvider([_text("surveyed"), _text("and the cache too")])
    mgr = _manager(tmp_path, provider)
    parent = _parent(mgr)
    other = _parent(mgr, "parent-2")

    first = asyncio.run(
        mgr.spawn_subagent(
            parent_id=parent.session_id, description="Survey", prompt="survey it",
            agent_name="general", name="survey",
        )
    )
    assert first["status"] == "completed"
    again = asyncio.run(
        mgr.spawn_subagent(
            parent_id=parent.session_id, description="Survey", prompt="again",
            agent_name="general", name="survey",
        )
    )
    assert "already has a subagent called" in again["error"]

    out = mgr._spawn_subagent_sync(
        parent_id=parent.session_id, to="survey", description="", prompt="look at the cache",
        agent_name="",
    )
    assert out["task_id"] == first["task_id"] and out["report"] == "and the cache too"
    refused = mgr._spawn_subagent_sync(
        parent_id=other.session_id, to=first["task_id"], description="", prompt="hi", agent_name="",
    )
    assert "not a subagent this session started" in refused["error"]
    assert "no subagent of this session" in mgr._spawn_subagent_sync(
        parent_id=parent.session_id, to="nobody", description="", prompt="hi", agent_name="",
    )["error"]


def test_a_name_is_read_back_from_the_parents_transcript():
    """After a restart the in-memory names are gone; the parent's own record still joins each
    named `task` call to the id its result gave."""
    messages = [
        {"role": "assistant", "tool_calls": [
            {"id": "a", "function": {"name": "task", "arguments": '{"name": "survey", "prompt": "p"}'}},
            {"id": "b", "function": {"name": "task", "arguments": {"name": "tests"}}},
            {"id": "c", "function": {"name": "task", "arguments": '{"prompt": "unnamed"}'}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": '<task id="abc123" state="running">'},
        {"role": "tool", "tool_call_id": "b", "content": "Subagent failed (task_id: def456): boom"},
        {"role": "tool", "tool_call_id": "c", "content": '<task id="ghi789" state="completed">'},
    ]
    assert named_children(messages) == {"survey": "abc123", "tests": "def456"}


def test_effort_is_a_level_the_model_offers_and_the_child_thinks_at_it(tmp_path, monkeypatch):
    from harness.llm.providers import variants

    monkeypatch.setattr(variants, "levels_for", lambda model, facts=None: ["low", "medium", "high"])
    mgr = _manager(tmp_path, ScriptedProvider([_text("reviewed")]))
    parent = _parent(mgr)

    refused = asyncio.run(
        mgr.spawn_subagent(
            parent_id=parent.session_id, description="Review", prompt="review",
            agent_name="general", effort="max",
        )
    )
    assert "low, medium, high" in refused["error"]

    out = asyncio.run(
        mgr.spawn_subagent(
            parent_id=parent.session_id, description="Review", prompt="review",
            agent_name="general", effort="high",
        )
    )
    assert out["status"] == "completed"
    assert mgr.session_store.load(out["task_id"]).thinking == "high"
    assert mgr._engines[out["task_id"]].thinking_level == "high"

    monkeypatch.setattr(variants, "levels_for", lambda model, facts=None: [])
    none = asyncio.run(
        mgr.spawn_subagent(
            parent_id=parent.session_id, description="Review", prompt="review",
            agent_name="general", effort="high",
        )
    )
    assert "offers no thinking levels" in none["error"]


def test_the_still_running_line_names_the_child_and_says_how_to_reach_it(tmp_path):
    mgr = _manager(tmp_path, ScriptedProvider([]))
    mgr._background_parents["abc123"] = "p1"
    mgr._background_labels["abc123"] = "Survey the repo"
    mgr._child_names["p1"] = {"survey": "abc123"}
    line = background_context(mgr, "p1")
    assert "- abc123 (survey) — Survey the repo" in line
    assert "message_task" in line
