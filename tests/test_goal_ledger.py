"""The goal ledger: each finished round is recorded from what the harness
measured, and the harness itself ends a goal that is proven, out of budget, or stalled.

Before it, a goal ended only on the model's own `done`/`blocked` or the round cap (256 by
default): a round that moved nothing opened the next one, and a finished goal whose
model never said `done` kept re-reading the whole session every round.
"""

from __future__ import annotations

import asyncio
import json

from harness.loop.goal_ledger import BUDGET, COMPLETE, CONTINUE, LEDGER_KEEP, NO_PROGRESS, STALL_ROUNDS, RoundFacts, call_signatures, progress_lines, settle_round, standing_of
from harness.loop.goal_proof import proven
from harness.loop.goals import ACT_DISARMED, Goal, GoalStore, PHASE_ACTIVE, PHASE_BLOCKED, PHASE_COMPLETE, goal_tools, round_prompt
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.interface.server.manager import SessionManager


def _shell(call_id: str, command: str, output: str, code: int) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "function": {"name": "run_shell", "arguments": json.dumps({"command": command})}}]},
        {"role": "tool", "tool_call_id": call_id, "content": f"{output}\n[exit {code}; cwd /ws]"},
    ]


def _verify(call_id: str, verdict: str) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "function": {"name": "task", "arguments": json.dumps({"prompt": "check", "subagent_type": "verify"})}}]},
        {"role": "tool", "tool_call_id": call_id, "content": f"VERDICT: {verdict}\n"},
    ]


def _facts(round_no: int, *, changed=(), calls=(), standing="", outcome="completed", tokens=10):
    return RoundFacts(
        round_no=round_no,
        outcome=outcome,
        tokens=tokens,
        changed=list(changed),
        standing=standing,
        calls=tuple(calls),
    )


# -- the ledger's rules -------------------------------------------------------------


def test_rounds_that_move_nothing_stop_the_goal_after_the_stall_limit():
    goal = Goal(objective="x")
    for n in range(1, STALL_ROUNDS):
        assert settle_round(goal, _facts(n), proven=False) == CONTINUE
    assert settle_round(goal, _facts(STALL_ROUNDS), proven=False) == NO_PROGRESS
    assert goal.stalled_rounds == STALL_ROUNDS
    assert [e["moved"] for e in goal.ledger] == [False] * STALL_ROUNDS


def test_re_running_the_same_red_check_is_not_progress_but_any_new_call_is():
    goal = Goal(objective="x")
    red = "`pytest -q` failed"
    settle_round(goal, _facts(1, calls=["run_shell:aaa"], standing=red), proven=False)
    assert goal.stalled_rounds == 0  # the first run moved the standing
    # The loop the stall rule exists for: the same command, the same red result.
    settle_round(goal, _facts(2, calls=["run_shell:aaa"], standing=red), proven=False)
    assert goal.stalled_rounds == 1
    # A round spent investigating is progress: a read the last round did not make.
    settle_round(goal, _facts(3, calls=["run_shell:aaa", "read_file:bbb"], standing=red), proven=False)
    assert goal.stalled_rounds == 0
    settle_round(goal, _facts(4, standing=red), proven=False)  # no tool call at all
    assert goal.stalled_rounds == 1
    settle_round(goal, _facts(5, changed=["/ws/app.py"], standing=red), proven=False)
    assert goal.stalled_rounds == 0  # a file changed
    settle_round(goal, _facts(6, standing="`pytest -q` passed"), proven=False)
    assert goal.stalled_rounds == 0  # the check moved


def test_a_cut_short_round_charges_spend_but_never_counts_a_stall():
    goal = Goal(objective="x", stalled_rounds=2)
    assert settle_round(goal, _facts(1, outcome="interrupted", tokens=50), proven=True) == CONTINUE
    assert settle_round(goal, _facts(2, outcome="error", tokens=50), proven=False) == CONTINUE
    assert goal.stalled_rounds == 2 and goal.tokens_used == 100
    assert "moved" not in goal.ledger[0] and "moved" not in goal.ledger[1]


