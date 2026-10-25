"""Who may arm and change a goal.

Another design lets the model start a goal itself (`create_goal`: "even if the user did not say goal") and
fences that with an execution-time authority: create, edit, pause and resume need a direct human message in a
top-level agent's turn. Ours had the same tool action (`set`) and neither half. In the real store the model
called `set` 0 times — the description told it to wait for the user to ask "explicitly" — and of its 7
`edit` calls 3 were in turns no person opened, each rewriting the objective and two the acceptance checks.
What has to hold:

  - in a turn a person opened, the model's `set` arms the goal;
  - in a turn the harness opened (a goal round, a background result, a scheduled run, a self-wake) `set`,
    `edit`, `pause`, `resume` and `clear` are refused, while `done` and `blocked` keep their own gates;
  - a person who speaks inside such a turn brings the authority with them;
  - a subagent holds no `goal` tool (its copy was bound to the parent's goal).
"""

from __future__ import annotations

import asyncio

import pytest

from harness.compose import build_engine
from harness.modes.agents.reeve import reeve_agent
from harness.loop.goals import PERSON_ACTIONS, Goal, GoalStore, goal_tools
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.interface.server.manager import SessionManager
from harness.session.sessions import SessionRecord

REFUSAL = "needs a turn the user opened"


class _Scripted(ProviderClient):
    """Plays `turns` in order; `on_request(n)` runs before the n-th answer."""

    def __init__(self, turns, on_request=None):
        self._turns = list(turns)
        self.on_request = on_request
        self.requests: list[dict] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.requests.append({"tools": [t["function"]["name"] for t in (tools or [])]})
        if self.on_request is not None:
            self.on_request(len(self.requests))
        return self._turns.pop(0) if self._turns else AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _goal_call(call_id: str = "g1", **arguments) -> AssistantTurn:
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name="goal", arguments=arguments)], finish_reason="tool_calls"
    )


def _text(text: str = "ok") -> AssistantTurn:
    return AssistantTurn(text=text, finish_reason="stop")


def _store(tmp_path, monkeypatch, session_id: str) -> GoalStore:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    store = GoalStore.for_session(session_id)
    assert store is not None
    return store


def _engine(tmp_path, monkeypatch, provider, session_id: str = "goal-authority"):
    store = _store(tmp_path, monkeypatch, session_id)
    engine = build_engine(
        agent=reeve_agent(), workspace=tmp_path, provider=provider, goal_store=store, session_id=session_id
    )
    return engine, store


def _run(engine, text: str, **kwargs) -> None:
    async def main():
        async for _ in engine.run(text, **kwargs):
            pass

    asyncio.run(main())


def _goal_results(engine) -> list[str]:
    names = {
        call["id"]: (call.get("function") or {}).get("name")
        for m in engine.messages
        for call in m.get("tool_calls") or []
    }
    return [
        str(m.get("content"))
        for m in engine.messages
        if m.get("role") == "tool" and names.get(m.get("tool_call_id")) == "goal"
    ]


# -- a person's turn arms the goal -----------------------------------------------------


def test_the_model_arms_a_goal_in_a_turn_the_person_opened(tmp_path, monkeypatch):
    provider = _Scripted(
        [_goal_call(action="set", objective="make every test pass", checks=["pytest -q"]), _text()]
    )
    engine, store = _engine(tmp_path, monkeypatch, provider)

    _run(engine, "làm tới khi test xanh hết thì thôi")

    goal = store.load()
    assert goal is not None and goal.objective == "make every test pass"
    assert goal.checks == ["pytest -q"]
    assert store.activation() == "armed"
    assert REFUSAL not in _goal_results(engine)[0]
    # The person must learn that an autonomous loop started, and how to end it.
    assert "Tell the user" in _goal_results(engine)[0]


def test_the_description_invites_the_inference_and_states_the_fence(tmp_path, monkeypatch):
    engine, _ = _engine(tmp_path, monkeypatch, _Scripted([]))
    description = engine.registry.get("goal").schema["function"]["description"]

    assert "even without the word" in description
    assert "in any language" in description
    assert "Only a turn the user opened may set" in description
    assert "explicitly asks" not in description  # the wording under which `set` was called 0 times


# -- a turn the harness opened may not move the finish line ----------------------------


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "goal_round", "round": 1},
        {"synthetic": "background_task", "session_id": "child-1"},
        {"kind": "scheduled_run", "task_id": "t1"},
    ],
)
def test_a_turn_the_harness_opened_cannot_rewrite_the_goal(tmp_path, monkeypatch, source):
    provider = _Scripted(
        [_goal_call(action="edit", objective="an easier objective", checks=["true"]), _text()]
    )
    engine, store = _engine(tmp_path, monkeypatch, provider)
    store.save(Goal(objective="make every test pass", checks=["pytest -q"]))
    store.arm()

    _run(engine, "continue", source=source)

    goal = store.load()
    assert goal.objective == "make every test pass" and goal.checks == ["pytest -q"]
    assert REFUSAL in _goal_results(engine)[0]


def test_a_self_wake_is_the_sessions_own_turn(tmp_path, monkeypatch):
    """A wake carries no source of its own; its chain depth is what says nobody typed."""
    provider = _Scripted([_goal_call(action="set", objective="keep going"), _text()])
    engine, store = _engine(tmp_path, monkeypatch, provider)
    engine.wake_depth = 1

    _run(engine, "⏰ Wake — the timer you set has fired. Continue where you left off.")

    assert store.load() is None
    assert REFUSAL in _goal_results(engine)[0]


