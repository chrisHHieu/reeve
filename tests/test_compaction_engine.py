"""Compaction inside a turn (`loop/engine`): when it is set off (by the estimate before a request,
by the usage a provider reports, by a provider refusing an overlong request), what the next request
is sent instead of the old turns, what happens when the summary cannot be written, and that the
state outlives a save. The window is forced tiny and the providers are scripted; no network.
"""

import asyncio
import json
import time
from dataclasses import replace

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.llm.providers.base import TokenUsage
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.events import EventType
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

from summary_fixtures import valid_summary
from harness.context.compaction import part as _part, render_parts as _render_parts

SUMMARY = valid_summary("keep building the report")


# A summarizer round-trip, whichever shape it takes. The instruction used to be
# `messages[0]` (its own system prompt); with the cache-reusing shape it is the LAST
# message, after the replayed conversation. Matching on the section list rather than a
# position works for both, and would have survived the change that broke this fake.
def _is_summarizer_call(messages):
    marker = "Produce ALL of the following sections"
    return any(marker in str(m.get("content", "")) for m in messages or [])


def _summarizer_body(messages):
    """Everything the summarizer was given to condense.

    Not `messages[1]`: in the cache-reusing shape the material is the replayed
    conversation spread across many messages, and the prior summary rides inside the
    compacted block rather than under a header of its own.
    """
    return chr(10).join(str(m.get("content", "")) for m in messages or [])


OVERFLOW = "Error 400: maximum context length is 100000 tokens, request used more"


class CompactingProvider(ProviderClient):
    """Plays the scripted turns to the conversation and answers every summarizer call on the side,
    so a compaction never uses up the script. Either can be made to fail a number of times."""

    def __init__(self, turns, *, summary=SUMMARY, summary_fails=0, main_overflows=0):
        self._turns = list(turns)
        self.summary = summary
        self.summary_fails, self.main_overflows = summary_fails, main_overflows
        self.summary_calls: list[dict] = []
        self.main_calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        if _is_summarizer_call(messages):
            self.summary_calls.append({"model": model, "messages": messages})
            if self.summary_fails:
                self.summary_fails = self.summary_fails - 1
                raise RuntimeError("the summary model is unavailable")
            return AssistantTurn(text=self.summary, finish_reason="stop")
        self.main_calls = self.main_calls + 1
        if self.main_overflows:
            self.main_overflows -= 1
            raise RuntimeError(OVERFLOW)
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def long_history(turns=8, bulk=1500):
    """A system prompt and `turns` exchanges, each answer `bulk` characters long."""
    history = [{"role": "system", "content": "be helpful"}]
    for n in range(turns):
        history += [{"role": "user", "content": f"request {n}", "ts": 1.0}, {"role": "assistant", "content": f"answer {n} " + "x" * bulk, "ts": 1.0}]
    return history


def make_engine(tmp_path, provider, *, messages=None, window=1_000):
    """The trigger is `window - max(output, buffer)`; with the buffer at 0 the window alone decides,
    so a small one sets compaction off and a huge one keeps it out of the way."""
    engine = TurnEngine(provider=provider, registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path), model="gpt-5.5", messages=messages)
    engine.compaction_settings = lambda: {"context_window": window, "buffer_tokens": 0, "keep_tokens": 0}
    return engine


def collect(engine, text="continue"):
    async def run():
        return [event async for event in engine.run(text)]

    return asyncio.run(run())


def _of(events, kind):
    return [event for event in events if event.type == kind]


def _finished(text="done"):
    return CompactingProvider([AssistantTurn(text=text, finish_reason="stop")])


# -- the cache-cold opportunistic prune ----------------------------------------------


class _CachingProvider(CompactingProvider):
    """CompactingProvider plus an Anthropic-shaped cache TTL, so the engine's cache-cold
    policy has a provider that manages one to read."""

    def __init__(self, *args, ttl: float = 300.0, **kwargs):
        super().__init__(*args, **kwargs)
        self._ttl = ttl

    def cache_ttl_seconds(self, model):
        return self._ttl


