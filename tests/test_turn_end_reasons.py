"""Typed turn-end reasons + the no-open-turn guarantee (turns.TurnEndReason).

Every way a turn ends must leave `turn/end {reason}` in the session log, with the
reason drawn from one nameable vocabulary — the `TurnEndReason` union — and a turn
the process never got to close (a crash) is repaired SYNTHETICALLY on the next attach:
reason `interrupted`, marked `synthetic`, the turn's real events untouched. Before
this, the error and stop paths ended a turn with no boundary event at all, and a crash
left `turn/start` dangling forever — a log that could not say how its own story
ended.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
from harness.llm.providers.base import ToolCall
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.session.turns import TurnEndReason


class _Scripted(ProviderClient):
    """One turn per request — a second chunk would REPLACE the step's turn."""

    def __init__(self, turns):
        self._turns = list(turns)

    def stream(self, *, model, messages, tools=None, **settings):
        if self._turns:
            yield StreamChunk(turn=self._turns.pop(0))

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("the engine must stream")

    def capabilities(self, model):
        return ModelCapabilities()


class _DiesImmediately(ProviderClient):
    def stream(self, *, model, messages, tools=None, **settings):
        raise ValueError("permanent failure")

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider, **kwargs):
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        **kwargs,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    return engine


def _run(engine, text="hello"):
    async def go():
        return [ev async for ev in engine.run(text)]

    return asyncio.run(go())


def _ends(log):
    return [e for e in log.read() if e["type"] == "turn/end"]


# -- every path closes, with a typed reason ----------------------------------------


def test_a_completed_turn_logs_the_typed_reason(tmp_path):
    engine = _engine(
        tmp_path, _Scripted([AssistantTurn(text="done", finish_reason="stop")])
    )
    _run(engine)
    (end,) = _ends(engine.log)
    assert end["reason"] == TurnEndReason.COMPLETED.value
    assert "synthetic" not in end
    assert engine.log.dangling_turns() == []


def test_a_failed_stream_closes_the_turn_as_error(tmp_path):
    """The give-up path used to return after the ERROR event with no boundary: the log
    held a turn that never said how it died."""
    engine = _engine(tmp_path, _DiesImmediately())
    _run(engine)
    (end,) = _ends(engine.log)
    assert end["reason"] == TurnEndReason.ERROR.value


def test_a_user_stop_closes_the_turn_as_interrupted(tmp_path):
    """Stop mid-stream: the provider streams a text delta, then the user presses Stop
    before any turn arrives — the turn ends `interrupted` instead of dangling."""
    engine = _engine(
        tmp_path,
        _Scripted([AssistantTurn(text="slow answer", finish_reason="stop")]),
    )

    def stream(*args, **kwargs):
        yield StreamChunk(text_delta="half an ans")
        engine._cancel.set()  # the user pressed Stop; nothing more arrives

    engine.provider.stream = stream
    _run(engine)
    (end,) = _ends(engine.log)
    assert end["reason"] == TurnEndReason.INTERRUPTED.value


def test_the_iteration_cap_logs_its_typed_reason(tmp_path):
    provider = _Scripted(
        [AssistantTurn(tool_calls=[ToolCall(id="c1", name="nope", arguments={})])]
    )
    engine = _engine(tmp_path, provider, max_iterations=1)
    _run(engine)
    end = _ends(engine.log)[-1]
    assert end["reason"] == TurnEndReason.MAX_ITERATIONS.value


def test_abandoning_the_run_mid_turn_still_closes_it(tmp_path):
    """A surface that walks away (socket gone) abandons the generator. The finalizer —
    reached because `run` propagates `aclose` into the loop — closes the turn
    `interrupted` at abandonment time, deterministically."""
    engine = _engine(
        tmp_path, _Scripted([AssistantTurn(text="long answer", finish_reason="stop")])
    )

    async def abandon():
        gen = engine.run("hello")
        async for _ in gen:  # consume the first event, then walk away
            break
        await gen.aclose()

    asyncio.run(abandon())
    (end,) = _ends(engine.log)
    assert end["reason"] == TurnEndReason.INTERRUPTED.value


# -- crash recovery ----------------------------------------------------------------


def _log_with_dangling_turn(path: Path, turn: int) -> SessionLog:
    log = SessionLog(path)
    log.append("turn/start", turn=turn)
    log.append("user/message", content="from the dead process")
    return log