def test_a_person_speaking_inside_a_round_brings_the_authority(tmp_path, monkeypatch):
    """A steer counts as direct human input: the person is there, in this turn."""
    engine_box: dict = {}

    def on_request(n):
        if n == 1:
            engine_box["engine"].queue_steering("change the objective to: ship the report")

    provider = _Scripted(
        [_text("working"), _goal_call(action="edit", objective="ship the report"), _text()],
        on_request=on_request,
    )
    engine, store = _engine(tmp_path, monkeypatch, provider)
    engine_box["engine"] = engine
    store.save(Goal(objective="make every test pass"))
    store.arm()

    _run(engine, "continue", source={"kind": "goal_round", "round": 1})

    assert store.load().objective == "ship the report"
    assert REFUSAL not in _goal_results(engine)[0]


def test_only_the_persons_actions_are_fenced(tmp_path, monkeypatch):
    """`done` and `blocked` keep their own gates (proof, the round count); `status` reads."""
    store = _store(tmp_path, monkeypatch, "matrix")
    tool = goal_tools(store, person_turn_of=lambda: False)[0]
    store.save(Goal(objective="make every test pass"))
    store.arm()

    for action in sorted(PERSON_ACTIONS):
        assert REFUSAL in tool(action=action, objective="x").get("error", ""), action
    assert store.load().objective == "make every test pass"

    assert tool(action="status")["ok"] is True
    assert REFUSAL not in str(tool(action="blocked", reason="the API key was revoked"))
    store.save(Goal(objective="make every test pass"))
    assert REFUSAL not in str(tool(action="done", reason="finished"))


def test_a_caller_with_no_turn_to_ask_about_is_not_refused(tmp_path, monkeypatch):
    """A bare `goal_tools(store)` (no engine) behaves as before."""
    store = _store(tmp_path, monkeypatch, "bare")
    assert goal_tools(store)[0](action="set", objective="x")["ok"] is True


# -- the producers name themselves -----------------------------------------------------


async def test_a_scheduled_run_names_itself_in_the_transcript(tmp_path, monkeypatch):
    from harness.integrations.automation import Schedule, ScheduledTask

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = SessionManager(data_dir=tmp_path / "data", provider=_Scripted([_text("brief")]))
    task = ScheduledTask(
        title="Daily brief", instructions="Summarise the news.", schedule=Schedule(kind="cron", cron="0 9 * * *"),
        workspace=str(workspace), agent="reeve",
    )
    manager.task_store.save(task)

    run = await manager._run_scheduled_task(task, trigger="schedule")

    record = manager.session_store.load(run.session_id)
    opening = next(m for m in record.messages if m.get("role") == "user")
    assert (opening.get("source") or {}).get("kind") == "scheduled_run"
    engine = manager.get_engine(run.session_id, workspace=str(workspace), agent="reeve")
    assert engine.person_turn is False


# -- a subagent holds no goal tool -----------------------------------------------------


def test_a_subagent_is_not_offered_the_parents_goal_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    provider = _Scripted([_text("the final report")])
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    mgr.session_store.save(
        SessionRecord(
            session_id="parent-1", workspace=str(tmp_path), model="gpt-5.5", mode="interactive",
            messages=[], title="t", agent="reeve",
        )
    )
    parent_engine = mgr.get_engine("parent-1", agent="reeve", workspace=str(tmp_path))
    assert parent_engine.registry.get("goal") is not None, "the parent must hold the tool"

    out = asyncio.run(
        mgr.spawn_subagent(parent_id="parent-1", description="d", prompt="p", agent_name="general")
    )

    assert out["status"] == "completed", out
    assert "goal" not in provider.requests[-1]["tools"]


# -- the whole path: the person asks, the model arms, the harness continues ------------


class _ByTurn(ProviderClient):
    """Answers by what the request IS, not by its order: side calls (a title, a stop-check) share the client."""

    def complete(self, *, model, messages, tools=None, **settings):
        offered = {t["function"]["name"] for t in (tools or [])}
        if "goal" not in offered:
            return _text("a side call")
        opening = next(
            (str(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), ""
        )
        answered = messages[-1].get("role") == "tool"
        if "<goal_round>" in opening or "goal_round" in str(messages[-2:]):
            if answered:
                return _text("still working")
            return _goal_call("g2", action="edit", objective="an easier objective")
        if answered:
            return _text("I set a goal and will keep going; Stop or /goal pause ends it.")
        return _goal_call(action="set", objective="make every test pass", max_rounds=1)

    def capabilities(self, model):
        return ModelCapabilities()


async def test_a_request_the_model_turned_into_a_goal_gets_its_first_round_without_a_command(
    tmp_path, monkeypatch
):
    """What "self-activated" means end to end: nobody typed /goal. The person's turn ends with an armed
    goal, the idle edge opens round 1, and inside that round the model can no longer move the finish line."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    provider = _ByTurn()
    mgr = SessionManager(workspace=tmp_path, provider=provider)
    sid = "goal-self-armed"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    mgr._loop = asyncio.get_running_loop()

    await mgr.deliver_to_session(sid, "keep going until every test passes")

    for _ in range(100):
        goal = store.load()
        if goal is not None and goal.phase == "blocked":
            break
        await asyncio.sleep(0.05)
    goal = store.load()
    assert goal is not None and goal.rounds_started == 1, goal
    assert goal.objective == "make every test pass"
    rounds = [m for m in engine.messages if (m.get("source") or {}).get("kind") == "goal_round"]
    assert len(rounds) == 1
    results = _goal_results(engine)
    assert REFUSAL not in results[0] and REFUSAL in results[1]
