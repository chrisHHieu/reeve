"""The turn loop is the order of its phases.

`TurnEngine._loop_body` used to be one 649-line function whose locals every branch could touch. The
phases are now methods that hand their decision back in `LoopState.next`. The behaviour is pinned by
the retry, fallback, rail, verification and resume suites (none edited); these tests pin the two
contracts a future turn policy depends on:

  - every phase that can end the turn says so: a phase never leaves the loop guessing whether to go
    around again;
  - the retry budget lives in `LoopState`, outside any one iteration, so a go-around does not reset it
    (a counter reset by the very jump that consumed it never depletes, and a permanently broken stream
    would run to the iteration cap instead of ending as the error it is).
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from harness.loop import TurnEngine
from harness.loop.loop_state import LoopState, StepState
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.errors import retry_budget
from harness.tools import ToolRegistry


class Quiet(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    return TurnEngine(
        provider=Quiet(), registry=registry, permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5", max_iterations=12,
    )


def _ready(engine, monkeypatch):
    """A turn's controls (a fresh cancel event bound to the loop that runs it) and no real waiting:
    the backoff is real time, and the point of these tests is the counters."""
    import harness.loop.step_recovery as recovery

    monkeypatch.setattr(recovery, "BACKOFF_SECONDS", (0.0,) * 8)
    engine._cancel = asyncio.Event()
    return engine


def _drain(generator):
    async def run():
        return [event async for event in generator]

    return asyncio.run(run())


class _Transport(Exception):
    """A dropped connection: transient, so a retry is worth making."""


def test_a_fresh_state_goes_around_and_a_step_has_nothing_streamed():
    assert LoopState().next == "go" and LoopState().attempts == 0 and LoopState().iterations == 0
    step = StepState()
    assert step.partial_turn().text is None and step.turn is None
    step.streamed += ["hel", "lo"]
    assert step.partial_turn().text == "hello"  # what the person watched arrive, no tool calls


def test_a_failure_nothing_can_fix_ends_the_turn_and_says_so(tmp_path, monkeypatch):
    engine, state = _ready(_engine(tmp_path), monkeypatch), LoopState(iterations=1)
    events = _drain(engine._recover_failed_step(ValueError("bad request"), state, StepState()))
    assert state.next == "end"
    assert [e.type for e in events] == [EventType.ERROR]


def test_a_transient_failure_goes_around_and_the_budget_survives_the_jump(tmp_path, monkeypatch):
    engine = _ready(_engine(tmp_path), monkeypatch)
    failure = ConnectionError("connection reset by peer")
    budget = retry_budget(failure)
    assert budget >= 1  # the premise: this class of failure earns retries

    state = LoopState(iterations=1)
    outcomes = []
    for _ in range(budget + 1):
        engine._cancel = asyncio.Event()
        events = _drain(engine._recover_failed_step(failure, state, StepState()))
        outcomes.append((state.next, [e.type for e in events][-1]))
        state.iterations += 1  # the loop's own increment for the next iteration

    assert [o[0] for o in outcomes] == ["go"] * budget + ["end"]
    assert outcomes[-1][1] == EventType.ERROR  # the budget ran out, so it ended as the error it is
    assert state.attempts == budget


def test_a_retry_refunds_the_iteration_it_used(tmp_path, monkeypatch):
    """Re-sending one model call is the same step again, not another one: a flaky connection must not
    eat a turn's whole step budget without the agent having done any work."""
    engine, state = _ready(_engine(tmp_path), monkeypatch), LoopState(iterations=3)
    _drain(engine._recover_failed_step(ConnectionError("connection reset by peer"), state, StepState()))
    assert state.next == "go" and state.iterations == 2


def test_a_finished_answer_ends_the_turn_with_the_completed_event(tmp_path):
    engine, state = _engine(tmp_path), LoopState(iterations=1)
    engine._begin_loop()
    engine._turn_stop_hooked = True  # no user hooks in this fixture
    events = _drain(engine._finish_or_nudge(AssistantTurn(text="ok", finish_reason="stop"), state))
    assert state.next == "end"
    assert events[-1].type == EventType.TURN_END and events[-1].data["status"] == "completed"


def test_the_iteration_cap_is_a_rail_and_a_fresh_turn_is_not(tmp_path):
    engine = _engine(tmp_path)
    engine._begin_loop()
    assert engine._rail_stopped(0) is False
    assert engine._rail_stopped(engine.max_iterations) is True
