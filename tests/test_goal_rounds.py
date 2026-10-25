"""Goal rounds — same-session continuation.

The mechanism under test: a durable `phase` + process-local `armed` goal opens one
ordinary turn per idle moment, with a fixed `<goal_round>` prompt, up to a cap; a person's
turn always wins (idle-only); consecutive rounds run without further user input; a
stopped or failed round pauses instead of retrying; a restart disarms without rewriting
the phase; the cap exhaustion is a named blocker; and the human's route can set, pause,
resume, edit and clear while the model's tool cannot resume what a person paused — with
the persona's `scheduling` gate binding the two arming actions the human shares with the
model.
"""

from __future__ import annotations

import asyncio
import json


from harness.loop.goals import ACT_ARMED, ACT_DISARMED, BLOCKED_AFTER_ROUNDS, DEFAULT_MAX_ROUNDS, MAX_MAX_ROUNDS, Goal, GoalStore, PHASE_ACTIVE, PHASE_BLOCKED, PHASE_COMPLETE, PHASE_PAUSED, goal_tools, round_prompt
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server.manager import SessionManager


# -- the store ------------------------------------------------------------------


def _store(tmp_path, monkeypatch, session_id="goal-tests") -> GoalStore:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    store = GoalStore.for_session(session_id)
    assert store is not None
    return store


def test_store_round_trips_and_survives_a_broken_file(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    assert store.load() is None and not store.exists()
    assert store.activation() == ACT_DISARMED

    goal = Goal(objective="write the report", max_rounds=3)
    before = goal.revision
    assert store.save(goal) is True
    assert store.exists()
    loaded = store.load()
    assert loaded is not None
    assert loaded.objective == "write the report"
    assert loaded.max_rounds == 3 and loaded.phase == PHASE_ACTIVE
    assert loaded.revision == before + 1  # save bumps it for the revision fence
    assert loaded.blocker is None

    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() is None  # best-effort: a broken file reads as "no goal"


def test_a_legacy_status_file_still_reads_as_a_goal(tmp_path, monkeypatch):
    """The first iteration of this store wrote `status: armed|completed|…`; a dev state
    dir from before the phase/activation split must read as a goal, not a broken file."""
    store = _store(tmp_path, monkeypatch, "legacy")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps({"objective": "old", "status": "armed", "rounds_started": 2, "max_rounds": 4}),
        encoding="utf-8",
    )
    loaded = store.load()
    assert loaded is not None and loaded.phase == PHASE_ACTIVE
    assert loaded.rounds_started == 2


def test_startup_disarm_is_activation_only(tmp_path, monkeypatch):
    """A restart must never keep opening rounds on its own (a driver mounted over
    existing state never arms a goal) — and it must not rewrite the durable phase, so a
    reopened session shows the inactive goal it had, not a rewritten history."""
    first = _store(tmp_path, monkeypatch, "g1")
    first.save(Goal(objective="keep going"))
    first.arm()
    second = _store(tmp_path, monkeypatch, "g2")
    second.save(Goal(objective="already paused", phase=PHASE_PAUSED))
    second.arm()

    assert GoalStore.disarm_all() == 2

    assert first.load().phase == PHASE_ACTIVE  # NOT paused, NOT rewritten
    assert first.activation() == ACT_DISARMED
    assert second.load().phase == PHASE_PAUSED
    assert second.activation() == ACT_DISARMED
    assert "restarted" not in (first.load().note or "")


# -- the model's controls -------------------------------------------------------


def _call(store: GoalStore, **kwargs) -> dict:
    tool = goal_tools(store)[0]
    return tool(**kwargs)


