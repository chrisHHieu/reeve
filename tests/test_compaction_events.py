"""Compaction as a durable transaction — and the invariant it makes true.

`compaction` used to be a display notice and nothing else, while `invariants.py`
audited for a `compaction/summary` event nothing wrote: every compacted session
reported a model-visibility violation against its own correct behaviour. These tests
pin the durable half — start/summary/end around the attempt, an end on EVERY exit,
crash repair for an unmatched start, and a real compacted session that leaves the
invariant clean.
"""

from __future__ import annotations

import asyncio

from harness.loop import TurnEngine
from harness.session.events import EventType
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import TokenUsage
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry

from summary_fixtures import valid_summary

SUMMARY = valid_summary("keep building the report")


def _is_summarizer_call(messages):
    marker = "Produce ALL of the following sections"
    return any(marker in str(m.get("content", "")) for m in messages or [])


class CompactingProvider(ProviderClient):
    def __init__(
        self, turns, *, summary=SUMMARY, summary_fails=0, summary_usage=None,
        main_overflows=0,
    ):
        self._turns = list(turns)
        self.summary = summary
        self.summary_fails = summary_fails
        self.summary_calls = 0
        self.summary_usage = (
            summary_usage
            if summary_usage is not None
            else TokenUsage(input=3_000, output=120)
        )
        self.main_overflows = main_overflows

    def complete(self, *, model, messages, tools=None, **settings):
        if _is_summarizer_call(messages):
            self.summary_calls += 1
            if self.summary_fails > 0:
                self.summary_fails -= 1
                raise RuntimeError("summarizer down")
            return AssistantTurn(
                text=self.summary, finish_reason="stop", usage=self.summary_usage
            )
        if self.main_overflows > 0:
            self.main_overflows -= 1
            raise RuntimeError(
                "Error 400: maximum context length is 100000 tokens, request used more"
            )
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def long_history(turns=8, bulk=1500):
    msgs = [{"role": "system", "content": "be helpful"}]
    for i in range(turns):
        msgs.append({"role": "user", "content": f"request {i}", "ts": 1.0})
        msgs.append(
            {"role": "assistant", "content": f"answer {i} " + "x" * bulk, "ts": 1.0}
        )
    return msgs


def _seed_log(engine, messages):
    """Record the pre-seeded history the way the turns that wrote it would have: the
    model-visibility audit counts history against the log, so a test that hands the
    engine a transcript without its events is asking to be flagged (correctly)."""
    system = next(
        (m.get("content") for m in messages if m.get("role") == "system"), None
    )
    engine.log.append(
        "request/header", reason="initial", model=engine.model, system=system, tools=[]
    )
    for message in messages:
        role = message.get("role")
        if role == "user":
            engine.log.append("user/message", content=str(message.get("content")))
        elif role == "assistant":
            engine.log.append("assistant/message", text=message.get("content") or "")
            for call in message.get("tool_calls") or []:
                engine.log.append(
                    "tool/call",
                    call_id=call.get("id"),
                    name=(call.get("function") or {}).get("name"),
                )
        elif role == "tool":
            engine.log.append(
                "tool/result",
                call_id=message.get("tool_call_id"),
                content=str(message.get("content")),
                is_error=False,
            )