def test_call_signatures_cover_only_the_newest_round_and_repeat_for_the_same_call():
    def call(cid, name, args):
        return {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "function": {"name": name, "arguments": args}}]}

    round_msg = lambda n: {"role": "user", "content": "<goal_round>", "source": {"kind": "goal_round", "round": n}}  # noqa: E731
    transcript = [
        round_msg(1), call("a", "read_file", '{"path": "a.py"}'),
        round_msg(2), call("b", "read_file", '{"path":  "a.py"}'), call("c", "grep", '{"pattern": "x"}'),
    ]
    sigs = call_signatures(transcript)
    assert len(sigs) == 2 and sigs[0].startswith("read_file:") and sigs[1].startswith("grep:")
    # Whitespace does not make a repeated call new.
    assert sigs[0] == call_signatures([round_msg(1), call("z", "read_file", '{"path": "a.py"}')])[0]


def test_proof_wins_over_budget_and_budget_over_the_stall():
    goal = Goal(objective="x", token_budget=100, stalled_rounds=STALL_ROUNDS - 1)
    assert settle_round(goal, _facts(1, tokens=500), proven=True) == COMPLETE
    goal = Goal(objective="x", token_budget=100, stalled_rounds=STALL_ROUNDS - 1)
    assert settle_round(goal, _facts(1, tokens=500), proven=False) == BUDGET
    goal = Goal(objective="x", token_budget=0, tokens_used=10**12)
    assert settle_round(goal, _facts(1, changed=["a"]), proven=False) == CONTINUE  # 0 = no budget