def test_a_crash_left_open_turn_is_closed_synthetically(tmp_path):
    """A fresh engine adopting a dead process's log repairs the boundary BEFORE its
    own turn opens: `interrupted`, marked `synthetic`, the dead turn's events intact."""
    log = _log_with_dangling_turn(tmp_path / "events.jsonl", turn=3)
    engine = _engine(
        tmp_path, _Scripted([AssistantTurn(text="ok", finish_reason="stop")])
    )
    engine.log = log
    _run(engine)

    events = log.read()
    (repaired,) = [e for e in events if e.get("synthetic")]
    assert repaired["type"] == "turn/end"
    assert repaired["turn"] == 3
    assert repaired["reason"] == TurnEndReason.INTERRUPTED.value
    # The repair lands before the new turn opens, so turns never nest.
    start_new = next(i for i, e in enumerate(events) if e["type"] == "turn/start" and e["turn"] != 3)
    assert events.index(repaired) < start_new
    assert log.dangling_turns() == []


def test_a_legacy_log_with_several_dangling_turns_is_repaired(tmp_path):
    log = SessionLog(tmp_path / "events.jsonl")
    log.append("turn/start", turn=1)
    log.append("turn/start", turn=2)
    log.append("turn/start", turn=4)
    engine = _engine(
        tmp_path, _Scripted([AssistantTurn(text="ok", finish_reason="stop")])
    )
    engine.log = log
    _run(engine)

    repaired = {e["turn"] for e in log.read() if e.get("synthetic")}
    assert repaired == {1, 2, 4}
    assert log.dangling_turns() == []


def test_the_repair_runs_once_per_log_not_once_per_turn(tmp_path):
    engine = _engine(
        tmp_path, _Scripted([AssistantTurn(text="ok", finish_reason="stop")])
    )
    _run(engine, "first")
    _run(engine, "second")
    # One live turn per run, closed normally; no synthetic rows piled on.
    ends = _ends(engine.log)
    assert len(ends) == 2
    assert not any(e.get("synthetic") for e in ends)


def test_a_dangling_turn_is_not_invented_for_a_clean_log(tmp_path):
    engine = _engine(
        tmp_path, _Scripted([AssistantTurn(text="ok", finish_reason="stop")])
    )
    _run(engine)
    assert not [e for e in engine.log.read() if e.get("synthetic")]


# -- an error ending says what kind of error -------------------------------------------


class _Fails(ProviderClient):
    def __init__(self, exc):
        self._exc = exc

    def stream(self, *, model, messages, tools=None, **settings):
        raise self._exc

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError

    def capabilities(self, model):
        return ModelCapabilities()


def test_an_error_ending_carries_its_class(tmp_path):
    """The notice's text is for the person; the class is what a count reads. Before it, 43
    of 54 error endings since 2026-09-01 could only be called `unknown`."""
    cases = {
        "a": (RuntimeError("Error code: 400 - {'error': {'code': 'invalid_request_error'}}"), "bad_request"),
        "b": (RuntimeError("Error code: 402 - {'error': {'message': 'Insufficient Balance'}}"), "no_quota"),
        "c": (RuntimeError("No Example API key configured — add it in Settings."), "no_key"),
        "d": (ImportError("cannot import name 'x' from 'y'"), "code"),
    }
    for folder, (exc, expected) in cases.items():
        (tmp_path / folder).mkdir()
        engine = _engine(tmp_path / folder, _Fails(exc))
        _run(engine)
        (end,) = _ends(engine.log)
        assert (end["reason"], end.get("error")) == (TurnEndReason.ERROR.value, expected), folder


def test_a_completed_turn_carries_no_error_class(tmp_path):
    engine = _engine(tmp_path, _Scripted([AssistantTurn(text="done", finish_reason="stop")]))
    _run(engine)
    (end,) = _ends(engine.log)
    assert "error" not in end


def test_the_classes_a_retry_decides_on_are_kept():
    from harness.llm.providers.errors import classify_stream_error, friendly_model_error
    from harness.llm.providers.turn_errors import turn_error_class

    overloaded = RuntimeError("upstream is overloaded, please retry")
    assert turn_error_class(overloaded) == classify_stream_error(overloaded)
    assert turn_error_class(RuntimeError("something nobody has seen")) == "unknown"
    # The person is told what an insufficient balance means (DeepSeek's 402, seen 2026-10-06).
    assert "out of quota" in (friendly_model_error("m", RuntimeError("402 Insufficient Balance")) or "")