def _cold_cache_messages():
    """One aged ~23k-char tool result behind a small tail: with a tiny window the prune
    protect band leaves it foldable (retention keeps 5k chars of it)."""
    big = "log line\n" * 2_600
    return [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "inspect the build"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "run_shell", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": big},
        {"role": "user", "content": "continue"},
        {"role": "assistant", "content": "done " + "y" * 12_000},
    ]


def _events(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_an_idle_cache_lets_aged_results_fold_for_free(tmp_path):
    provider = _CachingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=_cold_cache_messages(), window=1_000)
    engine.log = SessionLog(tmp_path / "events.jsonl")
    canonical = engine.messages[3]["content"]
    engine._last_request_at = time.time() - 400  # past the provider's 300 s TTL

    assert engine._opportunistic_prune() is True

    assert engine.compaction_state is not None
    assert engine.compaction_state.pruned_upto == 5
    tool = [m for m in engine._outbound_messages() if m.get("role") == "tool"][0]
    assert len(tool["content"]) < len(canonical)  # the view shrank...
    assert engine.messages[3]["content"] == canonical  # ...the canonical history did not
    prunes = [e for e in _events(tmp_path / "events.jsonl") if e["type"] == "compaction/prune"]
    assert len(prunes) == 1
    assert prunes[0]["reason"] == "cache_cold" and prunes[0]["saved_tokens"] > 0


def test_a_warm_cache_keeps_aged_results(tmp_path):
    provider = _CachingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=_cold_cache_messages(), window=1_000)
    engine._last_request_at = time.time()  # a request just went out: the prefix is warm

    assert engine._opportunistic_prune() is False

    assert engine.compaction_state is None


def test_an_unknown_ttl_prunes_nothing(tmp_path):
    """OpenAI/DeepSeek-style auto-caching has no TTL to read; a wrong "cold" would buy
    a full-price request, so nothing is pruned on a guess."""
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=_cold_cache_messages(), window=1_000)
    engine._last_request_at = time.time() - 100_000

    assert engine._cache_ttl_seconds() is None
    assert engine._opportunistic_prune() is False
    assert engine.compaction_state is None


def test_a_staged_revert_makes_the_prune_free(tmp_path):
    provider = _CachingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=_cold_cache_messages(), window=1_000)
    engine._last_request_at = time.time()  # warm...
    engine.mark_prefix_dirty()  # ...but a staged revert just changed the view

    assert engine._opportunistic_prune() is True

    assert engine._prefix_dirty is False  # the decision was spent


def test_a_model_switch_makes_the_prune_free(tmp_path):
    """A cache belongs to one model. Measured 2026-10-06: 19 switches mid-session, every
    one reading nothing from the cache, and each sent the whole view at full price with
    its aged results still in it."""
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=_cold_cache_messages(), window=1_000)
    engine._last_request_at = time.time()  # a request just went out...
    engine._last_request_model = engine.model

    assert engine._opportunistic_prune() is False  # ...to this model: the prefix is warm

    engine.model = "another:model"
    assert engine._opportunistic_prune() is True
    assert engine.compaction_state.pruned_upto == 5


def test_a_changed_tool_list_makes_the_prune_free(tmp_path):
    """The tool list leads the request, so once it differs from the one the last request
    carried nothing after it can be read from the cache."""
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=_cold_cache_messages(), window=1_000)
    engine._last_request_at = time.time()
    engine._last_request_model = engine.model
    engine._note_tool_changes()  # the list the last request carried

    assert engine._opportunistic_prune() is False

    def lookup(q: str = "") -> str:
        """Look something up."""
        return q

    engine.registry.register(lookup)
    assert engine._opportunistic_prune() is True

    engine._note_tool_changes()  # the next request carries the new list
    engine.compaction_state = None
    assert engine._opportunistic_prune() is False


def test_the_first_request_of_an_engine_proves_nothing(tmp_path):
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=_cold_cache_messages(), window=1_000)
    engine._note_tool_changes()
    engine.model = "another:model"

    def lookup(q: str = "") -> str:
        """Look something up."""
        return q

    engine.registry.register(lookup)

    assert engine._last_request_at is None  # nothing was sent, so nothing is cached
    assert engine._opportunistic_prune() is False


