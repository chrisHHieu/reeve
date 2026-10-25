"""The engine half of items 6.1–6.6: what the pressure signal counts, which lever runs
first, and what a bigger model may take back.

Companion to `test_compaction_pressure.py`, which pins the pure functions. Everything here
needs the engine because it is about ORDER — prune before summarize, measure after append,
offer only when the offer can be honoured.
"""

from __future__ import annotations

import asyncio
import json

from summary_fixtures import valid_summary

from harness.context import compaction as C
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import TokenUsage
from harness.tools import ToolRegistry


class _Provider(ProviderClient):
    """Scripted turns; a summarizer call is answered out of band and counted."""

    def __init__(self, turns, *, summary=None):
        summary = summary if summary is not None else valid_summary("do the thing")
        self._turns = list(turns)
        self.summary = summary
        self.summary_calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        if any(
            "Produce ALL of the following sections" in str(m.get("content", ""))
            or "condense" in str(m.get("content", ""))[:200]
            for m in messages or []
        ):
            self.summary_calls += 1
            return AssistantTurn(text=self.summary, finish_reason="stop")
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, provider, *, messages=None, window=1_000):
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.compaction_settings = lambda: {
        "context_window": window,
        "buffer_tokens": 0,
        "keep_tokens": max(1, window // 4),
    }
    return engine


def _shell_history(steps: int, *, chars: int = 20_000) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": "be helpful"}]
    msgs.append({"role": "user", "content": "audit the repo"})
    for i in range(steps):
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "function": {
                            "name": "run_shell",
                            "arguments": json.dumps({"command": "ls"}),
                        },
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * chars})
    return msgs


# -- 6.2 the signal was a whole step stale -------------------------------------------


def test_the_signal_counts_what_arrived_after_the_last_request(tmp_path):
    """`_last_context_tokens` is the provider's number for the LAST request. Everything
    appended since — the turn it produced and this step's tool results — occupies the
    window too, and reading a step behind is how a single large result sails past the
    threshold and overflows on the next call."""
    engine = _engine(tmp_path, _Provider([]))
    engine._last_context_tokens = 1_000
    engine._context_measured_at = len(engine.messages)
    assert engine._context_signal() == 1_000

    engine.messages.append({"role": "tool", "tool_call_id": "c1", "content": "x" * 40_000})
    signal = engine._context_signal()
    assert signal > 1_000
    assert signal >= 1_000 + 40_000 // 4 * 0.9, f"delta barely counted: {signal}"


def test_the_estimate_path_is_unchanged_when_usage_is_never_reported(tmp_path):
    engine = _engine(tmp_path, _Provider([]), messages=_shell_history(2))
    assert engine._last_context_tokens is None
    assert engine._context_signal() == C.estimate_tokens(engine._outbound_messages())


def test_the_stamp_is_taken_before_the_assistant_message_lands(tmp_path):
    """Otherwise the turn the provider just billed for would be counted twice."""
    provider = _Provider(
        [AssistantTurn(text="done", finish_reason="stop", usage=TokenUsage(input=500, output=5))]
    )
    engine = _engine(tmp_path, provider, window=10**9)
    asyncio.run(_drain(engine))

    assert engine._last_context_tokens == 500
    after = engine.messages[engine._context_measured_at :]
    assert any(m.get("role") == "assistant" for m in after), (
        "the assistant turn must be part of the delta, not folded into the anchor"
    )


async def _drain(engine, text="go"):
    return [e async for e in engine.run(text)]


# -- 6.1 the cheap lever runs first ---------------------------------------------------


def test_pruning_alone_can_spare_the_summarizer(tmp_path):
    """The whole point of the item. Stale shell output is shortened, the window comes back
    under the trigger, and no model is asked to condense anything."""
    messages = _shell_history(12)
    trigger = C.estimate_tokens(messages) - 100  # just over
    engine = _engine(tmp_path, _Provider([]), messages=messages)

    notice = engine._prune_now(trigger)

    assert notice is not None and "shortened" in notice.text
    # And it says which lever it was and what it bought, which is what lets a policy tell
    # "this is working" from "this is not" — see `tests/test_compaction_thrash.py`.
    assert notice.reason == "pruned"
    assert notice.saved_tokens > 0 and notice.saved_pct > 0
    assert notice.now_tokens < trigger
    assert engine.compaction_state is not None
    assert engine.compaction_state.boundary_index == 0, "nothing was summarized"
    assert engine.compaction_state.pruned_upto > 0
    assert engine.compaction_state.reason == "pruned"
    assert engine.compaction_state.last_attempt_at > 0
    assert C.estimate_tokens(engine._outbound_messages()) < trigger


