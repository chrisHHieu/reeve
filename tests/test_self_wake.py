"""Waking a session up later (`loop/selfwake`): a wake waits for a time, for a job to end or for a
named event, is due only once that happened and not after it fired, and is kept on disk. The three
tools the agent sets them with each add one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from harness.loop.selfwake import WakeStore, selfwake_tools


def _in(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest.fixture
def store(tmp_path):
    return WakeStore(tmp_path / "wakes.json")


def _due(store) -> set[str]:
    return {wake.id for wake in store.due()}


def test_a_timer_is_due_once_its_time_has_passed(store):
    later, earlier = store.add_timer("s1", _in(60)), store.add_timer("s1", _in(-1))
    assert earlier.id in _due(store) and later.id not in _due(store)


@pytest.mark.parametrize(("add", "happen"), [("add_completion", "complete_job"), ("add_event", "fire_event")])
def test_a_wake_on_a_job_or_an_event_is_due_once_that_happens(store, add, happen):
    wake = getattr(store, add)("s1", "it")
    assert wake.id not in _due(store)
    assert [w.id for w in getattr(store, happen)("it")] == [wake.id]  # the wakes it made due
    assert wake.id in _due(store)


def test_a_fired_wake_is_neither_due_nor_pending(store):
    wake = store.add_timer("s1", _in(-1))
    store.mark_fired(wake.id)
    assert wake.id not in _due(store) and wake.id not in {w.id for w in store.pending("s1")}


def test_a_wake_survives_a_reload(store, tmp_path):
    wake = store.add_completion("s1", "job-1")
    assert any(w.id == wake.id for w in WakeStore(tmp_path / "wakes.json").pending("s1"))


def test_each_tool_adds_its_kind_of_wake(store):
    sleep_until, wake_on, wake_on_event = selfwake_tools(store, "s1")
    assert wake_on("job-9")["job_id"] == "job-9"
    assert sleep_until(_in(300).isoformat())["fire_at"]
    assert sleep_until(_in(30).isoformat())["ok"]
    assert wake_on_event("alert-fired")["event_key"] == "alert-fired"
    pending = store.pending("s1")
    assert len(pending) == 4 and {w.kind for w in pending} == {"timer", "completion", "event"}