def test_prune_after_idle_seconds_overrides_the_provider_ttl(tmp_path):
    provider = _CachingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=_cold_cache_messages(), window=1_000)
    engine._last_request_at = time.time() - 500

    engine.compaction_settings = lambda: {
        "context_window": 1_000,
        "buffer_tokens": 0,
        "keep_tokens": 0,
        "prune_after_idle_seconds": 10_000,
    }
    assert engine._cache_ttl_seconds() == 10_000.0
    assert engine._opportunistic_prune() is False  # 500 s < the forced 10 000 s

    engine.compaction_settings = lambda: {
        "context_window": 1_000,
        "buffer_tokens": 0,
        "keep_tokens": 0,
        "prune_after_idle_seconds": 0,
    }
    assert engine._cache_ttl_seconds() is None  # 0 disables the pass
    assert engine._opportunistic_prune() is False


# -- nothing is summarized ahead of the trigger ---------------------------


def test_nothing_is_summarized_below_the_trigger(tmp_path):
    """The prepared compaction is gone: it summarized at 70% of the trigger and topped up
    at the trigger in about the time a full summary takes, so it bought neither speed
    nor money. A session between 70% of its trigger (where it prepared) and the trigger
    makes no summarizer call at all."""
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(turns=6, bulk=2000), window=4_000)
    engine.compaction_settings = lambda: {"context_window": 4_000, "buffer_tokens": 0, "keep_tokens": 0}
    assert 0.7 * engine._history_trigger_tokens() < engine._context_signal()
    assert engine._compaction_due() is False

    async def _run():
        return [e async for e in engine.run("continue")]

    asyncio.run(_run())

    assert provider.summary_calls == []
    assert engine.compaction_state is None


def test_a_failure_with_no_state_yet_still_backs_off(tmp_path):
    """The state-based gate has nothing to read before a first compaction; the engine
    clock is what stops a failing FIRST attempt from being retried every step."""
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    assert engine.compaction_state is None

    engine._stamp_compaction_failure()

    allowed, why = engine._compaction_gate_now()
    assert allowed is False and "failed" in why


def test_a_manual_compaction_does_not_feed_the_automatic_streak(tmp_path):
    """Mashing `/compact` on a quiet session must not arm the automatic policy's cooldown:
    the prior streak carries unchanged, success or not."""
    from harness.context.compaction import CompactionState

    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    engine.compaction_state = CompactionState(
        boundary_index=2,
        summary_text="old",
        working_state="w",
        ineffective_streak=1,
        attempts=1,
        last_attempt_at=1.0,
    )
    candidate = CompactionState(boundary_index=4, summary_text="new", working_state="w")

    engine._adopt_compaction(candidate, "summarized", "text", manual=True)

    assert engine.compaction_state.ineffective_streak == 1  # untouched


def test_a_failed_compaction_records_the_error_text(tmp_path, monkeypatch):
    """A harness bug and a provider outage must not look identical in the durable record:
    the exception text rides `compaction/end` on the failed exit, and the history is left
    exactly as it was (no trim fallback — study round 29)."""
    provider = CompactingProvider(
        [AssistantTurn(text="done", finish_reason="stop")], summary_fails=99
    )
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    engine.log = SessionLog(tmp_path / "events.jsonl")

    assert asyncio.run(engine._compact_now()) is None

    ends = [e for e in _events(tmp_path / "events.jsonl") if e["type"] == "compaction/end"]
    assert ends and ends[0]["reason"] == "failed"
    assert "the summary model is unavailable" in (ends[0].get("error") or "")
    assert engine.compaction_state is None
    assert [e for e in _events(tmp_path / "events.jsonl") if e["type"] == "compaction/summary"] == []