def test_the_ledger_is_bounded_and_survives_the_goal_file(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    store = GoalStore.for_session("ledger-file")
    goal = Goal(objective="x", token_budget=5000)
    for n in range(1, LEDGER_KEEP + 5):
        settle_round(goal, _facts(n, changed=["/ws/a.py"]), proven=False)
    assert len(goal.ledger) == LEDGER_KEEP and goal.ledger[-1]["round"] == LEDGER_KEEP + 4
    store.save(goal)
    back = store.load()
    assert back.ledger == goal.ledger and back.tokens_used == goal.tokens_used
    assert back.token_budget == 5000


def test_the_round_prompt_carries_the_latest_progress_and_the_stall_warning():
    goal = Goal(objective="ship it", token_budget=2_000_000)
    assert progress_lines(goal) == ["Token budget: 0 of 2.0M used; the goal stops when it is spent."]
    assert "Progress recorded" not in round_prompt(Goal(objective="ship it"), 1)  # round one unchanged
    settle_round(goal, _facts(1, changed=["/ws/src/app.py", "/ws/src/b.py"], standing="`pytest -q` failed", tokens=400_000), proven=False)
    for n in (2, 3, 4):
        settle_round(goal, _facts(n, standing="`pytest -q` failed", tokens=1000), proven=False)
    prompt = round_prompt(goal, 5)
    # Only the newest three rounds, file names without their paths.
    assert "Round 1:" not in prompt
    assert "- Round 4: changed no file, no tool call; checks: `pytest -q` failed." in prompt
    assert "The last 3 round(s) made no new tool call" in prompt
    assert "Token budget: 403k of 2.0M used" in prompt
    goal2 = Goal(objective="x")
    settle_round(goal2, _facts(1, changed=["/ws/src/app.py", "/ws/src/b.py"]), proven=False)
    assert "changed 2 file(s) (app.py, b.py), no tool call" in round_prompt(goal2, 2)


def test_the_standing_reads_declared_checks_else_the_newest_check_and_verdict():
    transcript = _shell("c1", "pytest -q", "1 failed in 0.1s", 1) + _verify("v1", "FAIL")
    assert standing_of(Goal(objective="x", checks=["pytest -q", "ruff check ."]), transcript) == (
        "`pytest -q` failed; `ruff check .` not run"
    )
    assert standing_of(Goal(objective="x"), transcript) == "`pytest -q` failed; verify FAIL"
    assert standing_of(Goal(objective="x"), []) == ""


def test_proven_needs_positive_evidence_not_just_an_absence_of_gaps():
    # No checks, no verifier to ask: `done` has nothing to object to — and nothing proved it.
    assert proven(Goal(objective="x"), [], verifier=False) is False
    assert proven(Goal(objective="x"), _verify("v1", "PASS"), verifier=False) is True
    assert proven(Goal(objective="x"), _verify("v1", "FAIL"), verifier=True) is False
    checked = Goal(objective="x", checks=["pytest -q"])
    assert proven(checked, [], verifier=False) is False
    assert proven(checked, _shell("c1", "pytest -q", "3 passed in 0.1s", 0)) is True
    assert proven(checked, _shell("c1", "pytest -q", "1 failed in 0.1s", 1)) is False


def test_the_models_resume_clears_the_stall_and_refuses_a_spent_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    store = GoalStore.for_session("tool-resume")
    store.save(Goal(objective="x", phase=PHASE_BLOCKED, stalled_rounds=3, blocker={"code": "no-progress", "message": "m"}))
    tool = goal_tools(store)[0]
    assert tool(action="resume")["ok"] and store.load().stalled_rounds == 0
    store.save(Goal(objective="y", phase=PHASE_BLOCKED, token_budget=10, tokens_used=10))
    assert "token_budget" in tool(action="resume")["error"]
    assert tool(action="edit", token_budget=100)["ok"]
    assert tool(action="resume")["ok"] and store.load().token_budget == 100


# -- the driver ends the goal ---------------------------------------------------------


class Provider(ProviderClient):
    """Answers every turn with a plain completion: a round that does nothing."""

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="looked around", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _manager(tmp_path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    return SessionManager(workspace=tmp_path, provider=Provider())


def _armed(sid: str, **kwargs) -> GoalStore:
    store = GoalStore.for_session(sid)
    store.save(Goal(**kwargs))
    store.arm()
    return store


async def _wait_for(predicate, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return predicate()


def _notices(engine) -> list[str]:
    return [m["text"] for m in engine.messages if m.get("role") == "notice" and m.get("kind") == "goal"]


async def test_the_driver_blocks_a_goal_whose_rounds_move_nothing(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    sid = "ledger-stall"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = _armed(sid, objective="polish the memo", max_rounds=50)
    mgr._loop = asyncio.get_running_loop()

    mgr.mark_running(sid)
    mgr.mark_idle(sid)

    assert await _wait_for(lambda: store.load().phase == PHASE_BLOCKED)
    goal = store.load()
    # Stopped at the stall limit, not after 50 rounds.
    assert goal.rounds_started == STALL_ROUNDS
    assert goal.blocker["code"] == "no-progress"
    assert store.activation() == ACT_DISARMED
    assert len(goal.ledger) == STALL_ROUNDS and goal.tokens_used > 0
    rounds = [m["content"] for m in engine.messages if "<goal_round>" in str(m.get("content"))]
    assert "Progress recorded" not in rounds[0]
    assert "- Round 1: changed no file, no tool call; checks: no check has run." in rounds[1]
    assert "The last 2 round(s) made no new tool call" in rounds[2]
    assert any("rounds in a row made no new tool call" in text for text in _notices(engine))


async def test_the_driver_completes_a_goal_the_transcript_proves_without_another_round(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    sid = "ledger-proven"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    engine.messages.extend(_shell("c1", "python -m pytest -q", "12 passed in 0.3s", 0))
    store = _armed(sid, objective="green suite", max_rounds=50, checks=["python -m pytest -q"])
    mgr._loop = asyncio.get_running_loop()

    mgr.mark_running(sid)
    mgr.mark_idle(sid)

    assert await _wait_for(lambda: store.load().phase == PHASE_COMPLETE)
    goal = store.load()
    assert goal.rounds_started == 1  # the model never said done; the harness judged
    assert store.activation() == ACT_DISARMED
    assert any(text.startswith("Goal complete: every acceptance check passed") for text in _notices(engine))


async def test_a_red_check_keeps_the_goal_running(tmp_path, monkeypatch):
    """The counter-case: the same setup with the check failing is not proven."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "ledger-red"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    engine.messages.extend(_shell("c1", "python -m pytest -q", "1 failed in 0.3s", 1))
    store = _armed(sid, objective="green suite", max_rounds=50, checks=["python -m pytest -q"])
    mgr.broadcast_session = _noop  # type: ignore[assignment]

    await mgr._continue_goal(sid)

    goal = store.load()
    assert goal.phase == PHASE_ACTIVE and goal.rounds_started == 1
    assert goal.ledger[-1]["standing"] == "`python -m pytest -q` failed"


async def test_the_driver_blocks_a_goal_that_spent_its_token_budget(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    sid = "ledger-budget"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = _armed(sid, objective="x", max_rounds=50, token_budget=1)
    mgr.broadcast_session = _noop  # type: ignore[assignment]

    await mgr._continue_goal(sid)

    goal = store.load()
    assert goal.phase == PHASE_BLOCKED and goal.blocker["code"] == "budget"
    assert goal.tokens_used >= 1
    assert any("spent its token budget" in text for text in _notices(engine))


async def test_the_rounds_spend_is_read_before_another_turn_can_reset_it(tmp_path, monkeypatch):
    """A person's turn may claim the engine the moment the round goes idle, and turn
    start zeroes the spend counter. The ledger must still charge the round."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "ledger-race"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = _armed(sid, objective="x", max_rounds=50)
    mgr.broadcast_session = _noop  # type: ignore[assignment]
    real_idle = mgr.mark_idle

    def idle_then_a_person_types(session_id):
        real_idle(session_id)
        engine._turn_tokens = 0  # what the next turn's start does
        engine.changes.begin_turn()

    mgr.mark_idle = idle_then_a_person_types  # type: ignore[assignment]
    await mgr._continue_goal(sid)

    assert store.load().ledger[-1]["tokens"] > 0


async def test_the_humans_resume_clears_a_stall_and_refuses_a_spent_budget(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch)
    sid = "ledger-human"
    mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    mgr.broadcast_session = _noop  # type: ignore[assignment]
    mgr._schedule_goal_continue = lambda session_id: None  # type: ignore[assignment]
    store = GoalStore.for_session(sid)
    store.save(Goal(objective="x", phase=PHASE_BLOCKED, stalled_rounds=3))
    assert (await mgr.goal_action(sid, "resume"))["ok"]
    assert store.load().stalled_rounds == 0 and store.load().phase == PHASE_ACTIVE

    store.save(Goal(objective="y", phase=PHASE_BLOCKED, token_budget=10, tokens_used=10))
    refused = await mgr.goal_action(sid, "resume")
    assert refused["ok"] is False and "token budget" in refused["error"]
    assert (await mgr.goal_action(sid, "edit", token_budget=1000))["ok"]
    assert (await mgr.goal_action(sid, "resume"))["ok"]
    created = await mgr.goal_action(sid, "clear")
    assert created["ok"]
    made = await mgr.goal_action(sid, "set", objective="z", token_budget="5000")
    assert made["ok"] and made["goal"]["token_budget"] == 5000 and made["goal"]["tokens_used"] == 0


async def _noop(*args, **kwargs) -> None:
    return None


async def test_a_goal_waits_for_its_background_subagent_instead_of_opening_rounds(tmp_path, monkeypatch):
    """Session b3a8491e-9db: six rounds (about 3.5M tokens) opened only to say "the verify
    run is still going". While a background child of the session runs, no round opens and
    none is charged; the child's deregistration asks the driver again."""
    mgr = _manager(tmp_path, monkeypatch)
    sid = "ledger-wait"
    engine = mgr.get_engine(sid, agent="reeve", workspace=str(tmp_path))
    store = _armed(sid, objective="x", max_rounds=50)
    mgr.broadcast_session = _noop  # type: ignore[assignment]
    with mgr._subagent_lock:
        mgr._background_parents["child-1"] = sid

    await mgr._continue_goal(sid)
    assert store.load().rounds_started == 0
    assert not [m for m in engine.messages if "<goal_round>" in str(m.get("content"))]

    requested: list[str] = []
    mgr._schedule_goal_continue = requested.append  # type: ignore[assignment]

    async def finished() -> dict:
        return {"status": "completed", "report": "VERDICT: PASS"}

    async def delivered(*args, **kwargs):
        return None

    mgr._inject_background_result = delivered  # type: ignore[assignment]
    await mgr._settle_background(parent_id=sid, child_id="child-1", description="verify", run=finished())
    assert "child-1" not in mgr._background_parents
    assert requested == [sid]  # asked again once the registry is clear

    del mgr._schedule_goal_continue
    await mgr._continue_goal(sid)
    assert store.load().rounds_started == 1