# -- an error ending the log can name and locate --------------------------------------


def test_a_status_with_no_words_still_names_the_ending():
    """The first error ending after endings got a class (2026-10-06) was a 404 for a model
    the project could not reach, and it was classed `unknown`."""
    from harness.llm.providers.errors import classify_stream_error
    from harness.llm.providers.turn_errors import turn_error_class

    cases = {
        "Error code: 404 - [{'error': {'code': 404, 'message': 'Publisher model x was not found'}}]": "no_access",
        "Error code: 400": "bad_request",
        "Error code: 422 - {'detail': 'bad'}": "bad_request",
        "Error code: 401": "no_key",
        "Error code: 500 - {}": "overloaded",
        "Error code: 418 - a teapot": "unknown",
        "Unknown Error": "unknown",
    }
    for text, expected in cases.items():
        assert turn_error_class(RuntimeError(text)) == expected, text
    # What a retry is decided on is not overridden by the status.
    for text in ("Error code: 400 - {'model': 'some-model'}", "Error code: 503"):
        exc = RuntimeError(text)
        assert turn_error_class(exc) == classify_stream_error(exc) != "unknown", text

    class WithStatus(RuntimeError):
        status_code = 422

    assert turn_error_class(WithStatus("no words at all")) == "bad_request"
    # What the text already says wins over the status it came with.
    assert turn_error_class(RuntimeError("Error code: 400 - Insufficient Balance")) == "no_quota"


def test_a_bug_in_this_apps_code_says_where_it_raised_and_never_what_it_said(tmp_path):
    from harness.llm.providers.turn_errors import raised_at

    class _Boom(ProviderClient):
        def stream(self, *, model, messages, tools=None, **settings):
            raise KeyError("the person's secret sentence")

        def complete(self, *, model, messages, tools=None, **settings):
            raise AssertionError

        def capabilities(self, model):
            return ModelCapabilities()

    engine = _engine(tmp_path, _Boom())
    _run(engine)
    (end,) = _ends(engine.log)
    assert end["error"] == "code"
    assert end["error_at"].startswith("KeyError") and "secret sentence" not in end["error_at"]

    # The location is the innermost frame of this app's own code, as a path inside it.
    from harness.context.memory import gate

    try:
        gate.judge_index(None, names=set())
    except Exception as exc:  # noqa: BLE001
        where = raised_at(exc)
    assert where.split(" at ")[1].startswith("context/memory/") and ":" in where
    assert raised_at(ValueError("raised outside the app")) == "ValueError"

    # Raised by code that is not this app's (a callback here, a library elsewhere), reached
    # through this app's: the place named is the app's frame it passed through.
    def not_ours(raw):
        raise LookupError("from outside")

    try:
        gate.judge("n.md", "---\nname: n\nmetadata:\n  type: project\n  anchors: a.py\n---\nA fact.\n", anchor=not_ours)
    except LookupError as exc:
        assert raised_at(exc).startswith("LookupError at context/memory/gate.py:")
    # A provider's refusal is not this app's code: no location is claimed for it.
    refused = tmp_path / "refused"
    refused.mkdir()
    engine = _engine(refused, _Fails(RuntimeError("Error code: 400 - {'error': {'code': 'invalid_request_error'}}")))
    _run(engine)
    assert "error_at" not in _ends(engine.log)[0]


def test_a_crash_of_the_loop_leaves_the_exception_and_its_place_on_the_ending(tmp_path, monkeypatch):
    """This path writes no notice: the row is the only place the log says what broke. Two
    endings in the store (2026-09-26, 2026-09-30) are an error with nothing beside it."""
    engine = _engine(tmp_path, _Scripted([AssistantTurn(text="done", finish_reason="stop")]))

    def broken(*args, **kwargs):
        raise ZeroDivisionError("inside the loop")

    monkeypatch.setattr(engine, "_track_check_streak", broken)
    try:
        _run(engine)
    except ZeroDivisionError:
        pass
    (end,) = _ends(engine.log)
    assert (end["reason"], end["error"]) == (TurnEndReason.ERROR.value, "crashed")
    assert end["error_at"].startswith("ZeroDivisionError") and "inside the loop" not in end["error_at"]