def test_the_prune_spill_is_written_once_across_renders(tmp_path):
    """`_outbound_messages` runs several times per step; the content-addressed sink writes
    a given result once, so the view stays byte-stable and no spill files pile up."""
    from harness.context.compaction import CompactionState
    from harness.tools.spill import SpillStore
    from harness.orchestration.worlds import LocalWorld

    big = "line of output\n" * 1_000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "run_shell", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": big},
    ]
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=messages, window=100_000)
    engine.world = LocalWorld(root=tmp_path, spill=SpillStore(tmp_path / "spill"))
    engine.compaction_state = CompactionState(
        boundary_index=0, summary_text="", working_state="", pruned_upto=4
    )

    first = engine._outbound_messages()
    second = engine._outbound_messages()

    assert first == second
    assert "the complete output is at" in first[3]["content"]
    assert len(list((tmp_path / "spill").glob("pruned-*.log"))) == 1


def test_measurement_uses_the_revert_view_and_suspends_an_unfitting_state(tmp_path):
    """A staged `/undo` hides canonical messages; the measurement must price the view the
    model actually receives, not the un-reverted one."""
    from harness.context import compaction as C
    from harness.session.revert import revert_view

    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(turns=4), window=100_000)
    engine.revert_state = {
        "boundary": 3,
        "staged_at": len(engine.messages),
        "ts": 0.0,
    }
    state = C.CompactionState(
        boundary_index=0, summary_text="", working_state="", pruned_upto=5
    )

    measured = engine._view_tokens(state)

    assert measured == C.estimate_tokens(
        C.apply_to_outbound(revert_view(engine.messages, engine.revert_state), None)
    )


def test_the_loop_asks_for_the_cache_cold_pass_between_turns(tmp_path):
    calls = []
    provider = _CachingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(turns=1), window=200_000)
    engine._opportunistic_prune = lambda: calls.append(True) or True

    collect(engine)

    assert calls  # asked on the iteration before the provider call


def test_a_turn_on_another_model_prunes_before_its_first_request(tmp_path):
    """Through the loop: the first turn goes to one model, the person switches, and the
    next turn's request is built from a view whose aged results were already folded."""
    provider = CompactingProvider(
        [
            AssistantTurn(text="done", finish_reason="stop"),
            AssistantTurn(text="done again", finish_reason="stop"),
        ]
    )
    # A view far under the window (nothing is due) with one large result older than the
    # 40k-token band a prune never touches.
    messages = _cold_cache_messages()
    messages[-1] = {"role": "assistant", "content": "done " + "y" * 180_000}
    engine = make_engine(tmp_path, provider, messages=messages, window=200_000)
    engine.log = SessionLog(tmp_path / "events.jsonl")

    collect(engine)
    assert engine.compaction_state is None  # no pressure, and the cache was warm
    assert engine._last_request_model == engine.model
    assert not [e for e in _events(tmp_path / "events.jsonl") if e["type"] == "compaction/prune"]

    engine.model = "another:model"
    collect(engine)

    prunes = [e for e in _events(tmp_path / "events.jsonl") if e["type"] == "compaction/prune"]
    assert len(prunes) == 1 and prunes[0]["reason"] == "cache_cold"
    assert engine._last_request_model == "another:model"


def test_an_estimate_over_the_trigger_compacts_before_the_request_and_the_next_request_shows_it(tmp_path):
    provider = _finished()
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    events = collect(engine)
    [compacted] = _of(events, EventType.COMPACTED)
    assert not _of(events, EventType.ERROR) and engine.compaction_state is not None
    assert provider.summary_calls[0]["model"] == "gpt-5.5"  # the conversation's own model writes it
    # The summary is shown to the person (display only), on the event and on the saved notice.
    assert compacted.data["summary"] == _render_parts([_part(SUMMARY)])
    notices = [m for m in engine.messages if m.get("role") == "notice" and m.get("kind") == "compacted"]
    assert notices and notices[0].get("summary") == _render_parts([_part(SUMMARY)])
    # What is sent: the system prompt, then the block in place of the old turns, which lists the
    # person's own messages; the stored transcript still holds every turn.
    sent = engine._outbound_messages()
    assert sent[0]["role"] == "system"
    block = sent[1]["content"]
    assert "<compacted-history>" in block and SUMMARY.splitlines()[-1] in block and "request 0" in block
    assert any("answer 0" in str(m.get("content")) for m in engine.messages)


