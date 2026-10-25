"""One price for a context: the estimate is taken over the request as it is sent.

A stored message carries the model's thinking, the card its result is drawn with, timings,
usage, and a second copy of a stamped message. None of it reaches a provider, and all of it
used to be priced: of 20 compactions in the real store, the harness's own price of its view
agreed with the provider's within 20% once. Scripted providers only.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from harness.context import compaction as C
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, ToolCall
from harness.llm.providers.base import TokenUsage
from harness.session.session_log import SessionLog
from harness.tools import ToolRegistry
from harness.tools.context_fold import FOLD_CONTEXT
from harness.llm.wire import SIDECARS, wire_message, wire_messages

IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 400_000}}


def stored_history(steps: int = 10, bulk: int = 2_000) -> list[dict]:
    """A session as it is on disk: every assistant row with its thinking and usage, every
    result with the card it is drawn with."""
    messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "audit the repo", "ts": 1.0, "api_content": "audit the repo\n\n<system-context>\nNow: noon\n</system-context>"},
    ]
    for i in range(steps):
        call = {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}
        messages.append(
            {
                "role": "assistant", "content": f"reading {i}", "tool_calls": [call], "ts": 1.0,
                "reasoning": "thinking about the next file. " * 300,
                "usage": {"input": 1, "output": 2, "context_breakdown": {"messages": 3}},
                "timing": {"dispatched": 1.0, "completed": 2.0},
            }
        )
        body = "line of code\n" * (bulk // 13)
        messages.append(
            {
                "role": "tool", "tool_call_id": f"c{i}", "content": body, "ts": 1.0,
                "_presentation": {"card": "read", "lines": [{"number": n, "text": "line of code"} for n in range(bulk // 13)]},
            }
        )
    return messages


def request_estimate(messages, tools) -> int:
    """What a provider is sent, estimated the way the engine says it does: wire messages
    plus the schemas."""
    schemas = sum(len(json.dumps(tool, default=str, ensure_ascii=False)) // 4 for tool in tools or [])
    return C.estimate_tokens(messages) + schemas


class Priced(ProviderClient):
    """Answers each request with a usage that is `bias` times the estimate of that request."""

    def __init__(self, turns, *, bias: float = 1.0):
        self._turns = list(turns)
        self.bias = bias
        self.estimates: list[int] = []
        self.requests: list[list[dict]] = []
        self.on_request = None

    def complete(self, *, model, messages, tools=None, **settings):
        estimate = request_estimate(messages, tools)
        self.estimates.append(estimate)
        self.requests.append(list(messages))
        if self.on_request is not None:
            self.on_request()
        turn = self._turns.pop(0)
        turn.usage = TokenUsage(input=int(estimate * self.bias), output=5)
        return turn

    def capabilities(self, model):
        return ModelCapabilities()


def lookup(path: str) -> str:
    """Look a path up."""
    return "found"


def make_engine(tmp_path, provider, *, messages=None, window=400_000, tools=True):
    registry = ToolRegistry()
    if tools:
        registry.register(lookup)
    engine = TurnEngine(
        provider=provider,
        registry=registry,
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="gpt-5.5",
        messages=messages,
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")
    engine.compaction_settings = lambda: {
        "context_window": window, "buffer_tokens": 0, "keep_tokens": 1_500, "fold_keep_tokens": 1_500,
    }
    return engine


def collect(engine, text="continue"):
    async def _run():
        return [e async for e in engine.run(text)]

    return asyncio.run(_run())


def done():
    return AssistantTurn(text="done", finish_reason="stop")


# -- the wire form -------------------------------------------------------------------------


def test_a_stored_row_costs_what_its_wire_form_costs():
    stored = stored_history()
    sent = wire_messages(stored)
    assert C.estimate_tokens(stored) == C.estimate_tokens(sent)
    # And the difference is not small: as stored, this session is several times its request.
    raw = sum(len(json.dumps(m, ensure_ascii=False)) for m in stored) // 4
    assert raw > 2 * C.estimate_tokens(stored)


@pytest.mark.parametrize("key", [key for key in SIDECARS if key != "api_content"])
def test_a_display_only_key_is_neither_sent_nor_priced(tmp_path, key):
    """One list (`harness/llm/wire.py`), read by the request and by its price: a key on it
    cannot be in one and not the other."""
    plain = {"role": "assistant", "content": "the answer"}
    row = {**plain, key: "x" * 8_000}
    assert C.estimate_tokens([row]) == C.estimate_tokens([plain])

    engine = make_engine(tmp_path, Priced([]), messages=[{"role": "user", "content": "hi"}, row], tools=False)
    assert all(key not in message for message in engine._outbound_messages())


def test_what_a_provider_is_sent_holds_none_of_what_is_kept_for_display(tmp_path):
    """By name, so that dropping a key from the list is seen: these are what a stored
    row holds today that no provider accepts or should be charged for."""
    provider = Priced([done()])
    engine = make_engine(tmp_path, provider, messages=stored_history(), tools=False)
    collect(engine)
    (request,) = provider.requests
    sent_keys = {key for message in request for key in message}
    assert sent_keys <= {"role", "content", "tool_calls", "tool_call_id"}
    for key in ("reasoning", "_presentation", "usage", "timing", "ts", "api_content"):
        assert key in SIDECARS
    # ...and the stamp a turn start wrote into `api_content` did travel.
    assert "<system-context>" in str(request[1]["content"])


def test_the_stamped_copy_of_a_message_is_priced_once_and_its_image_as_an_image():
    """`api_content` IS what is sent, in place of `content`. A session whose first message
    held a 1.4 MB picture stored it under both keys, and the copy was priced as 370k tokens
    of prose (real store, session 14715fae-61f, 2026-10-04)."""
    text = {"type": "text", "text": "what is this paper?"}
    stamp = {"type": "text", "text": "\n\n<system-context>\nNow: noon\n</system-context>"}
    row = {"role": "user", "content": [text, IMAGE], "api_content": [text, IMAGE, stamp], "ts": 1.0}

    price = C.estimate_tokens([row])

    assert price == C.estimate_tokens([{"role": "user", "content": [text, IMAGE, stamp]}])
    assert C.IMAGE_TOKEN_ESTIMATE <= price < C.IMAGE_TOKEN_ESTIMATE + 200


def test_a_notice_is_never_sent_and_costs_nothing():
    notice = {"role": "notice", "kind": "compacted", "text": "Context folded", "summary": "s" * 9_000}
    assert wire_message(notice) is None
    assert C.estimate_tokens([notice]) == 0


def test_wire_form_never_mutates_and_is_stable_on_what_is_already_sent():
    row = stored_history(steps=1)[2]
    before = json.dumps(row, sort_keys=True)
    sent = wire_message(row)
    assert json.dumps(row, sort_keys=True) == before
    assert "reasoning" not in sent and sent["tool_calls"] == row["tool_calls"]
    assert wire_message(sent) is sent


def test_reasoning_is_priced_only_where_the_route_sends_it_back():
    row = stored_history(steps=1)[2]
    dropped = C.estimate_tokens([row])
    echoed = C.estimate_tokens([row], echo="reasoning_content")
    assert echoed > dropped + len(row["reasoning"]) // 4 - 10
    assert wire_message(row, echo="reasoning_content")["reasoning_content"] == row["reasoning"]


def test_a_kept_tail_is_measured_in_what_is_sent():
    """`pick_boundary` keeps `keep_tokens` of the newest messages. Measured on stored rows
    (thinking and cards included) the tail it kept was a third of what was asked for."""
    stored = stored_history(steps=12)
    boundary = C.pick_boundary(stored, keep_tokens=2_000)
    kept = C.estimate_tokens(stored[boundary:])
    assert boundary is not None and 1_000 <= kept <= 2_000


# -- the engine's price --------------------------------------------------------------------


def test_the_price_of_a_view_is_the_request_as_sent(tmp_path):
    """Tool schemas, system prompt and wire messages: the number is for the bytes sent."""
    provider = Priced([done()])
    engine = make_engine(tmp_path, provider, messages=stored_history())
    seen: list[int] = []
    provider.on_request = lambda: seen.append(engine._view_tokens())

    collect(engine)

    assert seen == provider.estimates
    assert engine._tool_schema_tokens() > 0


def test_the_signal_before_any_provider_count_holds_the_tool_schemas(tmp_path):
    engine = make_engine(tmp_path, Priced([]), messages=stored_history())
    assert engine._last_context_tokens is None
    assert engine._context_signal() == (
        C.estimate_tokens(engine._outbound_messages()) + engine._tool_schema_tokens()
    )


@pytest.mark.parametrize("bias", [0.85, 1.0, 1.1])
def test_the_bias_is_the_providers_count_over_the_estimate_of_the_same_request(tmp_path, bias):
    """Like for like. The ratio this replaced divided the provider's count for the request
    by an estimate of the STORED rows: on this history that reads well under half."""
    engine = make_engine(tmp_path, Priced([done()], bias=bias), messages=stored_history())
    collect(engine)
    assert engine._estimate_bias == pytest.approx(bias, abs=0.01)


def test_a_compaction_does_not_move_the_bias(tmp_path):
    """After a compaction the request is the block and the tail. The old ratio went on
    dividing by the whole stored history and fell to its floor (0.25 in the real session),
    so every price after a compaction was a quarter of the truth."""
    provider = Priced([done(), done()], bias=1.1)
    messages = stored_history(steps=14)
    engine = make_engine(tmp_path, provider, messages=messages)
    engine.compaction_state = C.CompactionState(
        boundary_index=20, summary_text="earlier: read ten files, nothing wrong.", working_state=""
    )

    collect(engine)
    collect(engine, "and the rest")

    assert engine._estimate_bias == pytest.approx(1.1, abs=0.01)
    # The price of the view is the provider's price of it, in the provider's unit.
    assert engine._view_tokens() == pytest.approx(engine._context_signal(), rel=0.02)


def test_a_count_far_from_the_estimate_is_not_carried_as_bias(tmp_path):
    """Five times the estimate is not a tokenizer: it is an attachment priced by the page
    or a broken usage report, and the next estimate must not be multiplied by it."""
    engine = make_engine(tmp_path, Priced([done()], bias=5.0), messages=stored_history())
    collect(engine)
    assert engine._estimate_bias is None
    assert engine._last_context_tokens is not None  # the count itself is still the signal


def test_a_view_being_weighed_goes_through_the_projection_the_real_one_does(tmp_path):
    engine = make_engine(tmp_path, Priced([]), messages=stored_history(steps=14))
    state = C.CompactionState(boundary_index=20, summary_text="earlier work, summarized.", working_state="")

    weighed = engine._view_tokens(state)

    assert weighed == C.estimate_tokens(engine._outbound_messages(state)) + engine._tool_schema_tokens()
    assert weighed < engine._view_tokens(None) == engine._view_tokens()
    assert engine.compaction_state is None  # weighing a state does not adopt it


def test_the_delta_since_the_last_count_is_priced_as_sent(tmp_path):
    engine = make_engine(tmp_path, Priced([]), messages=stored_history(steps=1))
    engine._last_context_tokens = 10_000
    engine._context_measured_at = len(engine.messages)
    row = stored_history(steps=1)[2]  # an assistant row with ~9k characters of thinking
    engine.messages.append(row)
    assert engine._context_signal() == 10_000 + C.estimate_tokens([wire_message(row)])


# -- what a fold says ----------------------------------------------------------------------


def test_what_a_fold_tells_the_model_is_the_request_before_and_after(tmp_path):
    """The real session was told "~126,669 tokens before, ~3,540 from your next step" for a
    request the provider priced at 89,145 and a next one at 24,737."""
    note = (
        "Read a.py ten times over: nothing wrong in it. The audit of the repo is done for "
        "that file; b.py and c.py are left, and pytest has not been run yet."
    )
    fold = AssistantTurn(
        text="folding", finish_reason="tool_calls",
        tool_calls=[ToolCall(id="f1", name=FOLD_CONTEXT, arguments={"note": note})],
    )
    provider = Priced([fold, done()])
    engine = make_engine(tmp_path, provider, messages=stored_history(steps=14))
    engine.enable_model_fold()

    collect(engine, "and now b.py")

    first, second = provider.estimates
    (summary,) = [e for e in engine.log.read() if e.get("type") == "compaction/summary"]
    assert summary["before_tokens"] == pytest.approx(first, rel=0.05)
    assert summary["after_tokens"] == pytest.approx(second, rel=0.05)
    (start,) = [e for e in engine.log.read() if e.get("type") == "compaction/start"]
    assert start["before_tokens"] == pytest.approx(start["signal"], rel=0.05)
    result = next(m for m in engine.messages if m.get("role") == "tool" and "Folded" in str(m.get("content")))
    assert f"~{summary['before_tokens']:,} tokens before" in result["content"]
