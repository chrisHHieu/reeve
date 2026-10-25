"""Bounds on the one mechanism that opens a turn from inside a turn.

Every existing limit counts work WITHIN a turn — `max_iterations`, the repeat guard, the
output budget — and resets when the turn ends. A self-wake is the mechanism for opening the
next one, so none of them applied to it:

    turn N  →  sleep_until(...)  →  wake  →  turn N+1  →  sleep_until(...)  →  ...

`WakeStore.add_timer` created a wake unconditionally — no dedup, no ceiling on how many a
session held — and `resume_due_wakes` walked every due wake with no cap, so a turn that
scheduled `k` wakes produced up to `k` turns and the tick after that up to `k` more. What
these tests pin is three answers to three different questions:

    depth    is this chain making progress, or looping?
    budget   is this session simply busy?
    pending  is this request even distinct?
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from harness.loop.budgets import Budgets, HourlyBudget, hour_key
from harness.loop.selfwake import MAX_PENDING_WAKES_PER_SESSION, MAX_WAKE_DEPTH, WAKE_TURNS_PER_HOUR, KIND_COMPLETION, KIND_EVENT, KIND_TIMER, STATE_FIRED, Wake, WakeRefused, WakeStore, selfwake_tools


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def store(tmp_path):
    return WakeStore(tmp_path / "wakes.json")


# -- the counter, on its own ------------------------------------------------------


def test_a_rolling_hour_allows_up_to_the_cap_then_refuses():
    budget = HourlyBudget()
    assert all(budget.take(3) for _ in range(3))
    assert budget.take(3) is False
    assert budget.count == 3


def test_a_new_hour_is_a_fresh_allowance():
    """The answer is "not right now", never "never". A chain refused at the cap spends the
    next hour's allowance as soon as it arrives."""
    budget = HourlyBudget()
    for _ in range(3):
        budget.take(3)
    assert budget.take(3) is False
    later = _now() + timedelta(hours=1, minutes=1)
    assert budget.take(3, now=later) is True
    assert budget.count == 1


def test_the_bucket_key_is_utc_to_the_hour():
    """UTC so a daylight-saving transition cannot produce a 23- or 25-hour bucket, and
    coarse enough that the counter is one small string on disk."""
    noon = datetime(2026, 3, 8, 12, 30, tzinfo=timezone.utc)
    assert hour_key(noon) == "2026-03-08T12"
    # The same instant expressed in another offset still buckets by UTC.
    assert hour_key(noon.astimezone(timezone(timedelta(hours=7)))) == "2026-03-08T12"


def test_remaining_reports_what_is_left_without_spending_it():
    budgets = Budgets()
    assert budgets.remaining("w", 5) == 5
    budgets.take("w", 5)
    assert budgets.remaining("w", 5) == 4
    # An hour later the allowance is whole again, and nothing had to be spent to say so.
    assert budgets.remaining("w", 5, now=_now() + timedelta(hours=2)) == 5


def test_a_cap_of_zero_refuses_everything_rather_than_dividing_by_it():
    assert HourlyBudget().take(0) is False


def test_named_budgets_do_not_share_a_counter():
    """Wake hours and delegation hours have different caps; one shared counter would let a
    busy wake hour silence delegation."""
    budgets = Budgets()
    assert budgets.take("wake:s1", 1) is True
    assert budgets.take("wake:s1", 1) is False
    assert budgets.take("children:s1", 1) is True


def test_an_unreadable_counter_reads_as_fresh():
    """This comes back off disk. A corrupt counter must not take the wake system with it."""
    for junk in (None, [], "nope", {"hour": 3, "count": "x"}, {"hour": "h", "count": True}):
        assert HourlyBudget.from_dict(junk).count == 0
    assert Budgets.from_dict(None).to_dict() == {}


def test_the_counter_survives_a_reload(tmp_path):
    """A chain that spans a restart is still a chain, so the allowance cannot be resettable
    by restarting the app."""
    path = tmp_path / "wakes.json"
    store = WakeStore(path)
    for _ in range(3):
        store.take_turn_budget("s1", cap=3)
    assert store.take_turn_budget("s1", cap=3) is False
    assert WakeStore(path).take_turn_budget("s1", cap=3) is False


# -- what makes two wakes the same request ---------------------------------------