def test_the_usage_a_provider_reports_sets_it_off_between_two_steps(tmp_path):
    # The history is too small for the estimate; only the usage the first step reports crosses
    # the trigger, so the compaction happens before the second step.
    provider = CompactingProvider([
        AssistantTurn(tool_calls=[ToolCall(id="c1", name="nonexistent_tool", arguments={})], finish_reason="tool_calls", usage=TokenUsage(input=51_000, output=10)),
        AssistantTurn(text="done", finish_reason="stop"),
    ])
    engine = make_engine(tmp_path, provider, messages=long_history(turns=2, bulk=4_000), window=50_000)
    assert _of(collect(engine), EventType.COMPACTED) and provider.summary_calls
    assert engine._last_context_tokens is None  # forgotten once the view shrank


def test_a_summary_that_cannot_be_written_changes_nothing_when_nobody_is_there(tmp_path):
    """There is no trimming to fall back on (study round 29): a failed summary leaves no state and
    no block, only a durable `failed` row and the cooldown. A conversation that cannot compact fails
    loudly later instead of dropping turns without a word."""
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")], summary_fails=99)
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    events = collect(engine)  # is_attended is unset: nobody is there to ask
    assert not _of(events, EventType.COMPACTED) and engine.compaction_state is None
    assert len(provider.summary_calls) == 2  # one retry, whatever happens, then it stops
    assert not any("<compacted-history>" in str(m.get("content")) for m in engine._outbound_messages())


def _attended(engine, answer):
    asked: list[dict] = []

    async def ask(args, tool_call_id=None):
        asked.append(args)
        return {"answer": answer}

    engine.is_attended = lambda: True
    engine.question_asker = ask
    return asked


def test_a_person_who_is_there_is_asked_and_can_retry_or_skip(tmp_path):
    retried = make_engine(tmp_path, CompactingProvider([AssistantTurn(text="done", finish_reason="stop")], summary_fails=2), messages=long_history(), window=1_000)
    asked = _attended(retried, "Retry")
    collect(retried)
    assert asked and asked[0]["options"] == ["Retry", "Skip for now"]
    assert retried.compaction_state is not None

    skipped = make_engine(tmp_path, CompactingProvider([AssistantTurn(text="done", finish_reason="stop")], summary_fails=99), messages=long_history(), window=1_000)
    _attended(skipped, "Skip for now")
    events = collect(skipped)
    assert skipped.compaction_state is None and not _of(events, EventType.COMPACTED)


def test_a_provider_refusing_an_overlong_request_is_answered_by_compacting_and_asking_again(tmp_path):
    # The window is so large the trigger never fires; the provider's 400 is the only sign.
    provider = CompactingProvider([AssistantTurn(text="recovered", finish_reason="stop")], main_overflows=1)
    events = collect(make_engine(tmp_path, provider, messages=long_history(), window=1_000_000))
    assert _of(events, EventType.COMPACTED) and not _of(events, EventType.ERROR)
    answers = _of(events, EventType.ASSISTANT_MESSAGE)
    assert answers and answers[-1].data["text"] == "recovered"
    assert provider.main_calls == 2


def test_any_other_provider_error_is_shown_and_sets_off_nothing(tmp_path):
    class RateLimited(CompactingProvider):
        def complete(self, *, model, messages, tools=None, **settings):
            raise RuntimeError("rate limit exceeded")

    events = collect(make_engine(tmp_path, RateLimited([]), messages=long_history(turns=1), window=1_000_000))
    assert _of(events, EventType.ERROR) and not _of(events, EventType.COMPACTED)