def test_the_model_controls_the_goal_lifecycle(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    assert _call(store, action="status") == {"ok": True, "goal": None}
    assert "needs an objective" in _call(store, action="set")["error"]

    armed = _call(store, action="set", objective="finish the deck", max_rounds=99)
    assert armed["ok"] is True and armed["goal"]["phase"] == PHASE_ACTIVE
    assert armed["goal"]["max_rounds"] == 99  # inside the positive-safe-integer range
    assert armed["goal"]["activation"] == ACT_ARMED

    assert _call(store, action="pause", reason="user is editing")["goal"]["phase"] == PHASE_PAUSED
    # A pause is the user's stop: the model may ask, but not act (see the dedicated test).
    assert "user must resume" in _call(store, action="resume")["error"]

    done = _call(store, action="done", reason="shipped")
    assert done["goal"]["phase"] == PHASE_COMPLETE
    assert "<goal_complete>" in done["wrapup"] and done["goal"]["activation"] == ACT_DISARMED
    # A completed goal is not resumable — setting a new one is the honest move.
    assert "already complete" in _call(store, action="resume")["error"]

    # A direct human turn may report blocked (no round authority to second-guess).
    assert _call(store, action="blocked")["error"]  # a reason is required
    blocked = _call(store, action="blocked", reason="needs the numbers")
    assert blocked["goal"]["phase"] == PHASE_BLOCKED
    assert blocked["goal"]["blocker"] == {"code": "model-reported", "message": "needs the numbers"}
    assert "<goal_blocked>" in blocked["wrapup"]
    assert "unknown action" in _call(store, action="dance")["error"]


def test_the_default_cap_applies_when_unspecified(tmp_path, monkeypatch):
    """The default cap: 256. An omitted cap must land there, not on 5."""
    assert DEFAULT_MAX_ROUNDS == 256
    store = _store(tmp_path, monkeypatch)
    _call(store, action="set", objective="something")
    assert store.load().max_rounds == DEFAULT_MAX_ROUNDS


def test_the_hard_ceiling_is_dshs_positive_safe_integer(tmp_path, monkeypatch):
    """A positive SAFE integer is valid and enforces no tighter upper bound, so the clamp cannot be lower than
    `Number.MAX_SAFE_INTEGER` — and a value past it clamps exactly there."""
    assert MAX_MAX_ROUNDS == 2**53 - 1
    store = _store(tmp_path, monkeypatch)
    armed = _call(store, action="set", objective="everything", max_rounds=2**53 + 10)
    assert armed["goal"]["max_rounds"] == MAX_MAX_ROUNDS
    raised = _call(store, action="edit", max_rounds=2**60)
    assert raised["goal"]["max_rounds"] == MAX_MAX_ROUNDS


def test_set_refuses_to_overwrite_an_unfinished_goal(tmp_path, monkeypatch):
    """A silent overwrite would drop the objective someone was told the agent pursues;
    Edit, pause or clear must come first, and this tool requires it."""
    store = _store(tmp_path, monkeypatch)
    _call(store, action="set", objective="the first")
    refused = _call(store, action="set", objective="the second")
    assert "already active" in refused["error"] and "edit" in refused["error"]
    assert store.load().objective == "the first"
    # A COMPLETE goal is replaceable: that is how a new objective starts.
    _call(store, action="done", reason="finished")
    replaced = _call(store, action="set", objective="the second")
    assert replaced["ok"] is True and replaced["goal"]["rounds_started"] == 0
    assert store.load().objective == "the second"
    assert _call(store, action="clear")["ok"] is True and store.load() is None


def test_edit_preserves_progress_and_clear_deletes(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    _call(store, action="set", objective="write it", max_rounds=3)
    goal = store.load()
    goal.rounds_started = 2
    store.save(goal)

    updated = _call(store, action="edit", objective="write it better", max_rounds=7)
    assert updated["ok"] is True
    edited = store.load()
    assert edited.objective == "write it better"
    assert edited.max_rounds == 7
    assert edited.rounds_started == 2  # progress survives an edit
    assert "needs objective, max_rounds, checks and/or token_budget" in _call(store, action="edit")["error"]

    assert _call(store, action="clear")["ok"] is True
    assert store.load() is None and not store.exists()
    assert store.activation() == ACT_DISARMED


def test_the_model_cannot_resume_a_paused_goal(tmp_path, monkeypatch):
    """A pause is the user's stop (or a failed round's); the model asking again in the
    same breath is exactly the loop the pause exists to break."""
    store = _store(tmp_path, monkeypatch)
    _call(store, action="set", objective="keep going")
    _call(store, action="pause", reason="user stopped it")
    refused = _call(store, action="resume")
    assert "user must resume" in refused["error"]
    assert store.load().phase == PHASE_PAUSED and store.activation() == ACT_DISARMED


def test_resume_handles_disarmed_and_blocked_but_requires_capacity(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    _call(store, action="set", objective="keep going", max_rounds=2)
    store.disarm()  # what a restart does, without touching the phase
    assert _call(store, action="resume")["goal"]["activation"] == ACT_ARMED
    assert store.load().phase == PHASE_ACTIVE

    goal = store.load()
    goal.phase = PHASE_BLOCKED
    goal.blocker = {"code": "round-limit", "message": "out of rounds"}
    goal.rounds_started = 2
    store.save(goal)
    store.disarm()
    assert "used all 2 rounds" in _call(store, action="resume")["error"]
    # Raising the cap with an edit is what makes the blocked goal resumable again.
    assert _call(store, action="edit", max_rounds=4)["ok"] is True
    assert _call(store, action="resume")["goal"]["phase"] == PHASE_ACTIVE
    assert store.load().rounds_started == 2  # the cap raise did not reset progress


def test_blocked_threshold_binds_only_inside_a_goal_round(tmp_path, monkeypatch):
    """`blocked` before N admitted rounds is rejected when the model is inside an
    autonomous round; a direct human turn is not
    second-guessed by a count, and a reason is always required."""
    store = _store(tmp_path, monkeypatch)
    _call(store, action="set", objective="something hard", max_rounds=5)

    user_turn = goal_tools(store, round_of=lambda: 0)[0]
    assert "needs a reason" in user_turn(action="blocked")["error"]
    assert user_turn(action="blocked", reason="only the user can")["ok"] is True

    store.clear()
    _call(store, action="set", objective="something hard", max_rounds=5)
    goal = store.load()
    goal.rounds_started = 1
    store.save(goal)
    round_one = goal_tools(store, round_of=lambda: 1)[0]
    early = round_one(action="blocked", reason="it is hard")
    assert f"at least {BLOCKED_AFTER_ROUNDS}" in early["error"]
    assert store.load().phase == PHASE_ACTIVE

    goal = store.load()
    goal.rounds_started = BLOCKED_AFTER_ROUNDS
    store.save(goal)
    late = goal_tools(store, round_of=lambda: BLOCKED_AFTER_ROUNDS)[0](
        action="blocked", reason="needs the numbers"
    )
    assert late["ok"] is True and "<goal_blocked>" in late["wrapup"]
    blocked = store.load()
    assert blocked.phase == PHASE_BLOCKED
    assert blocked.blocker == {"code": "model-reported", "message": "needs the numbers"}
    assert blocked.note == "needs the numbers"  # the note rides alongside the code


def test_the_round_prompt_is_self_contained_and_wraps_up_on_the_last_round():
    goal = Goal(objective="ship the release notes", max_rounds=4)
    prompt = round_prompt(goal, 2)
    assert prompt.startswith("<goal_round>") and prompt.endswith("</goal_round>")
    assert json.dumps(goal.objective) in prompt
    assert "Round 2 of 4." in prompt
    assert 'goal(action="done"' in prompt and 'goal(action="blocked"' in prompt
    assert "FINAL permitted round" not in prompt

    final = round_prompt(goal, 4)
    assert "FINAL permitted round" in final
    assert "user-facing summary" in final


def test_the_tool_is_gated_on_the_scheduling_trait(tmp_path, monkeypatch):
    """Goals are autonomously-opened turns; only personas that opted into autonomous
    continuity (the `scheduling` trait, same as self-wake) carry the controls."""
    from harness.compose import build_engine
    from harness.modes.agents.chat import chat_agent
    from harness.modes.agents.reeve import reeve_agent

    store = _store(tmp_path, monkeypatch)
    with_goals = build_engine(
        agent=reeve_agent(),
        workspace=tmp_path,
        provider=Provider(),
        goal_store=store,
        session_id="gated-on",
    )
    assert "goal" in with_goals.registry.names()

    without = build_engine(
        agent=chat_agent(),
        workspace=tmp_path,
        provider=Provider(),
        goal_store=store,  # supplied, but Chat has not opted into autonomous continuity
        session_id="gated-off",
    )
    assert "goal" not in without.registry.names()


# -- the driver -----------------------------------------------------------------


class Provider(ProviderClient):
    """Answers every turn with a plain completion."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model_not_found: does not exist or you do not have access")
        return AssistantTurn(text="step done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, monkeypatch, provider=None) -> SessionManager:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=provider or Provider())


def _armed(store: GoalStore, **kwargs) -> Goal:
    goal = Goal(**kwargs)
    store.save(goal)
    store.arm()
    return goal


async def _wait_for(predicate, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


def _rounds_in(engine) -> list[dict]:
    return [m for m in engine.messages if "goal_round" in str(m.get("content"))]


async def test_a_round_opens_at_idle_with_the_prompt_and_charges_the_cap(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-driver"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    assert "goal" in engine.registry.names()  # the agent has `scheduling`

    store = GoalStore.for_session(sid)
    _armed(store, objective="finish the report", max_rounds=3)
    mgr._loop = asyncio.get_running_loop()

    await mgr._continue_goal(sid)

    goal = store.load()
    assert goal.rounds_started == 1 and goal.phase == PHASE_ACTIVE
    rounds = _rounds_in(engine)
    assert len(rounds) == 1
    assert "finish the report" in rounds[0]["content"] and "Round 1 of 3" in rounds[0]["content"]
    # The message source rides the round, so the `goal` tool knows its authority.
    assert rounds[0]["source"]["kind"] == "goal_round"
    assert engine.goal_round == 1


async def test_a_round_is_charged_at_admission_not_when_it_finishes(tmp_path, monkeypatch):
    """`rounds_started` is bumped when the goal message enters the log: while round 1 runs the store and the bar read `1/M`."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-admission"
    mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    _armed(store, objective="charge me early", max_rounds=4)
    frames: list[dict] = []
    observed: list[int] = []

    async def record(session_id, message):
        frames.append(message)

    real_deliver = mgr.deliver_to_session

    async def deliver(*args, **kwargs):
        # Sampled INSIDE the delivery: the charge is already durable here.
        observed.append(store.load().rounds_started)
        return await real_deliver(*args, **kwargs)

    mgr.broadcast_session = record  # type: ignore[assignment]
    mgr.deliver_to_session = deliver  # type: ignore[assignment]
    await mgr._continue_goal(sid)

    assert observed == [1]  # `1/M` before the turn even ran
    assert store.load().rounds_started == 1
    charge = [f for f in frames if f["type"] == "goal"][-1]
    assert charge["data"]["rounds_started"] == 1 and charge["data"]["action"] == "round"


async def test_a_delivery_that_never_ran_rolls_the_charge_back(tmp_path, monkeypatch):
    """The busy/no-engine race has no round behind it: the admission charge is rolled
    back, so the inherited contract holds — a yield does not consume a round."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-race"
    mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    _armed(store, objective="raced", max_rounds=4)
    frames: list[dict] = []

    async def record(session_id, message):
        frames.append(message)

    async def never_ran(*args, **kwargs):
        return None  # what deliver_to_session returns when it cannot claim the session

    mgr.broadcast_session = record  # type: ignore[assignment]
    mgr.deliver_to_session = never_ran  # type: ignore[assignment]
    await mgr._continue_goal(sid)

    assert store.load().rounds_started == 0  # charged then rolled back
    actions = [f["data"].get("action") for f in frames if f["type"] == "goal"]
    assert actions == ["round", "round-rollback"]
    assert [f["data"]["rounds_started"] for f in frames if f["type"] == "goal"] == [1, 0]


async def test_a_person_turn_makes_the_round_yield_without_charging_it(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-busy"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    _armed(store, objective="later", max_rounds=3)

    mgr.mark_running(sid)  # the user is mid-turn
    try:
        await mgr._continue_goal(sid)
    finally:
        mgr._running_sessions.discard(sid)

    assert store.load().rounds_started == 0  # yielded, not consumed
    assert not _rounds_in(engine)


async def test_consecutive_rounds_run_without_the_user_typing(tmp_path, monkeypatch):
    """The chaining fix: a round's own idle edge must re-enter the serialized driver. Before it, the in-flight mark
    was still held while `mark_idle` fired, so round 2 needed an external event."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-chain"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    _armed(store, objective="two rounds", max_rounds=2)
    mgr._loop = asyncio.get_running_loop()

    mgr.mark_running(sid)
    mgr.mark_idle(sid)  # the ONLY external idle event in this test

    assert await _wait_for(lambda: store.load().phase == PHASE_BLOCKED)
    goal = store.load()
    assert goal.rounds_started == 2 and goal.blocker["code"] == "round-limit"
    assert len(_rounds_in(engine)) == 2
    assert "Round 2 of 2" in _rounds_in(engine)[1]["content"]


async def test_the_cap_blocks_with_a_named_blocker_and_a_notice(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-cap"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    _armed(store, objective="loop forever", max_rounds=1, rounds_started=1)
    broadcasts: list[dict] = []

    async def record(session_id, message):
        broadcasts.append(message)

    mgr.broadcast_session = record  # type: ignore[assignment]
    await mgr._continue_goal(sid)

    blocked = store.load()
    assert blocked.phase == PHASE_BLOCKED
    assert blocked.blocker == {
        "code": "round-limit",
        "message": "Goal reached its configured limit of 1 rounds.",
    }
    notice = next(
        m for m in engine.messages if m.get("role") == "notice" and m.get("kind") == "goal"
    )
    assert "round limit" in notice["text"]
    assert not _rounds_in(engine)
    goal_frames = [m for m in broadcasts if m["type"] == "goal"]
    assert goal_frames and goal_frames[0]["data"]["driver"] is True
    assert goal_frames[0]["data"]["blocker"]["code"] == "round-limit"
    activation_frames = [m for m in broadcasts if m["type"] == "goal_activation"]
    assert activation_frames and activation_frames[0]["data"]["activation"] == ACT_DISARMED


async def test_a_failed_round_pauses_the_goal_instead_of_retrying(tmp_path, monkeypatch):
    provider = Provider(fail=True)
    mgr = _manager(tmp_path, monkeypatch, provider)
    sid = "goal-error"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    _armed(store, objective="will fail", max_rounds=3)
    mgr.broadcast_session = _noop  # type: ignore[assignment]

    await mgr._continue_goal(sid)

    paused = store.load()
    assert paused.phase == PHASE_PAUSED
    assert "error" in paused.note
    assert paused.rounds_started == 1  # the round ran; it just failed
    assert store.activation() == ACT_DISARMED
    # And the pause is visible in the transcript, not only in the store.
    assert any(
        m.get("role") == "notice" and m.get("kind") == "goal" for m in engine.messages
    )


async def test_the_idle_hook_schedules_the_next_round(tmp_path, monkeypatch):
    """The wiring, not just the driver: every turn path ends in `mark_idle`, and that is
    what must notice an armed goal and open the next round on the server loop."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-idle-hook"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    _armed(store, objective="continue after idle", max_rounds=1)
    mgr._loop = asyncio.get_running_loop()

    mgr.mark_running(sid)
    mgr.mark_idle(sid)  # the end of some turn — here a synthetic one

    # Waits for the whole driver pass to settle (the round then its cap block), so the
    # scheduled task cannot outlive the test.
    assert await _wait_for(lambda: store.load().phase == PHASE_BLOCKED)
    assert store.load().rounds_started == 1
    assert len(_rounds_in(engine)) == 1


async def test_an_unarmed_goal_opens_nothing(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-paused"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    store.save(Goal(objective="stop", phase=PHASE_PAUSED))
    store.disarm()

    await mgr._continue_goal(sid)
    assert not _rounds_in(engine)
    assert store.load().rounds_started == 0


async def test_a_silently_disarmed_active_goal_opens_nothing(tmp_path, monkeypatch):
    """A restart's state exactly: `active` on disk, disarmed in this process. The driver
    must NOT revive it (the goal rounds' never-revive rule, kept)."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-disarmed"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    store.save(Goal(objective="do not revive me", max_rounds=3))
    store.disarm()

    mgr._loop = asyncio.get_running_loop()
    mgr.mark_running(sid)
    mgr.mark_idle(sid)
    await asyncio.sleep(0.3)

    assert store.load().phase == PHASE_ACTIVE  # durable phase untouched
    assert store.load().rounds_started == 0
    assert not _rounds_in(engine)


async def test_an_edit_during_setup_invalidates_the_reservation(tmp_path, monkeypatch):
    """The revision fence: the checkpoint happens BEFORE the round is queued, and the
    reservation is re-validated after it — an edit that lands in that window wins."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-fence"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    _armed(store, objective="original", max_rounds=3)
    order: list[str] = []

    real_save = mgr.save

    def save_then_edit(session_id, engine_arg):
        real_save(session_id, engine_arg)
        order.append("checkpoint")
        edited = store.load()
        edited.objective = "edited mid-setup"
        store.save(edited)  # a human edit lands while the round is being prepared
        order.append("edit")

    real_deliver = mgr.deliver_to_session

    async def deliver(*args, **kwargs):
        order.append("deliver")
        return await real_deliver(*args, **kwargs)

    mgr.save = save_then_edit  # type: ignore[assignment]
    mgr.deliver_to_session = deliver  # type: ignore[assignment]
    await mgr._continue_goal(sid)

    assert order == ["checkpoint", "edit"]  # the stale reservation never reached delivery
    assert store.load().objective == "edited mid-setup"
    assert store.load().rounds_started == 0
    assert not _rounds_in(engine)


async def test_the_human_route_controls_the_goal_and_a_pause_aborts_the_round(
    tmp_path, monkeypatch
):
    """`POST /goal` is the human's door: resume works where the model's is refused, an
    edit can raise a cap and a resume can then continue, and a pause on a running session
    pulls the live turn."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-route"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    store.save(Goal(objective="keep going", max_rounds=1, rounds_started=1))
    store.arm()
    interrupted: list[str] = []
    engine.request_interrupt = lambda: interrupted.append(sid)  # type: ignore[assignment]
    mgr.broadcast_session = _noop  # type: ignore[assignment]

    # The model's resume cannot; the human's can — after the cap is raised.
    assert mgr.goal_for_session(sid)["phase"] == PHASE_ACTIVE
    assert "raise the round limit" in (
        await mgr.goal_action(sid, "resume")
    )["error"]
    assert (await mgr.goal_action(sid, "edit", max_rounds=4))["ok"] is True
    assert (await mgr.goal_action(sid, "resume"))["goal"]["activation"] == ACT_ARMED

    # Pause while a turn is running: the live round is aborted, the phase is durable.
    mgr.mark_running(sid)
    try:
        paused = await mgr.goal_action(sid, "pause", reason="user stopped it")
    finally:
        mgr._running_sessions.discard(sid)
    assert paused["goal"]["phase"] == PHASE_PAUSED
    assert store.activation() == ACT_DISARMED
    assert interrupted == [sid]
    # The human can resume the very goal the model is refused; clear deletes it.
    assert (await mgr.goal_action(sid, "resume"))["ok"] is True
    assert (await mgr.goal_action(sid, "clear"))["goal"] is None
    assert store.load() is None


async def test_the_human_route_sets_replaces_and_refuses(tmp_path, monkeypatch):
    """`set` is the composer `/goal` command's create: objective required, whitespace
    collapsed, the cap clamped, and a non-complete goal protected with the standard wording —
    while a COMPLETE one is replaceable (that is how a new objective starts)."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-set"
    mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    frames: list[dict] = []

    async def record(session_id, message):
        frames.append(message)

    mgr.broadcast_session = record  # type: ignore[assignment]

    assert "needs an objective" in (await mgr.goal_action(sid, "set"))["error"]
    created = await mgr.goal_action(
        sid, "set", objective="  ship   the release  ", max_rounds=3
    )
    assert created["ok"] is True
    assert created["goal"]["objective"] == "ship the release"  # trimmed + collapsed
    assert created["goal"]["max_rounds"] == 3
    assert created["goal"]["phase"] == PHASE_ACTIVE
    assert created["goal"]["activation"] == ACT_ARMED
    assert "continue toward this objective" in created["note"]
    # The route broadcasts the whole view (driver=False: the command's notice is the
    # transcript line), so the GoalDock moves without a refetch.
    goal_frames = [f for f in frames if f["type"] == "goal"]
    assert goal_frames and goal_frames[-1]["data"]["action"] == "set"
    assert goal_frames[-1]["data"]["driver"] is False

    refused = await mgr.goal_action(sid, "set", objective="something else")
    assert refused["ok"] is False
    assert (
        "use /goal edit <objective> to change it or /goal clear before replacing it"
        in refused["error"]
    )
    assert store.load().objective == "ship the release"

    done = store.load()
    done.phase = PHASE_COMPLETE
    store.save(done)
    store.disarm()
    replaced = await mgr.goal_action(sid, "set", objective="the next thing")
    assert replaced["ok"] is True and replaced["goal"]["rounds_started"] == 0
    assert store.load().objective == "the next thing"
    assert store.activation() == ACT_ARMED


async def test_the_configured_default_cap_reaches_both_creation_paths(
    tmp_path, monkeypatch
):
    """`[goal] max_rounds`: the resolved value is what an
    omitted cap lands on, for the model's tool and the human's route alike."""
    (tmp_path / ".reeve").mkdir()
    (tmp_path / ".reeve" / "config.toml").write_text("[goal]\nmax_rounds = 7\n")
    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-config"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    mgr.broadcast_session = _noop  # type: ignore[assignment]

    assert engine.goal_default_max_rounds == 7
    # The model's tool, exactly as `build_engine` bound it.
    tool = goal_tools(store, default_max_rounds=engine.goal_default_max_rounds)[0]
    assert tool(action="set", objective="from the model")["goal"]["max_rounds"] == 7
    store.clear()

    # The human's route: the engine carries the same resolved config.
    created = await mgr.goal_action(sid, "set", objective="from the person")
    assert created["ok"] is True and created["goal"]["max_rounds"] == 7

    # A session with no engine and no record still resolves the GLOBAL config.
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "config.toml").write_text("[goal]\nmax_rounds = 11\n")
    assert mgr._goal_default_rounds("no-record") == 11


async def test_the_route_reads_and_writes_over_http(tmp_path, monkeypatch):
    """The exposure contract: `GET` reads the view (null when none), `POST` runs the five
    human actions and refuses an unknown one."""
    from fastapi.testclient import TestClient

    from harness.interface.server import create_app

    mgr = _manager(tmp_path, monkeypatch)
    sid = "goal-http"
    mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = GoalStore.for_session(sid)
    _armed(store, objective="ship it", max_rounds=2)
    mgr.broadcast_session = _noop  # type: ignore[assignment]
    client = TestClient(create_app(mgr))

    # The generated OpenAPI document advertises the read/control surface.
    schema = client.get("/openapi.json").json()
    assert "/v1/sessions/{session_id}/goal" in schema["paths"]
    assert {"get", "post"} <= set(schema["paths"]["/v1/sessions/{session_id}/goal"])

    empty = client.get("/v1/sessions/no-goal/goal").json()
    assert empty == {"goal": None}

    view = client.get(f"/v1/sessions/{sid}/goal").json()["goal"]
    assert view["objective"] == "ship it"
    assert view["phase"] == PHASE_ACTIVE and view["activation"] == ACT_ARMED
    assert view["rounds_started"] == 0 and view["max_rounds"] == 2

    paused = client.post(
        f"/v1/sessions/{sid}/goal", json={"action": "pause", "reason": "hold on"}
    ).json()
    assert paused["ok"] is True and paused["goal"]["phase"] == PHASE_PAUSED

    edited = client.post(
        f"/v1/sessions/{sid}/goal", json={"action": "edit", "objective": "ship it twice"}
    ).json()
    assert edited["goal"]["objective"] == "ship it twice"

    resumed = client.post(f"/v1/sessions/{sid}/goal", json={"action": "resume"}).json()
    assert resumed["goal"]["phase"] == PHASE_ACTIVE and resumed["goal"]["activation"] == ACT_ARMED

    # `set` (the composer `/goal` command's create) is refused over an unfinished goal…
    refused_set = client.post(
        f"/v1/sessions/{sid}/goal", json={"action": "set", "objective": "replacement"}
    ).json()
    assert refused_set["ok"] is False
    assert "use /goal edit <objective>" in refused_set["error"]
    assert client.get(f"/v1/sessions/{sid}/goal").json()["goal"]["objective"] == "ship it twice"

    refused = client.post(f"/v1/sessions/{sid}/goal", json={"action": "dance"}).json()
    assert refused["ok"] is False and "unknown action" in refused["error"]

    cleared = client.post(f"/v1/sessions/{sid}/goal", json={"action": "clear"}).json()
    assert cleared == {"ok": True, "goal": None, "note": "the goal was cleared"}
    assert client.get(f"/v1/sessions/{sid}/goal").json() == {"goal": None}

    # …and accepted (armed, cap applied) once there is no unfinished goal to protect.
    created = client.post(
        f"/v1/sessions/{sid}/goal",
        json={"action": "set", "objective": "again", "max_rounds": 9},
    ).json()
    assert created["ok"] is True
    assert created["goal"]["objective"] == "again" and created["goal"]["max_rounds"] == 9
    assert created["goal"]["phase"] == PHASE_ACTIVE
    assert created["goal"]["activation"] == ACT_ARMED


async def _noop(*args, **kwargs) -> None:  # pragma: no cover - tiny async sink
    return None


def test_a_rounds_binding_survives_an_attachment_inside_it():
    """Retry and resume re-read the round from the turn's opening message. A todo nudge
    or a screenshot the engine attached mid-round is not that message; read as one, it
    cost the retried round its completion authority (round 0)."""
    from types import SimpleNamespace

    from harness.loop.controls import ControlsMixin

    holder = SimpleNamespace(
        messages=[
            {"role": "user", "content": "<goal_round>…</goal_round>", "source": {"kind": "goal_round", "round": 2}},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "screenshot", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}], "_attached_by": "c1"},
            {"role": "user", "content": "System note: this turn is running long.", "_attached_by": "todo_nudge"},
        ]
    )
    assert ControlsMixin._goal_round_of_last_turn(holder) == 2
    holder.messages.append({"role": "user", "content": "thôi dừng lại"})
    assert ControlsMixin._goal_round_of_last_turn(holder) == 0


def _shell(call_id: str, command: str, output: str, code: int) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "function": {"name": "run_shell", "arguments": json.dumps({"command": command})}}]},
        {"role": "tool", "tool_call_id": call_id, "content": f"{output}\n[exit {code}; cwd D:\ws]"},
    ]


def _edit(call_id: str) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "function": {"name": "replace_in_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": call_id, "content": "replaced"},
    ]


def test_done_needs_proof_from_the_transcript_not_the_models_word(tmp_path, monkeypatch):
    """The goal loop runs with nobody watching; the model marking its own work done is
    the model grading itself (lecture 13). `done` is refused until each acceptance check
    passed with no edit after it — read from the transcript by code, never run here."""
    store = _store(tmp_path, monkeypatch)
    transcript: list[dict] = []
    tool = goal_tools(store, messages_of=lambda: transcript)[0]
    assert tool(action="set", objective="make the suite green", checks=["python -m pytest -q", " python  -m pytest -q "])["ok"]
    assert store.load().checks == ["python -m pytest -q"]  # normalized, deduped

    refused = tool(action="done", reason="all green")
    assert "done refused" in refused["error"] and "has not been run" in refused["error"]
    assert store.load().phase == "active"  # refused, not recorded

    transcript += _shell("c1", "python -m pytest -q", "1 failed, 9 passed", 1)
    assert "failed on its last run" in tool(action="done")["error"]

    transcript += _edit("e1") + _shell("c2", "python  -m pytest -q", "10 passed", 0)
    done = tool(action="done", reason="green")
    assert done.get("ok") is True and store.load().phase == "complete"


def test_an_edit_after_the_passing_run_reopens_the_gap(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    transcript = _shell("c1", "npm test", "ok", 0) + _edit("e1")
    tool = goal_tools(store, messages_of=lambda: transcript)[0]
    tool(action="set", objective="x", checks=["npm test"])
    assert "1 file edit(s) came after it" in tool(action="done")["error"]


def test_a_red_last_check_blocks_done_even_without_declared_checks(tmp_path, monkeypatch):
    """No acceptance list: the newest check in the session, left failing with nothing
    changed since, is the known state of the work. Without a transcript reader (a bare
    caller) `done` is taken at its word, as before."""
    store = _store(tmp_path, monkeypatch)
    transcript = _shell("c1", "ruff check .", "2 errors", 1)
    tool = goal_tools(store, messages_of=lambda: transcript)[0]
    tool(action="set", objective="clean lint")
    assert "the last check, `ruff check .`, failed" in tool(action="done")["error"]

    transcript += _edit("e1")  # something changed: the red run is no longer the state
    assert tool(action="done").get("ok") is True

    bare = _store(tmp_path / "bare", monkeypatch)
    _call(bare, action="set", objective="y")
    assert _call(bare, action="done").get("ok") is True


def test_the_round_prompt_names_the_acceptance_checks():
    from harness.loop.goals import Goal, round_prompt

    prompt = round_prompt(Goal(objective="ship it", checks=["pytest -q", "ruff check ."]), 2)
    assert "Acceptance checks" in prompt and "`pytest -q`; `ruff check .`" in prompt
    assert "Acceptance checks" not in round_prompt(Goal(objective="write the memo"), 1)


def _verify(call_id: str, verdict: str) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "function": {"name": "task", "arguments": json.dumps({"description": "check it", "prompt": "…", "subagent_type": "verify"})}}]},
        {"role": "tool", "tool_call_id": call_id, "content": f'<task id="t1" state="completed"><task_result>VERDICT: {verdict}\nFindings: none\n</task_result></task>'},
    ]


def test_a_goal_without_checks_needs_an_independent_verdict(tmp_path, monkeypatch):
    """No acceptance checks declared: `done` needs the verify subagent's PASS with no
    edit after it — a fresh context judging work it did not write (lecture 13). Where no
    verifier can be asked, the rule steps aside instead of blocking the goal forever."""
    store = _store(tmp_path, monkeypatch)
    transcript: list[dict] = []
    tool = goal_tools(store, messages_of=lambda: transcript, verifier_of=lambda: True)[0]
    tool(action="set", objective="write the migration guide")
    assert "no independent verdict exists" in tool(action="done")["error"]

    transcript += _verify("v1", "FAIL")
    assert "last verdict was FAIL" in tool(action="done")["error"]

    transcript += _edit("e1") + _verify("v2", "PASS") + _edit("e2")
    assert "1 file edit(s) came after it" in tool(action="done")["error"]

    transcript += _verify("v3", "PASS")
    assert tool(action="done").get("ok") is True

    # A verdict from some other subagent type is not independent verification.
    other = _store(tmp_path / "other", monkeypatch)
    lookalike = [dict(m) for m in _verify("g1", "PASS")]
    lookalike[0]["tool_calls"][0]["function"]["arguments"] = json.dumps({"subagent_type": "general", "prompt": "…", "description": "x"})
    g = goal_tools(other, messages_of=lambda: lookalike, verifier_of=lambda: True)[0]
    g(action="set", objective="y")
    assert "no independent verdict" in g(action="done")["error"]

    # No verifier available (no task tool, or verify switched off): not required.
    free = _store(tmp_path / "free", monkeypatch)
    f = goal_tools(free, messages_of=lambda: [], verifier_of=lambda: False)[0]
    f(action="set", objective="z")
    assert f(action="done").get("ok") is True


def test_a_piped_run_is_named_but_never_counts_as_the_check(tmp_path, monkeypatch):
    """Live: the model ran `python -m pytest -q 2>&1 | Select-Object -Last 20` and was told
    the check never ran. A pipe can hide the exit code (POSIX reports the last stage), so
    the variant still does not count — the refusal now says what happened."""
    store = _store(tmp_path, monkeypatch)
    transcript = _shell("c1", "python -m pytest -q 2>&1 | tail -n 20", "2 passed", 0)
    tool = goal_tools(store, messages_of=lambda: transcript)[0]
    tool(action="set", objective="green", checks=["python -m pytest -q"])
    error = tool(action="done")["error"]
    assert "only ran as `python -m pytest -q 2>&1 | tail -n 20`" in error and "exactly as declared" in error


def test_each_acceptance_check_reports_its_state_for_the_goal_strip():
    """GUI UX review, Goal: the strip showed `2/256` and the objective, while the goal's real
    progress - which checks pass - lived only in the model's prompt. The strip gets the same
    reading the done-gate makes, one state per declared command."""
    from harness.loop.goal_proof import check_states

    goal = Goal(objective="ship it", checks=["pytest -q", "ruff check .", "npm test", "tsc"])
    transcript = [
        *_shell("c1", "pytest -q", "3 passed", 0),
        *_shell("c2", "ruff check .", "Found 2 errors.", 1),
        *_shell("c3", "tsc", "ok", 0),
        *_edit("c4"),
    ]
    assert check_states(goal, transcript) == [
        {"command": "pytest -q", "state": "stale"},  # passed, then a file changed
        {"command": "ruff check .", "state": "failed"},
        {"command": "npm test", "state": "not_run"},
        {"command": "tsc", "state": "stale"},
    ]
    assert check_states(goal, [*transcript, *_shell("c5", "pytest -q", "3 passed", 0)])[0] == {
        "command": "pytest -q",
        "state": "passed",
    }
    assert check_states(Goal(objective="no checks"), transcript) == []


def test_the_goal_strip_view_carries_the_check_states_and_the_tool_view_does_not():
    import types

    from harness.loop.goals import goal_view
    from harness.interface.server.manager.goal_driver import GoalDriverMixin

    goal = Goal(objective="ship it", checks=["pytest -q"])
    holder = types.SimpleNamespace(
        session_messages=lambda _sid: _shell("c1", "pytest -q", "1 passed", 0),
        _edit_exempt_roots=lambda _sid: [],
    )
    surface = GoalDriverMixin._goal_surface_view(holder, "s1", goal, "armed")
    assert surface["check_status"] == [{"command": "pytest -q", "state": "passed"}]
    assert "check_status" not in goal_view(goal, "armed")  # model-facing view unchanged
