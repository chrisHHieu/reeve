"""The Metrics page follows the agent: a finished step is folded and announced, not the turn that ends.

Reported 2026-10-01: tokens on the Metrics page moved only when a turn finished. Three reasons stacked. The fold
ran from `save()` only, and `save()` ran at a checkpoint (a tool result), never after a model reply that called no
tool. It was debounced to one per 10 s with no trailing fold, so the last steps of a burst waited for the next
trigger. And the page fetched once on open and never again. What has to hold:

  - a finished step (a model reply, a tool result) arms a fold, a stream delta does not;
  - the fold runs off the caller's thread, no sooner than the minimum interval after the last, and a burst of
    steps is ONE fold that reads the newest log (the trailing fold);
  - when it lands, every open page is told (`metrics_changed` on /ws/events);
  - a `save()` never waits on a fold;
  - the live turn loops (interactive and background) call it on the same events.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from harness.session.events import CHECKPOINT_EVENTS, STEP_EVENTS, EventType
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server import SessionManager, create_app
from harness.interface.server.manager import metrics_api
from harness.session.sessions import SessionRecord
from harness.session.session_log import SessionLog


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(metrics_api, "_FOLD_MIN_INTERVAL_S", 0.4)
    monkeypatch.setattr(metrics_api, "_FOLD_SETTLE_S", 0.05)
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.session_store.save(
        SessionRecord(session_id="s1", workspace=str(tmp_path), model="m1", mode="workspace-write", agent="reeve")
    )
    yield manager
    manager.metrics_store.close()


def _log(mgr):
    return SessionLog(mgr.session_store.conv_dir / "s1.events.jsonl")


def _reply(log, step, tokens_in):
    """One finished model reply of a turn that has not ended: its usage is on the assistant row."""
    if step == 1:
        log.append("turn/start", turn=1)
    log.append("step/start", turn=1, step=step)
    log.append(
        "assistant/message",
        turn=1,
        step=step,
        model="zai-coding-plan:glm-5.3",
        text="thinking done",
        usage={"input": tokens_in, "output": 10, "cache_read": 0, "cache_write": 0},
        timing={"dispatched": 1.0, "first_token": 1.1, "completed": 1.5},
        tool_calls=[],
    )


def _tokens_in(mgr):
    return mgr.ops_metrics("7d")["headline"]["tokens_in"]


def _wait_for(check, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return check()


def test_a_finished_step_reaches_the_report_before_the_turn_ends(mgr):
    _reply(_log(mgr), 1, 100)  # no turn/end: the turn is still running
    mgr.note_metrics_progress("s1")
    assert _wait_for(lambda: _tokens_in(mgr) == 100)
    assert mgr.ops_metrics("7d")["headline"]["turns_completed"] == 0  # and the turn really has not ended


def test_the_last_step_of_a_burst_is_folded_by_the_trailing_fold(mgr):
    log = _log(mgr)
    _reply(log, 1, 100)
    mgr.note_metrics_progress("s1")
    assert _wait_for(lambda: _tokens_in(mgr) == 100)
    _reply(log, 2, 50)  # inside the interval after the last fold
    mgr.note_metrics_progress("s1")
    assert _wait_for(lambda: _tokens_in(mgr) == 150)  # the old 10 s debounce dropped this one


def test_a_burst_of_steps_is_one_fold(mgr, monkeypatch):
    folds = []
    real = mgr._fold_session_body
    monkeypatch.setattr(mgr, "_fold_session_body", lambda sid: folds.append(sid) or real(sid))
    _reply(_log(mgr), 1, 100)
    for _ in range(25):
        mgr.note_metrics_progress("s1")
    assert len(mgr._metrics_pending) == 1
    assert _wait_for(lambda: bool(folds)) and _wait_for(lambda: not mgr._metrics_pending)
    time.sleep(0.2)
    assert folds == ["s1"]


def test_a_save_never_waits_on_a_fold(mgr):
    _reply(_log(mgr), 1, 100)
    start = time.monotonic()
    mgr._record_session_metrics("s1")  # what `save()` calls
    assert time.monotonic() - start < 0.05
    assert mgr.metrics_store.watermark("s1") == 0.0  # not folded on this thread
    assert _wait_for(lambda: _tokens_in(mgr) == 100)


def test_the_page_is_told_when_the_fold_lands(mgr):
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    heard: list[dict] = []

    async def client(message):
        heard.append(message)

    mgr._loop = loop
    mgr.register_event_client(client)
    try:
        _reply(_log(mgr), 1, 100)
        mgr.note_metrics_progress("s1")
        assert _wait_for(lambda: bool(heard))
        assert heard[0] == {"type": "metrics_changed", "data": {"session_id": "s1"}}
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_a_fold_with_nothing_to_read_says_nothing(mgr):
    mgr._loop = None
    mgr.note_metrics_progress("never-logged")  # no log: nothing is written and nothing raises
    time.sleep(0.2)
    assert _wait_for(lambda: not mgr._metrics_pending)


# -- which events, and the loops that use them ---------------------------------------------


def test_a_step_is_a_reply_or_a_tool_result_and_a_delta_is_not():
    assert STEP_EVENTS == {EventType.ASSISTANT_MESSAGE, EventType.TOOL_FINISHED}
    for delta in (EventType.ASSISTANT_DELTA, EventType.REASONING_DELTA, EventType.TOOL_CALL_DELTA, EventType.TOOL_OUTPUT_DELTA):
        assert delta not in STEP_EVENTS
    # The reply was never a checkpoint: that is why a thinking-only step never reached the report.
    assert EventType.ASSISTANT_MESSAGE not in CHECKPOINT_EVENTS


class _Provider(ProviderClient):
    def __init__(self, turns):
        self._turns = list(turns)

    def complete(self, *, model, messages, tools=None, **settings):
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def test_the_interactive_turn_loop_notes_each_finished_step(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path, provider=_Provider([AssistantTurn(text="hello", finish_reason="stop")]))
    noted: list[str] = []
    monkeypatch.setattr(manager, "note_metrics_progress", lambda sid: noted.append(sid))
    try:
        client = TestClient(create_app(manager))
        seen = []
        with client.websocket_connect("/ws/session/live1") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "user_message", "text": "hi"})
            while True:
                frame = ws.receive_json()
                seen.append(frame["type"])
                if frame["type"] == "turn_done":
                    break
        assert "assistant_message" in seen
        assert noted == ["live1"]  # one reply, one note; the turn's end is not what asks
    finally:
        manager.metrics_store.close()