def test_the_compacting_signal_comes_before_the_summary_and_is_never_saved(tmp_path):
    # Surfaces show "Compacting context…" from COMPACTING, which is sent before the slow summary call.
    engine = make_engine(tmp_path, _finished(), messages=long_history(), window=1_000)
    kinds = [event.type for event in collect(engine)]
    assert EventType.COMPACTING in kinds and kinds.index(EventType.COMPACTING) < kinds.index(EventType.COMPACTED)
    assert not any(m.get("role") == "notice" and m.get("kind") == "compacting" for m in engine.messages)


# -- the settings and the saved state ------------------------------------------------------------


class _Hi(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="hi", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


def test_the_compaction_settings_are_checked_kept_and_given_back_under_their_flat_names(tmp_path):
    from harness.interface.server.manager import SessionManager

    manager = SessionManager(workspace=tmp_path, provider=_Hi())
    kept = manager.set_compaction_settings(buffer_tokens=30_000, keep_tokens=12_000, model="gpt-4o-mini")
    assert kept["ok"] and (kept["buffer_tokens"], kept["keep_tokens"]) == (30_000, 12_000)
    assert manager.compaction_settings()["model"] == "gpt-4o-mini"
    assert manager.set_compaction_settings(buffer_tokens="lots")["ok"] is False
    assert manager.set_compaction_settings(keep_tokens="lots")["ok"] is False
    assert manager.set_compaction_settings(buffer_tokens=-1)["buffer_tokens"] == 0  # a negative is taken as none
    flat = manager.compaction_settings_payload()  # as /v1/settings names them
    assert (flat["compaction_keep_tokens"], flat["compaction_model"]) == (12_000, "gpt-4o-mini")


def test_the_compaction_state_is_there_again_after_a_save_and_a_rebuild(tmp_path):
    from harness.context.compaction import CompactionState
    from harness.interface.server.manager import SessionManager

    manager = SessionManager(workspace=tmp_path, provider=_Hi())
    engine = manager.get_engine("compact-persist", agent="reeve", workspace=str(tmp_path))
    assert callable(engine.compaction_settings) and engine.compaction_settings()["buffer_tokens"] == 20_000  # read from Settings each time
    engine.messages += long_history(turns=3)[1:]
    engine.compaction_state = CompactionState(boundary_index=3, summary_text="the gist", working_state="", user_messages=["u"])
    manager.save("compact-persist", engine)
    manager._engines.pop("compact-persist")
    assert manager.get_engine("compact-persist", agent="reeve", workspace=str(tmp_path)).compaction_state == engine.compaction_state


# -- the standing plan across the boundary --------------------------------------------
#
# Reproduced from the field (2026-08-27): compaction fired mid-turn while a plan was in
# flight, the agent finished the work correctly, and the Progress panel never moved again.
# Every `todo_write` call that had established the list was on the wrong side of the
# boundary, so the model no longer knew what the list said and stopped updating it.


def _with_plan(engine, plan):
    from harness.tools.todo import TodoList

    engine.todo = TodoList(items=list(plan))
    return engine


PLAN = [
    {"content": "Load the CSV", "status": "done"},
    {"content": "Chart revenue by region", "status": "in_progress"},
    {"content": "Write the summary", "status": "pending"},
]


def test_the_plan_crosses_the_boundary_with_an_instruction_to_re_issue_it(tmp_path):
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = _with_plan(
        make_engine(tmp_path, provider, messages=long_history(), window=1_000), PLAN
    )

    events = collect(engine)
    assert any(e.type == EventType.COMPACTED for e in events)

    block = engine._outbound_messages()[1]["content"]
    assert "[x] Load the CSV" in block
    assert "[>] Chart revenue by region" in block
    assert "[ ] Write the summary" in block
    # The list alone would fix the model and leave the panel frozen: the panel is fed by
    # `todo_write` CALLS, and this block is text.
    assert "todo_write" in block


def test_a_failed_summary_leaves_the_plan_and_view_untouched(tmp_path):
    """Unattended, summarizer down: nothing is adopted, so the outbound view carries no
    compacted block and the plan in the todo list is still whatever it was."""
    provider = CompactingProvider(
        [AssistantTurn(text="done", finish_reason="stop")], summary_fails=99
    )
    engine = _with_plan(
        make_engine(tmp_path, provider, messages=long_history(), window=1_000), PLAN
    )

    collect(engine)
    assert engine.compaction_state is None
    assert not any(
        "<compacted-history>" in str(m.get("content"))
        for m in engine._outbound_messages()
    )


def test_a_session_that_never_planned_gets_no_plan_section(tmp_path):
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)

    collect(engine)
    assert "Standing plan" not in engine._outbound_messages()[1]["content"]