def make_engine(tmp_path, provider, *, messages=None, window=1_000, seed=True):
    engine = TurnEngine(
        provider=provider,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    if seed and messages:
        _seed_log(engine, messages)
    engine.compaction_settings = lambda: {
        "context_window": window,
        "buffer_tokens": 0,
        "keep_tokens": 0,
    }
    return engine


def collect(engine, text="continue"):
    async def _run():
        return [e async for e in engine.run(text)]

    return asyncio.run(_run())


def _events(engine, kind):
    return [e for e in engine.log.read() if e.get("type") == kind]


def test_a_summarized_compaction_is_a_balanced_transaction(tmp_path):
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    collect(engine)

    starts = _events(engine, "compaction/start")
    summaries = _events(engine, "compaction/summary")
    ends = _events(engine, "compaction/end")
    assert len(starts) == 1 and len(summaries) == 1 and len(ends) == 1
    assert ends[0]["reason"] == "summarized"
    assert starts[0]["before_tokens"] > 0
    assert starts[0]["model"] == "gpt-5.5"

    # The durable half carries the numbers a reader needs to answer "why did context
    # shrink, and by how much" without re-deriving them from token counters.
    summary = summaries[0]
    assert summary["reason"] == "summarized"
    assert summary["boundary"] > 0
    assert summary["saved_tokens"] > 0 and summary["saved_pct"] > 0
    assert summary["after_tokens"] < summary["before_tokens"]
    assert summary["summary_chars"] == len(SUMMARY)
    assert engine.log.dangling_compactions() == 0

    # The payoff: a compacted session no longer reports its own correct behaviour as a
    # model-visibility violation.
    assert _events(engine, "invariant/violation") == []


def _image_message(i):
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": f"look at screenshot {i}"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
        "ts": 1.0,
    }


def test_compaction_offloads_old_images_and_logs_it(tmp_path):
    """Images are the other payload that never ages out: at the first compaction, all but
    the newest two become placeholders in the outbound view (the summary's description
    stands), the decision is durable in the state, the log names it, and canonical history
    keeps every image."""

    class VisionProvider(CompactingProvider):
        # A vision route keeps image parts on the wire, so the offload is what removes
        # them; a non-vision route degrades every image in projection anyway.
        def capabilities(self, model):
            return ModelCapabilities(vision=True)

    messages = long_history()
    messages += [_image_message(1), _image_message(2), _image_message(3)]
    provider = VisionProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=messages, window=1_000)
    engine.compaction_settings = lambda: {
        "context_window": 1_000,
        "buffer_tokens": 0,
        "keep_tokens": 6_000,  # the three image messages land in the verbatim tail
    }
    collect(engine)

    assert engine.compaction_state is not None
    assert len(engine.compaction_state.offloaded_images) == 1
    out = engine._outbound_messages()
    kept = [
        part
        for m in out
        if isinstance(m.get("content"), list)
        for part in m["content"]
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    assert len(kept) == 2  # newest two stay
    ends = _events(engine, "compaction/end")
    assert ends and ends[0]["images_offloaded"] == 1
    assert (
        sum(
            1
            for m in engine.messages
            if isinstance(m.get("content"), list)
            for part in m["content"]
            if isinstance(part, dict) and part.get("type") == "image_url"
        )
        == 3
    )  # canonical history untouched


def test_a_failed_compaction_writes_only_the_failed_end_event(tmp_path):
    """No trim fallback (study round 29): the transaction closes as `failed` with the
    reason on the durable row, no `compaction/summary` claims a block that never entered
    the view, and the invariant is satisfied because nothing was hidden."""
    provider = CompactingProvider(
        [AssistantTurn(text="done", finish_reason="stop")], summary_fails=99
    )
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    collect(engine)

    summaries = _events(engine, "compaction/summary")
    ends = _events(engine, "compaction/end")
    assert summaries == []
    assert ends and ends[-1]["reason"] == "failed"
    # The reason the summarizer failed rides the durable row: a provider outage and a
    # harness bug must not look identical.
    assert "summarizer down" in str(ends[-1].get("error") or "")
    assert engine.compaction_state is None
    assert engine.log.dangling_compactions() == 0
    assert _events(engine, "invariant/violation") == []


def test_a_prune_only_attempt_balances_without_claiming_a_summary(tmp_path, monkeypatch):
    # One aged, foldable result dominates the window; the cheap lever alone brings the
    # view under the trigger, so no summarizer call happens. No block enters the
    # outbound view — so no `compaction/summary`, and the invariant must not ask for one.
    #
    # The protect band is pinned small: production sizes it against the trigger
    # (`prune_keep_tokens`), which on a 1k-token window protects the whole view and is
    # the correct production behavior — this test is about the transaction, not the band.
    monkeypatch.setattr("harness.context.compaction.tokens.PRUNE_PROTECT_TOKENS", 100)
    path = "/w/.reeve/output/shell-1.log"
    marker = f"\n… 1,204 lines omitted — the complete output is at {path} …\n"
    messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "check the build"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_shell", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "HEAD\n" + "x" * 8000 + marker},
        {"role": "user", "content": "and now?"},
        {"role": "assistant", "content": "still green " + "y" * 400},
        {"role": "user", "content": "summary?"},
        {"role": "assistant", "content": "all good " + "z" * 400},
    ]
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=messages, window=1_000)
    events = collect(engine)

    assert any(e.type == EventType.COMPACTED for e in events)
    assert provider.summary_calls == 0
    assert engine.compaction_state is not None
    assert engine.compaction_state.boundary_index == 0  # prune-only, no block
    starts = _events(engine, "compaction/start")
    assert len(starts) == 1 and starts[0]["before_tokens"] > 0
    assert _events(engine, "compaction/summary") == []
    ends = _events(engine, "compaction/end")
    assert len(ends) == 1 and ends[0]["reason"] == "pruned"
    assert engine.log.dangling_compactions() == 0
    assert _events(engine, "invariant/violation") == []


