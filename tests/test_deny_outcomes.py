"""What a person's refusal tells the model.

Two controls already existed and still do: **Deny** on the card ("not this way" — the
turn continues) and **Stop** in the composer, which when pressed at a card already
declines the action and ends the turn. A third button was written for that second
meaning and then deleted: it was measurably the same end state, iteration count, and
batch answers as Stop, so it offered no capability — only two ways to say one thing,
which for a non-developer is a question rather than a choice.

What was actually missing was words. Both paths handed the model three of them:
`denied by user`, or `interrupted by user`. Reading either with no instruction attached,
a model does the sensible thing — tries another way, sometimes a near variation of the
same call, and on the next turn proposes the very thing that was refused, because an
abandoned turn and a refused action look identical in that phrasing.

reeve had already decided a refusal needs instructions attached. The person's refusal is
the stronger signal and was the one carrying none. These tests pin both refusals now
carrying one, and pin that they stay distinguishable from each other.
"""

from __future__ import annotations

import asyncio

import aisuite as ai

from harness.loop import USER_DENY_MESSAGE, USER_STOP_MESSAGE, ApprovalOutcome, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import Mode, PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.tools import ToolRegistry


class _Persistent(ProviderClient):
    """A model that keeps asking, which is exactly what a refusal has to survive."""

    def __init__(self, calls_per_turn=1):
        self.calls = 0
        self.per_turn = calls_per_turn

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return AssistantTurn(
            tool_calls=[
                ToolCall(
                    id=f"c{self.calls}-{i}",
                    name="write_file",
                    arguments={"path": f"f{self.calls}-{i}.txt", "content": "x"},
                )
                for i in range(self.per_turn)
            ],
            finish_reason="tool_calls",
        )

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, outcome, *, calls_per_turn=1, max_iterations=6, stop=False):
    engine: TurnEngine

    async def answer(_request):
        if stop:
            # Exactly what the composer's Stop button does while a card is on screen:
            # `_interruptible` resolves the pending approval as DENY with `_cancel` set.
            engine.request_interrupt()
            await asyncio.sleep(0)
        return outcome() if callable(outcome) else outcome

    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    permissions = PermissionEngine(workspace_root=tmp_path)
    permissions.mode = Mode.WORKSPACE_WRITE
    engine = TurnEngine(
        provider=_Persistent(calls_per_turn),
        registry=registry,
        permissions=permissions,
        model="gpt-5.5",
        approver=answer,
        max_iterations=max_iterations,
    )
    return engine


def _run(engine, text="do it"):
    async def go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(go())


def _tool_contents(engine):
    return [m["content"] for m in engine.messages if m.get("role") == "tool"]


# -- "not this way": the turn continues, with instructions ------------------------


def test_a_plain_deny_lets_the_turn_continue():
    """Working around a refusal is often exactly what the user wanted — they declined
    the METHOD. Stopping every turn on every Deny would break that, and it is not what
    any comparable harness does."""
    import tempfile
    from pathlib import Path

    engine = _engine(Path(tempfile.mkdtemp()), ApprovalOutcome.DENY)
    events = _run(engine)
    assert events[-1].data["status"] == "max_iterations_exceeded"
    assert len(_tool_contents(engine)) == 6  # it kept being asked, and kept being told


def test_a_plain_deny_tells_the_model_not_to_try_a_variation(tmp_path):
    engine = _engine(tmp_path, ApprovalOutcome.DENY)
    _run(engine)
    first = _tool_contents(engine)[0]
    assert USER_DENY_MESSAGE in first
    assert "Do not retry it or attempt a variation" in first
    # And it names the other option, so stopping is a move the model knows it can make.
    assert "tell them plainly what you could not do" in first


# -- Stop at the card: the same act, finally described as one -------------------


def test_stop_at_the_card_declines_and_ends_the_turn(tmp_path):
    """This is the control that already existed. The model in this test asks again every
    turn, so a refusal that merely advised would run to the iteration cap the way a
    plain Deny does."""
    engine = _engine(tmp_path, ApprovalOutcome.DENY, stop=True)
    events = _run(engine)

    assert events[-1].type is EventType.INTERRUPTED
    assert events[-1].data["iterations"] == 1  # stopped on the first card, not the sixth
    assert len(_tool_contents(engine)) == 1
    assert not (tmp_path / "f1-0.txt").exists()  # and the action never ran