def test_the_plan_survives_the_save_and_rebuild_a_reload_does(tmp_path):
    """The block is rebuilt from the persisted state, so a reopened session must not lose
    the plan the model was just told to re-issue."""
    from harness.context.compaction import CompactionState

    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = _with_plan(
        make_engine(tmp_path, provider, messages=long_history(), window=1_000), PLAN
    )
    collect(engine)

    rebuilt = CompactionState.from_dict(engine.compaction_state.as_dict())
    assert rebuilt == engine.compaction_state
    assert rebuilt.standing_plan[1]["content"] == "Chart revenue by region"


def test_a_provider_that_under_reports_the_prompt_is_not_believed(tmp_path):
    """opencode-go's Anthropic-wire route for qwen3.8-flash reported the same 12,209 input
    tokens five steps running while the session grew: trusted, the signal never passed
    13k and compaction could never fire. History is append-only between measurements, so
    a count that does not move after thousands of appended tokens is partial."""
    from harness.context import compaction as C

    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(turns=2, bulk=100), window=10_000_000)
    engine._record_context_measure(1_000)
    assert engine._last_context_tokens == 1_000  # nothing to contradict it yet

    engine.messages.extend(long_history(turns=4, bulk=24_000)[1:])  # tens of thousands appended
    honest = C.estimate_tokens(engine.messages)
    engine._record_context_measure(1_000 + honest)  # a count that moved: believed
    assert engine._usage_unreliable is False

    engine.messages.extend(long_history(turns=4, bulk=24_000)[1:])
    engine._record_context_measure(1_000 + honest)  # did not move: partial
    assert engine._usage_unreliable is True
    assert engine._last_context_tokens is None
    assert engine._context_signal() >= C.estimate_tokens(engine.messages) * 0.9
    engine._record_context_measure(50)  # and it stays disbelieved while its count does not follow
    assert engine._last_context_tokens is None


def test_display_only_reasoning_is_not_counted_as_appended_prompt(tmp_path):
    """gpt-6-luna's long reasoning stays in history for display but is never sent; counting
    it as appended prompt made an honest provider look partial (a false alarm that pushed
    the session onto the cruder estimate)."""
    from harness.context import compaction as C

    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(turns=2, bulk=100), window=10_000_000)
    engine._record_context_measure(1_000)
    engine.messages.append(  # ~45k tokens if it were priced: past the evidence the check needs
        {"role": "assistant", "content": "short answer", "reasoning": "thinking " * 20_000}
    )
    engine.messages.append({"role": "user", "content": "next"})
    sent_growth = C.estimate_tokens(engine._outbound_messages()) + engine._tool_schema_tokens() - engine._usage_anchor[1]
    engine._record_context_measure(1_000 + sent_growth)  # priced what was actually sent
    assert engine._usage_unreliable is False


def test_the_answer_tells_the_surface_where_this_session_compacts(tmp_path):
    """Session b3a8491e-9db: a 1M model under the auto cap (400k) compacts at 380k, and the
    context gauge read "381k of 1M · 38%" just before it did. The message event carries
    the trigger the engine itself uses, so the gauge measures against the real point."""
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(turns=1), window=500_000)
    engine.compaction_settings = lambda: {
        "context_window": 500_000,
        "max_context_tokens": 100_000,
        "buffer_tokens": 0,
        "keep_tokens": 0,
        "output_tokens": 1,
    }
    final = next(e for e in collect(engine) if e.type == EventType.ASSISTANT_MESSAGE)
    assert final.data["compact_at"] == engine._history_trigger_tokens() == 100_000