def test_an_identical_timer_is_one_wake_not_two(store):
    when = _now() + timedelta(minutes=5)
    first = store.add_timer("s1", when, note="check the deploy")
    again = store.add_timer("s1", when, note="check the deploy")
    assert again.id == first.id
    assert len(store.pending("s1")) == 1


def test_the_same_instant_for_a_different_reason_is_two_wakes(store):
    when = _now() + timedelta(minutes=5)
    store.add_timer("s1", when, note="check the deploy")
    store.add_timer("s1", when, note="check the queue")
    assert len(store.pending("s1")) == 2


def test_waiting_on_one_job_twice_is_waiting_once(store):
    first = store.add_completion("s1", "swe-worker-1")
    again = store.add_completion("s1", "swe-worker-1")
    assert again.id == first.id
    assert len(store.pending("s1")) == 1


def test_identity_is_per_kind():
    """A timer is identified by when it fires, a completion by which job, an event by which
    key — collapsing across kinds would merge unrelated requests."""
    timer = Wake("a", "s1", KIND_TIMER, fire_at="2026-01-01T00:00:00+00:00", note="n")
    job = Wake("b", "s1", KIND_COMPLETION, job_id="j1")
    event = Wake("c", "s1", KIND_EVENT, event_key="pr-opened")
    assert timer.identity() != job.identity() != event.identity() != timer.identity()


def test_dedup_does_not_cross_sessions(store):
    when = _now() + timedelta(minutes=5)
    store.add_timer("s1", when, note="n")
    store.add_timer("s2", when, note="n")
    assert len(store.pending()) == 2


# -- the three bounds --------------------------------------------------------------


def test_a_chain_past_the_depth_cap_is_refused(store):
    """The loop that had no bound: a wake-driven turn scheduling the next wake."""
    store.add_timer("s1", _now(), depth=MAX_WAKE_DEPTH)
    with pytest.raises(WakeRefused) as caught:
        store.add_timer("s1", _now() + timedelta(seconds=1), depth=MAX_WAKE_DEPTH + 1)
    # The message reaches the model verbatim, so it must name the limit AND the way out.
    message = str(caught.value)
    assert str(MAX_WAKE_DEPTH) in message
    assert "report" in message


def test_the_depth_cap_applies_to_every_kind(store):
    """One gate for all three, so a limit cannot be enforced on timers and forgotten for
    completions — which is the shape of the bug it exists to prevent."""
    for add in (
        lambda: store.add_completion("s1", "j", depth=MAX_WAKE_DEPTH + 1),
        lambda: store.add_event("s1", "e", depth=MAX_WAKE_DEPTH + 1),
    ):
        with pytest.raises(WakeRefused):
            add()


def test_a_turn_may_still_schedule_a_wake_at_the_cap(store):
    """Off-by-one matters here: refusing AT the cap would make `MAX_WAKE_DEPTH` mean
    one fewer turn than it says."""
    wake = store.add_timer("s1", _now(), depth=MAX_WAKE_DEPTH)
    assert wake.depth == MAX_WAKE_DEPTH


def test_a_session_cannot_hold_unbounded_pending_wakes(store):
    """A turn that schedules more than this is not planning, it is enumerating."""
    for i in range(MAX_PENDING_WAKES_PER_SESSION):
        store.add_timer("s1", _now() + timedelta(minutes=i + 1))
    with pytest.raises(WakeRefused) as caught:
        store.add_timer("s1", _now() + timedelta(days=1))
    assert str(MAX_PENDING_WAKES_PER_SESSION) in str(caught.value)


def test_the_pending_cap_is_per_session(store):
    for i in range(MAX_PENDING_WAKES_PER_SESSION):
        store.add_timer("s1", _now() + timedelta(minutes=i + 1))
    store.add_timer("s2", _now() + timedelta(minutes=1))  # a different session is unaffected


def test_fired_wakes_do_not_count_against_the_pending_cap(store):
    """Otherwise a long-lived session would fill its allowance with its own history."""
    for i in range(MAX_PENDING_WAKES_PER_SESSION):
        w = store.add_timer("s1", _now() + timedelta(minutes=i + 1))
        store.mark_fired(w.id)
    store.add_timer("s1", _now() + timedelta(days=1))


def test_the_turn_budget_refuses_after_the_hourly_cap(store):
    assert all(store.take_turn_budget("s1") for _ in range(WAKE_TURNS_PER_HOUR))
    assert store.take_turn_budget("s1") is False
    assert store.take_turn_budget("s2") is True


