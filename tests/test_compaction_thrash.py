"""Compaction is the one expensive thing the loop does on its own initiative, so it has to
remember its own attempts.

`_compaction_due()` is a pure reading of pressure, which meant a session compaction could
not shrink re-attempted on EVERY iteration: a summarizer round-trip per step, forever, with
no signal to anyone that it was not working. Worse, the policy had no way to tell "saved
half the window" from "saved nothing" â€” `_assert_smaller` refuses only a compaction that
GROWS the view, so a one-token improvement counted as a success.

What is pinned here: the gate's own arithmetic, then the two failures that cost real money â€”
a summarizer that keeps failing, and a summarizer that keeps succeeding at nothing â€” each
measured by counting provider calls, because "it did not try" is the property that matters
and a notice is not evidence of it.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from summary_fixtures import valid_summary

from harness.context import compaction as C
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.tools import ToolRegistry


class _Summarizer(ProviderClient):
    """Counts summarizer calls, and can be told to fail or to answer with nothing useful."""

    def __init__(self, *, summary=None, fail=False):
        # Valid eight-section reply by default: `build_state` re-asks once and then raises
        # on a missing section, so a one-word fake is not a summary any more.
        self.summary = summary if summary is not None else valid_summary("x")
        self.fail = fail
        self.summary_calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        if any(
            "Produce ALL of the following sections" in str(m.get("content", ""))
            or "condense" in str(m.get("content", ""))[:200]
            for m in messages or []
        ):
            self.summary_calls += 1
            if self.fail:
                raise RuntimeError("summarizer down")
            return AssistantTurn(text=self.summary, finish_reason="stop")
        return AssistantTurn(text="done", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider, *, messages=None, cap=400, window=100_000):
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.compaction_settings = lambda: {
        # `cap` is the desired trigger; the new rule derives it from the window, so the
        # buffer is `window âˆ’ cap` (max() then picks the buffer when it dwarfs output).
        # Keep a quarter of the trigger verbatim, mirroring the old engine fraction.
        "context_window": window,
        "buffer_tokens": max(0, window - cap),
        "keep_tokens": max(1, cap // 4),
    }
    return engine


def _history(steps: int, *, chars: int = 4_000) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "be helpful"}]
    msgs.append({"role": "user", "content": "audit the repo"})
    for i in range(steps):
        msgs.append({"role": "user", "content": f"step {i}: " + ("ask " * 200)})
        msgs.append(
            {"role": "assistant", "content": "working on it " + ("y" * chars)}
        )
    return msgs


# -- the gate, on its own ---------------------------------------------------------


def test_with_no_history_compaction_is_allowed():
    assert C.compaction_gate(None, now=1_000.0) == (True, "ok")


def test_a_state_that_never_tried_is_allowed():
    state = C.CompactionState(boundary_index=1, summary_text="s", working_state="")
    assert C.compaction_gate(state, now=1_000.0) == (True, "ok")


def _failed_attempt(at: float) -> C.CompactionState:
    """A state whose last attempt could not summarize (no trim fallback exists)."""
    return C.CompactionState(
        boundary_index=1,
        summary_text="s",
        working_state="",
        last_attempt_at=at,
        reason="failed",
    )


def test_a_recent_failure_holds_compaction_back():
    allowed, why = C.compaction_gate(_failed_attempt(1_000.0), now=1_000.0 + 5)
    assert (allowed, why) == (False, "cooldown")


def test_a_failure_long_ago_is_forgotten():
    allowed, _ = C.compaction_gate(
        _failed_attempt(1_000.0), now=1_000.0 + C.COMPACTION_COOLDOWN_SECONDS + 1
    )
    assert allowed is True


def test_a_successful_prune_is_not_a_policy_failure():
    """`reason` says what the last attempt did. `failed` is the one the gate waits for;
    a prune (or a prepared or summarized state) must never be read as a failure."""
    healthy = C.CompactionState(
        boundary_index=1,
        summary_text="s",
        working_state="",
        last_attempt_at=1_000.0,
        reason="pruned",
    )
    assert C.compaction_gate(healthy, now=1_000.0 + 1) == (True, "ok")


def test_one_unproductive_pass_is_not_yet_a_reason_to_stop():
    """One awkward span can genuinely defeat compaction once. Two in a row means the tool is
    not the problem."""
    state = C.CompactionState(
        boundary_index=1,
        summary_text="s",
        working_state="",
        last_attempt_at=1_000.0,
        reason="summarized",
        ineffective_streak=1,
    )
    assert C.compaction_gate(state, now=1_000.0 + 1) == (True, "ok")


def test_two_unproductive_passes_in_a_row_start_a_cooldown():
    state = C.CompactionState(
        boundary_index=1,
        summary_text="s",
        working_state="",
        last_attempt_at=1_000.0,
        reason="summarized",
        ineffective_streak=C.INEFFECTIVE_STREAK_LIMIT,
    )
    assert C.compaction_gate(state, now=1_000.0 + 1) == (False, "cooldown")


def test_a_productive_pass_is_never_held_back():
    """The streak is the only thing that arms the gate for a successful attempt, so a
    session that keeps compacting usefully keeps compacting."""
    state = C.CompactionState(
        boundary_index=1,
        summary_text="s",
        working_state="",
        last_attempt_at=1_000.0,
        reason="summarized",
        ineffective_streak=0,
        saved_pct=0.7,
    )
    assert C.compaction_gate(state, now=1_000.0 + 1) == (True, "ok")


# -- the failures that cost money -------------------------------------------------


def test_a_failing_summarizer_is_not_retried_every_iteration(tmp_path):
    """The expensive one. Before the cooldown this attempted on every iteration of every
    turn — two summarizer calls per step, each one raising.

    A failure adopts nothing (no trim fallback, study round 29), so the cooldown comes
    from the engine clock; it still holds the next automatic attempt back. Waiting for
    `INEFFECTIVE_STREAK_LIMIT` failed attempts would spend four summarizer calls before
    resting a summarizer that is simply down.
    """
    provider = _Summarizer(fail=True)
    engine = _engine(tmp_path, provider, messages=_history(20))
    engine._last_context_tokens = None

    async def _go():
        first = await engine._compact_now()
        calls_after_first = provider.summary_calls
        second = await engine._compact_now()
        return first, second, calls_after_first, provider.summary_calls

    first, second, calls_after_first, calls_at_end = asyncio.run(_go())
    # The first attempt does try (twice: the unconditional retry), then gives up.
    assert calls_after_first == 2, "one try plus its retry"
    assert first is None, "nothing is adopted on failure"
    assert engine.compaction_state is None
    assert engine._last_compaction_error.startswith("RuntimeError")
    # The second attempt inside the cooldown touches the provider ZERO times. This is the
    # assertion that matters: nothing about a notice would have shown it.
    assert second is None
    assert calls_at_end == calls_after_first, "held back, not re-attempted"


def test_an_unproductive_streak_arms_the_cooldown(tmp_path):
    """The wiring from adoption to gate: `_adopt_compaction` is what counts a streak, and
    the gate is what acts on it. Pinned through the engine because the arithmetic is
    worthless if nothing fills it in â€” which was the state of `created_at` before this."""
    engine = _engine(tmp_path, _Summarizer(), messages=_history(4))
    engine.compaction_state = C.CompactionState(
        boundary_index=1, summary_text="s", working_state=""
    )

    for _ in range(C.INEFFECTIVE_STREAK_LIMIT):
        engine._adopt_compaction(
            C.CompactionState(
                boundary_index=1, summary_text="s", working_state="", saved_pct=0.0
            ),
            "summarized",
            "Context compacted â€” earlier turns were summarized",
        )

    state = engine.compaction_state
    assert state.ineffective_streak == C.INEFFECTIVE_STREAK_LIMIT
    assert C.compaction_gate(state, now=time.time()) == (False, "cooldown")


def test_a_second_pass_with_nothing_new_to_compact_costs_no_model_call(tmp_path):
    """The other way an attempt can be free: once the boundary cannot advance, `build_state`
    returns before any provider call. Worth pinning because it is the reason a session that
    keeps hitting the trigger does not keep paying â€” the cooldown is for the cases where
    the boundary CAN advance and the result is not worth it."""
    provider = _Summarizer(summary=valid_summary("short summary"))
    engine = _engine(tmp_path, provider, messages=_history(30), cap=50_000)

    async def _go():
        await engine._compact_now()
        calls = provider.summary_calls
        again = await engine._compact_now()
        return calls, again, provider.summary_calls

    calls, again, after = asyncio.run(_go())
    assert calls >= 1
    assert again is None
    assert after == calls, "nothing new to compact, so nothing was sent"


def test_force_ignores_the_cooldown(tmp_path):
    """`force` means the provider already refused the request on window size. Declining to
    try there does not save money â€” it guarantees the turn dies."""
    provider = _Summarizer(fail=True)
    engine = _engine(tmp_path, provider, messages=_history(20))

    async def _go():
        await engine._compact_now()
        calls = provider.summary_calls
        notice = await engine._compact_now(force=True)
        return calls, notice, provider.summary_calls

    calls, notice, after = asyncio.run(_go())
    assert notice is None  # no trim fallback: the attempt itself is what matters
    assert after > calls, "the overflow path must still try"


def test_a_productive_compaction_reports_what_it_saved(tmp_path):
    provider = _Summarizer(summary=valid_summary("short summary"))
    engine = _engine(tmp_path, provider, messages=_history(30), cap=50_000)

    notice = asyncio.run(engine._compact_now())
    assert notice is not None and notice.reason == "summarized"
    assert notice.saved_tokens > 0
    assert notice.saved_pct >= C.MIN_SAVINGS_PCT
    assert notice.now_tokens > 0
    # The numbers a surface reads, and they agree with the sentence's claim.
    assert notice.numbers["saved_tokens"] == notice.saved_tokens
    assert engine.compaction_state.saved_tokens == notice.saved_tokens
    assert engine.compaction_state.ineffective_streak == 0


def test_the_event_carries_the_numbers(tmp_path):
    """A reader can say what compaction bought, not just that it happened."""
    provider = _Summarizer(summary=valid_summary("short summary"))
    engine = _engine(tmp_path, provider, messages=_history(30), cap=50_000)
    engine._last_context_tokens = None

    async def _go():
        events = [event async for event in engine.run("go")]
        return [e for e in events if e.type.value == "compacted"]

    compacted = asyncio.run(_go())
    if compacted:  # the turn may not reach the trigger; the unit above covers that path
        payload = compacted[0].data
        assert payload["text"]
        assert set(payload) >= {"reason", "saved_tokens", "saved_pct", "now_tokens"}


# -- the record on disk -----------------------------------------------------------


def test_the_attempt_record_round_trips(tmp_path):
    state = C.CompactionState(
        boundary_index=3,
        summary_text="s",
        working_state="w",
        last_attempt_at=1234.5,
        attempts=7,
        reason="pruned",
        saved_tokens=900,
        saved_pct=0.42,
        ineffective_streak=1,
    )
    back = C.CompactionState.from_dict(state.as_dict())
    assert back is not None
    assert (back.attempts, back.reason, back.saved_tokens) == (7, "pruned", 900)
    assert back.saved_pct == pytest.approx(0.42)
    assert (back.ineffective_streak, back.last_attempt_at) == (1, 1234.5)


def test_a_state_written_before_these_fields_existed_reads_as_never_tried():
    """The session on disk is the one that most needs the new policy, so an old record must
    load rather than fail â€” and it must not arrive pretending compaction already tried."""
    old = {
        "boundary_index": 2,
        "summary_text": "s",
        "working_state": "w",
        "created_at": 1.0,
    }
    state = C.CompactionState.from_dict(old)
    assert state is not None
    assert (state.attempts, state.ineffective_streak, state.last_attempt_at) == (0, 0, 0.0)
    assert state.reason == ""
    assert C.compaction_gate(state, now=999.0) == (True, "ok")


# -- the boundary tripwire --------------------------------------------------------


def test_a_boundary_landing_on_a_tool_result_is_reported_not_fatal(caplog):
    """Selection cannot produce this â€” `_turn_starts` and `trim_state` take only `user` and
    `assistant` roles â€” so reaching it means a boundary was written by something else. It
    runs on every provider feed, so it warns and sends the request rather than refusing to:
    a visible defect must not become a dead session."""
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "ask"},
        {"role": "tool", "tool_call_id": "c1", "content": "orphan"},
    ]
    state = C.CompactionState(boundary_index=2, summary_text="s", working_state="")

    with caplog.at_level(logging.WARNING):
        outbound = C.apply_to_outbound(messages, state)

    assert any("lands on a tool result" in record.message for record in caplog.records)
    # Still a legal request: the block, then the tail as the state described it.
    assert outbound[0]["role"] == "system"
    assert outbound[1]["role"] == "user" and "compacted-history" in outbound[1]["content"]
    assert outbound[-1]["content"] == "orphan"