def test_an_unmatched_start_is_repaired_on_the_next_attach(tmp_path):
    log = SessionLog(tmp_path / "events.jsonl")
    log.append("turn/start", turn=1)
    log.append("compaction/start", turn=1, step=2, trigger=1_000, before_tokens=9_000)
    assert log.dangling_compactions() == 1

    engine = make_engine(tmp_path, CompactingProvider([]), messages=long_history(2, 10))

    repaired = SessionLog(log.path)
    engine.log = repaired
    engine._close_dangling_turn()

    ends = [e for e in repaired.read() if e.get("type") == "compaction/end"]
    assert len(ends) == 1
    assert ends[0]["reason"] == "interrupted" and ends[0].get("synthetic") is True
    assert repaired.dangling_compactions() == 0
    # The repair runs once per attached log, not once per call.
    engine._close_dangling_turn()
    assert len([e for e in repaired.read() if e.get("type") == "compaction/end"]) == 1


def test_a_failed_attempt_ends_with_failed(tmp_path, monkeypatch):
    # Summarizer down: the attempt ends `failed` and the cooldown applies (there is no
    # trim fallback to return anything else). Nothing is left open.
    provider = CompactingProvider(
        [AssistantTurn(text="done", finish_reason="stop")], summary_fails=99
    )
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    collect(engine)

    ends = _events(engine, "compaction/end")
    assert ends and ends[-1]["reason"] == "failed"
    assert _events(engine, "compaction/summary") == []
    assert engine.log.dangling_compactions() == 0


def test_a_raw_overflow_tries_the_cheap_lever_before_the_summarizer(tmp_path, monkeypatch):
    """When the provider refuses the request for length, the summarizer's replay is the
    SAME over-window conversation — it may be refused too. Pruning aged output first is
    the only move that can make that replay fit, and it costs no round-trip."""
    monkeypatch.setattr("harness.context.compaction.tokens.PRUNE_PROTECT_TOKENS", 100)
    # The protect band is 10% of the (huge) trigger; pin it out of the way so the aged
    # result is reachable. Production never has a 1M-token trigger in a test-sized view.
    monkeypatch.setattr("harness.context.compaction.tokens.PRUNE_KEEP_FRACTION", 0.0)
    path = "/w/.reeve/output/build-1.log"
    marker = f"\n… 9,000 lines omitted — the complete output is at {path} …\n"
    messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "check the build"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_shell", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "HEAD\n" + "x" * 8000 + marker},
        {"role": "user", "content": "and now?"},
        {"role": "assistant", "content": "still building " + "y" * 400},
    ]
    provider = CompactingProvider(
        [AssistantTurn(text="done", finish_reason="stop")], main_overflows=1
    )
    # A trigger the estimate never reaches: the 400 is the only pressure signal.
    engine = make_engine(tmp_path, provider, messages=messages, window=1_000_000)
    events = collect(engine)

    assert any(e.type == EventType.COMPACTED for e in events)
    assert provider.summary_calls == 0  # the cheap lever was enough
    assert engine.compaction_state is not None
    assert engine.compaction_state.boundary_index == 0  # prune-only
    assert _events(engine, "compaction/summary") == []
    ends = _events(engine, "compaction/end")
    assert ends[-1]["reason"] == "pruned"
    assert any(e.type == EventType.ASSISTANT_MESSAGE for e in events)  # recovered
    assert engine.log.dangling_compactions() == 0