def test_depth_travels_on_the_wake_and_not_on_the_store(store):
    """It has to be on the record: the store outlives the engine, and a chain is exactly
    what must not come back as a fresh zero after a restart."""
    store.add_timer("s1", _now(), depth=4)
    assert any(w.depth == 4 for w in WakeStore(store.path).pending("s1"))


# -- the fired-wake audit trail ---------------------------------------------------


def test_fired_wakes_are_dropped_once_past_retention(store):
    wake = store.add_timer("s1", _now())
    store.mark_fired(wake.id)
    assert store.pending("s1") == []
    # Backdate the trail past the window and take another action that expires it.
    store._wakes[wake.id].fired_at = (_now() - timedelta(days=30)).isoformat()
    store.mark_fired(store.add_timer("s1", _now() + timedelta(minutes=1)).id)
    assert wake.id not in store._wakes


def test_a_fired_wake_records_when(store):
    wake = store.add_timer("s1", _now())
    store.mark_fired(wake.id)
    assert store._wakes[wake.id].state == STATE_FIRED
    assert store._wakes[wake.id].fired_at


def test_a_file_written_before_budgets_existed_still_loads(tmp_path):
    """`budgets` is a new key on an existing file, and `Wake` gained two fields. Both must
    read back from what 0.5.52 wrote."""
    path = tmp_path / "wakes.json"
    path.write_text(
        '{"wakes": [{"id": "old", "session_id": "s1", "kind": "timer",'
        ' "state": "pending", "fire_at": "2030-01-01T00:00:00+00:00", "note": "n",'
        ' "created_at": "2026-01-01T00:00:00+00:00"}]}',
        encoding="utf-8",
    )
    store = WakeStore(path)
    [wake] = store.pending("s1")
    assert wake.depth == 0 and wake.fired_at is None
    assert store.take_turn_budget("s1") is True


# -- the tools can refuse, and say why --------------------------------------------


def test_a_refused_wake_is_an_error_result_not_a_raise(store):
    """A tool that raises takes the turn down. The refusal is information the model can act
    on, so it travels the same route every other tool refusal does."""
    depth = {"n": MAX_WAKE_DEPTH + 1}
    sleep_until, wake_on, wake_on_event = selfwake_tools(
        store, "s1", depth_of=lambda: depth["n"]
    )
    for result in (
        sleep_until((_now() + timedelta(minutes=1)).isoformat()),
        wake_on("job-1"),
        wake_on_event("pr-opened"),
    ):
        assert "error" in result
        assert result["reason"] == "wake_refused"


def test_the_tools_read_the_depth_of_the_turn_that_is_calling(store):
    """A callable, not a number: the engine is built once per session and cached while the
    depth changes every turn."""
    depth = {"n": 0}
    sleep_until, _, _ = selfwake_tools(store, "s1", depth_of=lambda: depth["n"])
    first = sleep_until((_now() + timedelta(minutes=1)).isoformat())
    assert first.get("ok") is True
    assert store.pending("s1")[0].depth == 0

    depth["n"] = 3
    sleep_until((_now() + timedelta(minutes=2)).isoformat())
    assert max(w.depth for w in store.pending("s1")) == 3


def test_without_a_depth_reader_a_wake_is_depth_zero(store):
    """The CLI, the bench and direct `build_engine` callers have no chain to continue."""
    sleep_until, _, _ = selfwake_tools(store, "s1")
    sleep_until((_now() + timedelta(minutes=1)).isoformat())
    assert store.pending("s1")[0].depth == 0


# -- the policy at the tick -------------------------------------------------------
#
# `resume_due_wakes` is where a wake becomes a turn, so it is where the two cross-turn
# bounds are applied. Exercised on a bare manager so the policy is tested without
# standing up engines, sockets and stores it does not touch.


class _Unrouted:
    def __init__(self):
        self.records: list = []

    def record(self, session_id, kind, message, *, reason=""):
        self.records.append({"session_id": session_id, "reason": reason})


def _manager_with(store, *, fake_resume: bool = True):
    from harness.interface.server.manager import SessionManager

    manager = object.__new__(SessionManager)
    manager.wakes = store
    manager.unrouted = _Unrouted()
    manager.broadcasts: list = []
    manager.resumed: list = []

    async def broadcast_session(session_id, payload):
        manager.broadcasts.append(payload)

    manager.broadcast_session = broadcast_session
    if fake_resume:
        # Short-circuits the turn so a test can count deliveries without building engines.
        # Tests that exercise `_resume_wake` itself pass `fake_resume=False`, because a fake
        # here would shadow the real one and the assertion would pass for the wrong reason.
        async def _resume_wake(wake):
            manager.resumed.append(wake.id)

        manager._resume_wake = _resume_wake
    return manager


