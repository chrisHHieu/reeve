"""A request that succeeds and says nothing (engine.py + providers/base.py + errors.py).

Found by measurement, not by reading: `openrouter:z-ai/glm-5.3-flash` is one model id
fanned across six upstreams, and OpenRouter picks per request. Time-to-first-token across
five calls with an IDENTICAL body ranged 0.59s-8.75s, and the call routed to `Z.AI` closed
its stream having sent nothing at all — twice out of two.

Before this, that answer ended the turn `completed` with an empty assistant message. The
GUI renders nothing for an empty message, so the spinner stopped and the screen stayed
blank; the session log recorded a successful step. The bug was indistinguishable from "the
model is slow", which is how it was reported.

Two things are pinned here. An empty answer is RETRIED — the retry re-rolls the routing,
which is the definition of a failure worth another attempt. And when the retries are spent
it SURFACES, because a turn that ends in silence is the thing that made this unreportable.

A SECOND identical empty is not retried: the same route serving the same nothing twice is
deterministic, so the budget stops there and the error surfaces one attempt
earlier than the class budget would otherwise allow.
"""

from __future__ import annotations

import asyncio

from harness.loop import STREAM_RETRIES, TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from harness.llm.providers.base import ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry


class _Empties(ProviderClient):
    """Answers with `empties` content-free turns, then a real one."""

    def __init__(
        self,
        empties: int,
        *,
        upstream: str | None = "Z.AI",
        upstreams: list | None = None,
        then=None,
    ):
        self.empties = empties
        self.upstream = upstream
        self.upstreams = upstreams
        self.then = then if then is not None else AssistantTurn(text="the real answer")
        self.attempts = 0

    def stream(self, *, model, messages, tools=None, **settings):
        self.attempts += 1
        if self.attempts <= self.empties:
            name = self.upstream
            if self.upstreams is not None:
                name = self.upstreams[min(self.attempts - 1, len(self.upstreams) - 1)]
            yield StreamChunk(turn=AssistantTurn(text="", upstream=name))
        else:
            yield StreamChunk(turn=self.then)

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider):
    return TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
    )


def _run(engine, text="hello"):
    async def go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(go())


# -- what counts as empty ---------------------------------------------------------


def test_only_a_turn_with_nothing_in_it_counts():
    assert AssistantTurn(text="").is_empty
    assert AssistantTurn(text=None).is_empty
    # A lone newline reaches the transcript as an empty bubble.
    assert AssistantTurn(text="  \n ").is_empty
    # Thinking is content: the user watched it arrive and a retry would discard it.
    assert not AssistantTurn(text="", reasoning="hmm").is_empty
    # A tool-only turn is the normal shape of an agent step.
    assert not AssistantTurn(
        text="", tool_calls=[ToolCall(id="c1", name="run_shell", arguments={})]
    ).is_empty
    assert not AssistantTurn(text="ok").is_empty


# -- the engine -------------------------------------------------------------------


def test_an_empty_answer_is_tried_again(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _Empties(1)
    events = _run(_engine(tmp_path, provider))

    assert provider.attempts == 2, "the empty answer was accepted instead of re-rolled"
    assert events[-1].data["status"] == "completed"
    answer = next(e for e in events if e.type is EventType.ASSISTANT_MESSAGE)
    assert answer.data["text"] == "the real answer"


def test_the_retry_says_what_happened_and_discards_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    events = _run(_engine(tmp_path, _Empties(1)))

    notice = next(e for e in events if e.type is EventType.RETRYING)
    assert notice.data["of"] == STREAM_RETRIES
    # Nothing streamed, so there is no half-answer on screen to throw away.
    assert notice.data["discard_partial"] is False
    assert "empty response" in notice.data["error"]
    # The upstream that produced it is named — that is the whole diagnostic value.
    assert "Z.AI" in notice.data["error"]


def test_a_turn_that_is_empty_every_time_is_reported_not_swallowed(tmp_path, monkeypatch):
    """The bug this file exists for: the turn used to end `completed` with an empty
    message, which renders as nothing at all."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _Empties(99)
    events = _run(_engine(tmp_path, provider))

    # One retry, not the class' two: the same upstream served the same nothing twice, so
    # a third attempt buys another identical empty.
    assert provider.attempts == 2
    kinds = [e.type for e in events]
    assert EventType.ERROR in kinds, "an answer of nothing ended the turn as a success"
    error = next(e for e in events if e.type is EventType.ERROR)
    assert "empty response" in error.data["error"]
    assert not any(
        e.type is EventType.ASSISTANT_MESSAGE and not e.data.get("text") for e in events
    ), "an empty assistant message reached the surface"


def test_a_rerouted_empty_keeps_the_full_budget(tmp_path, monkeypatch):
    """The retry exists to re-roll the routing dice, so a DIFFERENT upstream each attempt
    is exactly the case the budget is for — and the one the deterministic rule must not
    touch. Three upstreams, the class' full budget, then it surfaces."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    provider = _Empties(99, upstreams=["Z.AI", "GMICloud", "Parasail"])
    events = _run(_engine(tmp_path, provider))

    assert provider.attempts == STREAM_RETRIES + 1
    assert events[-1].type is EventType.ERROR


def test_an_upstreams_identity_is_recorded_for_the_step(tmp_path, monkeypatch):
    """One model id, six machines behind it, 0.6s-14s apart. Which one answered is not
    derivable from the message — so it is written down when it is still known."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    engine = _engine(
        tmp_path, _Empties(0, then=AssistantTurn(text="hi", upstream="GMICloud"))
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    _run(engine)

    said = [e for e in engine.log.read() if e["type"] == "assistant/message"]
    assert said and said[0]["upstream"] == "GMICloud"


def test_a_plain_server_that_names_no_upstream_records_none(tmp_path, monkeypatch):
    """Most endpoints are one machine and say nothing; the field simply stays absent
    rather than inventing a name for them."""
    monkeypatch.setattr("harness.loop.step_recovery.BACKOFF_SECONDS", (0.0, 0.0))
    engine = _engine(tmp_path, _Empties(0, then=AssistantTurn(text="hi", upstream=None)))
    engine.log = SessionLog(tmp_path / "events.jsonl")
    _run(engine)

    said = [e for e in engine.log.read() if e["type"] == "assistant/message"]
    assert said and "upstream" not in said[0]