def test_the_prune_is_judged_in_the_triggers_unit(tmp_path):
    """The trigger is in the provider's unit, so "did the prune alone get under it" must
    be too: the view is priced with the bias the provider's last count showed. Read
    raw, a prune that was enough went to the summarizer anyway."""
    messages = _shell_history(12)
    trigger = int(C.estimate_tokens(messages) * 0.5) - 100  # just over, in the provider's unit

    uncalibrated = _engine(tmp_path / "raw", _Provider([]), messages=list(messages))
    assert uncalibrated._prune_now(trigger) is None  # read raw, the prune looks too small

    engine = _engine(tmp_path / "calibrated", _Provider([]), messages=list(messages))
    engine._estimate_bias = 0.5  # the provider counts half of what the estimator does
    notice = engine._prune_now(trigger)

    assert notice is not None and notice.reason == "pruned"
    assert notice.now_tokens < trigger
    assert engine.compaction_state.boundary_index == 0, "nothing was summarized"


def test_a_second_pass_with_nothing_newly_aged_does_nothing(tmp_path):
    """Pruning must not rewrite the same messages on every check: a provider matches a
    cached prefix byte for byte, so a rewrite that changes nothing still costs the cache."""
    messages = _shell_history(12)
    trigger = C.estimate_tokens(messages) - 100
    engine = _engine(tmp_path, _Provider([]), messages=messages)

    assert engine._prune_now(trigger) is not None, "the first pass must actually prune"
    first = engine.compaction_state
    assert first is not None and first.pruned_upto > 0

    assert engine._prune_now(trigger) is None
    assert engine.compaction_state is first, "the state object must not even be rebuilt"


def test_the_prune_point_only_moves_forward(tmp_path):
    engine = _engine(tmp_path, _Provider([]), messages=_shell_history(12))
    engine.compaction_state = C.CompactionState(
        boundary_index=0, summary_text="", working_state="", pruned_upto=10**6
    )
    assert engine._prune_now(1_000) is None
    assert engine.compaction_state.pruned_upto == 10**6


def test_pruning_that_is_not_enough_still_counts_and_summarizes(tmp_path):
    """A partial reduction is real. Keeping it means the summarizer that runs next has
    less to read — throwing it away to 'start clean' would pay for the same tokens twice.
    """
    messages = _shell_history(12)
    engine = _engine(tmp_path, _Provider([]), messages=messages)

    assert engine._prune_now(10) is None  # a trigger nothing could reach
    assert engine.compaction_state is not None
    assert engine.compaction_state.pruned_upto > 0


def test_a_session_with_no_large_tool_output_is_left_alone(tmp_path):
    engine = _engine(tmp_path, _Provider([]), messages=_shell_history(12, chars=50))
    assert engine._prune_now(100) is None
    assert engine.compaction_state is None, "no rewrite means no state to persist"


def test_the_overflow_path_tries_the_cheap_lever_first(tmp_path):
    """`force` used to skip pruning and go straight to the summarizer. That had it
    backwards: the summarizer's replay IS the over-window conversation the provider just
    refused, so the expensive call is the one most likely to fail, while pruning is the
    only lever that can make it fit. The cost the old order was avoiding — a prune that
    reports success and still overflows — only materializes when the estimate is wrong,
    and the calibration plus the overflow retry loop are what cover that."""
    messages = _shell_history(12)
    engine = _engine(tmp_path, _Provider([]), messages=messages)
    calls = []
    engine._prune_now = lambda trigger: calls.append(trigger)  # type: ignore[assignment]

    asyncio.run(engine._compact_now(force=True))
    assert calls == [engine._history_trigger_tokens()]


# -- 6.5 a bigger window is offered at READ time, never written -----------------------


def test_switching_models_writes_nothing_to_the_transcript(tmp_path):
    """There used to be an offer written on every switch, and a session that changed model
    five times carried five identical dead notices (owner removal 2026-09-10). Switching
    models as often as you like adds NOTHING to the transcript.

    What survives is the part that was never in question: compaction is a projection, the
    canonical list is untouched, and the switch itself is still marked."""
    engine = _engine(tmp_path, _Provider([]), messages=_shell_history(3, chars=100))
    engine.compaction_state = C.CompactionState(
        boundary_index=4, summary_text="earlier work", working_state=""
    )
    engine.compaction_settings = lambda: {"context_window": 1_000_000}

    engine.switch_model("openrouter:big/model")
    engine.switch_model("openrouter:small/model")

    kinds = [m.get("kind") for m in engine.messages if m.get("role") == "notice"]
    assert kinds == ["model_switch", "model_switch"], "switches are marked; nothing else"
    # A bigger window does not silently un-compact: the view stays projected.
    assert engine.compaction_state is not None
    assert "<compacted-history>" in json.dumps(engine._outbound_messages())


# -- 6.4 the switch that was never a switch -------------------------------------------


def test_compaction_has_no_off_switch_pretending_to_be_one(tmp_path):
    """`enabled: False` used to short-circuit the trigger, and nothing in the product —
    Settings, prefs, or a test — ever set it. A gate that cannot be reached is not a
    feature; it is a claim the code makes about itself and cannot keep."""
    engine = _engine(tmp_path, _Provider([]), messages=_shell_history(12), window=100)
    engine.compaction_settings = lambda: {
        "context_window": 100,
        "buffer_tokens": 0,
        "enabled": False,
    }
    assert engine._compaction_due() is True