def test_the_summarizer_round_trip_is_charged_to_the_turn(tmp_path):
    """The budget exists to stop runaway spend; a session oscillating around the trigger
    pays for full-context summarizer calls, and they used to be invisible to it."""
    main_usage = TokenUsage(input=1_500, output=40)
    provider = CompactingProvider(
        [AssistantTurn(text="done", finish_reason="stop", usage=main_usage)]
    )
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    collect(engine)

    summaries = _events(engine, "compaction/summary")
    assert summaries[0]["summarizer_tokens"] == provider.summary_usage.total
    ends = [e for e in engine.log.read() if e.get("type") == "turn/end"]
    assert ends[-1]["tokens"] == main_usage.total + provider.summary_usage.total
    notice = next(
        m
        for m in engine.messages
        if m.get("role") == "notice" and m.get("kind") == "compacted"
    )
    assert notice.get("summarizer_tokens") == provider.summary_usage.total


def test_a_retried_summarizer_is_charged_exactly_once(tmp_path):
    # The first attempt fails before it reports anything, the unconditional retry
    # succeeds: one billed round-trip, one charge. Guards against the counter being fed
    # per attempt rather than per reported usage.
    provider = CompactingProvider(
        [AssistantTurn(text="done", finish_reason="stop")], summary_fails=1
    )
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    collect(engine)
    summaries = _events(engine, "compaction/summary")
    assert summaries[0]["summarizer_tokens"] == provider.summary_usage.total


def test_the_persisted_notice_carries_the_numbers(tmp_path):
    provider = CompactingProvider([AssistantTurn(text="done", finish_reason="stop")])
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    collect(engine)
    notice = next(
        m
        for m in engine.messages
        if m.get("role") == "notice" and m.get("kind") == "compacted"
    )
    assert notice.get("saved_tokens", 0) > 0
    assert notice.get("reason") == "summarized"
    assert notice.get("summary") == SUMMARY


class APIConnectionError(Exception):
    """Named for the taxonomy in providers/errors.py (`is_transient` reads the class
    name), so this failure travels the ordinary transport retry path."""


class _TransientAfterCompaction(CompactingProvider):
    """Compacts cleanly, then the main model call dies as a flaky connection would."""

    def __init__(self):
        super().__init__([])

    def stream(self, *, model, messages, tools=None, **settings):
        raise APIConnectionError("connection reset by peer")
        yield  # pragma: no cover — generator marker


def test_a_retried_main_call_does_not_reopen_the_transaction(tmp_path):
    provider = _TransientAfterCompaction()
    engine = make_engine(tmp_path, provider, messages=long_history(), window=1_000)
    events = collect(engine)

    assert any(e.type == EventType.RETRYING for e in events)
    assert any(e.type == EventType.ERROR for e in events)  # the budget is finite
    starts = _events(engine, "compaction/start")
    ends = _events(engine, "compaction/end")
    assert len(starts) == 1 and len(ends) == 1
    assert engine.log.dangling_compactions() == 0
    assert _events(engine, "invariant/violation") == []