def test_stop_at_the_card_reads_as_a_refusal_not_an_abandoned_turn(tmp_path):
    """`interrupted by user` was true and useless: it cannot tell a model that the user
    walked away from a model that the user said no to THIS. The next turn it proposes the
    same thing again, which is the loop the wording caused."""
    engine = _engine(tmp_path, ApprovalOutcome.DENY, stop=True)
    _run(engine)
    said = _tool_contents(engine)[0]
    assert USER_STOP_MESSAGE in said
    assert "which declines it" in said
    assert "do not look for a way around it" in said
    assert "do not start something else" in said


def test_the_two_refusals_stay_distinguishable(tmp_path):
    """They must not converge on one message: one says "find another way", the other
    says "do not". A model that could not tell them apart would obey whichever it read
    last."""
    denied = _engine(tmp_path, ApprovalOutcome.DENY)
    _run(denied)
    stopped = _engine(tmp_path, ApprovalOutcome.DENY, stop=True)
    _run(stopped)

    assert USER_DENY_MESSAGE != USER_STOP_MESSAGE
    assert USER_DENY_MESSAGE in _tool_contents(denied)[0]
    assert USER_STOP_MESSAGE in _tool_contents(stopped)[0]
    # Only one of them offers a way forward.
    assert "take it" in USER_DENY_MESSAGE
    assert "take it" not in USER_STOP_MESSAGE


def test_a_stop_does_not_leak_into_the_next_turn(tmp_path):
    """The user refused one action, not the conversation."""
    engine = _engine(tmp_path, ApprovalOutcome.DENY, stop=True)
    _run(engine, "do it")

    async def allow(_request):
        return ApprovalOutcome.ONCE

    engine.approver = allow
    events = _run(engine, "now do this other thing")
    assert not engine._cancel.is_set()
    assert events[-1].type is not EventType.INTERRUPTED


def test_no_third_control_was_left_behind():
    """The deleted button's vocabulary has to go with it: a value the UI never sends and
    the server would still accept is a promise nothing keeps."""
    assert not hasattr(ApprovalOutcome, "STOP")
    assert "stop" not in {o.value for o in ApprovalOutcome}


# -- the wire -------------------------------------------------------------------


def test_three_consecutive_denials_get_a_hard_stop_sentence(tmp_path):
    """The denial breaker: a model that keeps re-proposing refusals is
    told to stop after three in a row — guidance text appended to the deny result, never
    a grant."""
    engine = _engine(tmp_path, ApprovalOutcome.DENY)
    _run(engine)
    contents = _tool_contents(engine)
    assert len(contents) >= 3
    assert "Stop retrying this action" not in contents[0]
    assert "Stop retrying this action" not in contents[1]
    assert "Stop retrying this action" in contents[2]


def test_one_approval_resets_the_denial_streak(tmp_path):
    outcomes = [
        ApprovalOutcome.DENY,
        ApprovalOutcome.DENY,
        ApprovalOutcome.ONCE,
        ApprovalOutcome.DENY,
    ]
    index = {"n": 0}

    def next_outcome():
        value = outcomes[min(index["n"], len(outcomes) - 1)]
        index["n"] += 1
        return value

    engine = _engine(tmp_path, next_outcome, max_iterations=6)
    _run(engine)
    contents = _tool_contents(engine)
    assert len(contents) >= 4
    assert "Stop retrying this action" not in contents[3]


def test_an_unknown_refusal_word_degrades_to_a_refusal():
    """`POST /v1/inbox/{id}/resolve` takes a raw string, so an unknown word must decline
    rather than allow — including "stop", which a client built against the deleted
    button might still send."""
    import types

    from harness.interface.server.manager import SessionManager

    stub = types.SimpleNamespace(
        task_store=types.SimpleNamespace(task_for_run_session=lambda _s: None)
    )
    request = types.SimpleNamespace(
        tool_name="write_file", arguments={}, metadata=None
    )
    for word in ("stop", "nonsense_word"):
        assert (
            SessionManager.approval_outcome(stub, word, request, "s1")
            is ApprovalOutcome.DENY
        )