def _seed_due(store, count, session_id="s1"):
    for i in range(count):
        store.add_timer(session_id, _now() - timedelta(seconds=count - i))


def test_the_tick_resumes_at_most_the_per_tick_cap(tmp_path):
    """A burst of due wakes is spread over ticks instead of arriving as one pile of
    concurrent turns."""
    store = WakeStore(tmp_path / "wakes.json")
    manager = _manager_with(store)
    # Above the per-tick cap and below the pending cap, so the tick limit is what bites.
    burst = manager.MAX_WAKES_PER_TICK + 4
    assert burst < MAX_PENDING_WAKES_PER_SESSION
    _seed_due(store, burst)

    resumed = asyncio.run(manager.resume_due_wakes())
    assert resumed == manager.MAX_WAKES_PER_TICK
    # The remainder is still due, so the next tick picks it up rather than losing it.
    assert len(store.due()) == burst - manager.MAX_WAKES_PER_TICK


def test_a_session_over_its_hourly_allowance_gets_no_turn(tmp_path):
    """And the wake is still consumed: opening the turn is exactly what was refused, so
    leaving it pending would retry the same refusal every tick forever."""
    store = WakeStore(tmp_path / "wakes.json")
    for _ in range(WAKE_TURNS_PER_HOUR):
        store.take_turn_budget("s1")
    _seed_due(store, 1)
    manager = _manager_with(store)

    resumed = asyncio.run(manager.resume_due_wakes())
    assert resumed == 0
    assert manager.resumed == []
    assert store.pending("s1") == [], "the wake must be consumed, not retried forever"


def test_a_refused_wake_is_parked_and_announced(tmp_path):
    """A chain that stops silently is indistinguishable from an agent that finished."""
    store = WakeStore(tmp_path / "wakes.json")
    for _ in range(WAKE_TURNS_PER_HOUR):
        store.take_turn_budget("s1")
    _seed_due(store, 1)
    manager = _manager_with(store)

    asyncio.run(manager.resume_due_wakes())
    assert len(manager.unrouted.records) == 1
    assert manager.unrouted.records[0]["session_id"] == "s1"
    assert [b["type"] for b in manager.broadcasts] == ["wake_refused"]


def test_each_session_has_its_own_allowance(tmp_path):
    store = WakeStore(tmp_path / "wakes.json")
    for _ in range(WAKE_TURNS_PER_HOUR):
        store.take_turn_budget("busy")
    _seed_due(store, 1, session_id="quiet")
    manager = _manager_with(store)

    assert asyncio.run(manager.resume_due_wakes()) == 1
    assert manager.unrouted.records == []


def test_the_wake_carries_the_next_turns_depth(tmp_path):
    """The chain depth travels with the turn it opens, one deeper each time — this is what
    `sleep_until` reads to decide whether the chain may continue."""
    store = WakeStore(tmp_path / "wakes.json")
    store.add_timer("s1", _now() - timedelta(seconds=1), depth=3)
    # The REAL `_resume_wake`, so the thing under test is the depth it computes.
    manager = _manager_with(store, fake_resume=False)
    delivered: list = []

    async def deliver_to_session(session_id, message, *, source=None, wake_depth=None):
        delivered.append((session_id, wake_depth))

    def team_staleness_digest(session_id):
        return ""

    manager.deliver_to_session = deliver_to_session
    manager.team_staleness_digest = team_staleness_digest
    manager._wake_message = lambda wake: "wake"

    asyncio.run(manager.resume_due_wakes())
    assert delivered == [("s1", 4)]


def test_an_unknown_wake_message_does_not_break_the_tick(tmp_path):
    """`resume_due_wakes` swallows per-wake failures so one broken wake cannot stop the
    scheduler; the wake is still marked fired so it cannot wedge every future tick."""
    store = WakeStore(tmp_path / "wakes.json")
    _seed_due(store, 2)
    manager = _manager_with(store)

    async def _boom(wake):
        raise RuntimeError("no")

    manager._resume_wake = _boom
    assert asyncio.run(manager.resume_due_wakes()) == 0
    assert store.pending("s1") == []
