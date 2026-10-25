"""Thinking blocks the Messages API will take back.

`_BindingApi` stands in for an enforced account: every thinking block it hands out remembers the
conversation it was written after (system, the set of tools, every message before it, with thinking
and cache markers left out), and a request that replays the block after any of that changed is a
400 with the API's own wording. Its check compares the lists themselves, independently of how the
provider decides, so these tests pin what the API would do and not how the harness gets there.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from harness.llm.providers import AnthropicProvider
from harness.llm.providers.claude import binding
from harness.llm.providers.claude_subscription.provider import AnthropicSubscriptionProvider
from harness.loop.message_shapes import assistant_message

MODEL = "claude-opus-5-5"
TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "write_file", "description": "Write.", "parameters": {"type": "object", "properties": {}}}},
]


class BadRequestError(Exception):
    """Named like the SDK's 400, which is all the provider reads of it."""


_REFUSAL = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': "
    "'messages.%d.content.0: Invalid `signature` in `thinking` block. The block is bound to a different "
    "conversation. Remove the block, or set `thinking.block_binding.prefix_mismatch_behavior` to "
    "\"drop_block\".'}}"
)


def _seen(value):
    """What the check compares: no cache markers, no thinking."""
    if isinstance(value, dict):
        return {k: _seen(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_seen(v) for v in value if not (isinstance(v, dict) and v.get("type") in ("thinking", "redacted_thinking"))]
    return value


def _system(system):
    return [system] if isinstance(system, str) else [block["text"] for block in system or []]


class _BindingApi:
    """`messages.create` / `beta.messages.create` on an account the prefix check is enforced on."""

    def __init__(self, *, reject_controls=False, rewrite_first=False):
        self.requests: list[dict] = []
        self.bound: dict[str, tuple] = {}
        self.reject_controls = reject_controls
        # A gateway that changes the first message on the way, differently each time (a stamp).
        self.rewrite_first = rewrite_first
        self.next_signature = 0

        def create(**request):
            return self._answer(request)

        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))

    def _answer(self, request):
        request = copy.deepcopy(request)
        self.requests.append(request)
        thinking = request.get("thinking") or {}
        if "block_binding" in thinking and self.reject_controls:
            raise BadRequestError("Error code: 400 - thinking.block_binding: Extra inputs are not permitted")
        if self.rewrite_first:
            request["messages"][0]["content"][0]["text"] += f" [gateway {len(self.requests)}]"
        tools = sorted(_seen(request.get("tools") or []), key=lambda tool: tool["name"])
        head = (_system(request.get("system")), tools)
        dropped = []
        for index, message in enumerate(request["messages"]):
            for position, block in enumerate(message["content"]):
                if block.get("type") != "thinking":
                    continue
                if any(key.startswith("_") for key in block):
                    raise BadRequestError(f"Error code: 400 - messages.{index}.content.{position}: Extra inputs are not permitted")
                if self.bound.get(block["signature"]) != (head, _seen(request["messages"][:index])):
                    if thinking.get("block_binding", {}).get("prefix_mismatch_behavior") == "drop_block":
                        dropped.append(SimpleNamespace(
                            type="thinking_dropped", path=f"messages.{index}.content.{position}", reason="prefix_binding_mismatch",
                        ))
                        continue
                    raise BadRequestError(_REFUSAL % index)
        self.next_signature += 1
        signature = f"sig-{self.next_signature}"
        self.bound[signature] = (head, _seen(request["messages"]))
        reply = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking=f"thought {self.next_signature}", signature=signature),
                SimpleNamespace(type="text", text=f"answer {self.next_signature}"),
            ],
            stop_reason="end_turn",
        )
        if "block_binding" in thinking:
            reply.input_transformations = dropped
        return reply


def _provider(api, **options):
    return AnthropicProvider(client=api, thinking_budget=8192, **options)


def _ask(provider, history, tools=TOOLS):
    """One step: the answer is stored the way the engine stores it."""
    turn = provider.complete(model=MODEL, messages=history, tools=tools)
    history.append(assistant_message(turn, model=MODEL))
    return turn


def _signatures(request):
    return [b["signature"] for m in request["messages"] for b in m["content"] if b.get("type") == "thinking"]


def _conversation():
    return [{"role": "system", "content": "You are Reeve."}, {"role": "user", "content": "first question"}]


def test_an_unchanged_conversation_carries_every_thought_back_as_it_was_written():
    api = _BindingApi()
    provider, history = _provider(api), _conversation()
    _ask(provider, history)
    history.append({"role": "user", "content": "second question"})
    turn = _ask(provider, history)

    assert _signatures(api.requests[-1]) == ["sig-1"]
    assert turn.thinking_dropped is None
    sent = api.requests[-1]["messages"][1]["content"][0]
    assert sent == {"type": "thinking", "thinking": "thought 1", "signature": "sig-1"}


def test_a_compacted_head_leaves_the_tails_old_thoughts_out_and_keeps_the_turn_going():
    """The reported failure: the compaction block stands where the first message was, the tail is
    replayed verbatim, and its thinking was written after the old head."""
    api = _BindingApi()
    provider, history = _provider(api), _conversation()
    _ask(provider, history)
    history.append({"role": "user", "content": "second question"})
    _ask(provider, history)
    history.append({"role": "user", "content": "third question"})

    compacted = [history[0], {"role": "user", "content": "<summary>the first question, answered</summary>"}, *history[2:]]
    turn = _ask(provider, compacted)

    assert _signatures(api.requests[-1]) == []
    assert turn.thinking_dropped == {"stale": 2}
    # The answers themselves are still there; only the reasoning behind them is left out.
    texts = [b.get("text") for m in api.requests[-1]["messages"] for b in m["content"] if b.get("type") == "text"]
    assert "answer 1" in texts and "answer 2" in texts


def test_a_thought_written_after_the_change_is_carried_from_then_on():
    api = _BindingApi()
    provider, history = _provider(api), _conversation()
    _ask(provider, history)
    history[1] = {"role": "user", "content": "first question, as the prune left it"}
    history.append({"role": "user", "content": "second question"})
    _ask(provider, history)
    history.append({"role": "user", "content": "third question"})
    turn = _ask(provider, history)

    assert _signatures(api.requests[-1]) == ["sig-2"]
    assert turn.thinking_dropped == {"stale": 1}


def test_a_different_tool_list_is_a_different_conversation_but_the_same_tools_reordered_is_not():
    api = _BindingApi()
    provider, history = _provider(api), _conversation()
    _ask(provider, history)
    history.append({"role": "user", "content": "plan it"})
    _ask(provider, history, tools=list(reversed(TOOLS)))
    assert _signatures(api.requests[-1]) == ["sig-1"]

    history.append({"role": "user", "content": "plan mode: write tools hidden"})
    turn = _ask(provider, history, tools=TOOLS[:1])
    assert _signatures(api.requests[-1]) == []
    assert turn.thinking_dropped == {"stale": 2}


def test_the_request_asks_the_api_to_drop_what_it_still_finds_and_the_log_hears_of_it():
    """A gateway that rewrites the request is a change the harness cannot see: the API drops the
    block instead of failing the turn, and says so."""
    api = _BindingApi(rewrite_first=True)
    provider, history = _provider(api), _conversation()
    _ask(provider, history)
    history.append({"role": "user", "content": "second question"})
    turn = _ask(provider, history)

    sent = api.requests[-1]
    assert binding.BETA in sent["betas"]
    assert sent["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert turn.thinking_dropped == {"api": 1}


def test_a_route_that_refuses_the_controls_is_asked_without_them_from_then_on():
    api = _BindingApi(reject_controls=True)
    provider, history = _provider(api), _conversation()
    turn = _ask(provider, history)

    assert turn.text == "answer 1"
    assert "block_binding" in api.requests[0]["thinking"] and "block_binding" not in api.requests[1]["thinking"]
    history.append({"role": "user", "content": "second question"})
    _ask(provider, history)
    assert len(api.requests) == 3 and "betas" not in api.requests[-1]


def test_a_refused_thought_is_answered_once_without_thinking_and_never_sent_again():
    api = _BindingApi(rewrite_first=True, reject_controls=True)
    provider, history = _provider(api), _conversation()
    _ask(provider, history)  # the controls are refused here and latched off
    history.append({"role": "user", "content": "second question"})
    turn = _ask(provider, history)

    assert turn.text == "answer 2"
    assert turn.thinking_dropped == {"retried": 1}
    assert _signatures(api.requests[-2]) == ["sig-1"] and _signatures(api.requests[-1]) == []

    history.append({"role": "user", "content": "third question"})
    before = len(api.requests)
    turn = _ask(provider, history)
    # sig-1 is not tried again. The gateway rewrites every request anew, so sig-2 is refused too,
    # and that request goes once more without it.
    assert _signatures(api.requests[before]) == ["sig-2"]
    assert turn.text == "answer 3" and turn.thinking_dropped == {"stale": 1, "retried": 1}


def test_a_second_refusal_after_the_retry_is_the_turns_error():
    api = _BindingApi()
    provider = _provider(api)
    history = _conversation()
    history.append({
        "role": "assistant", "content": "earlier",
        "_anthropic": {"blocks": [{"type": "thinking", "thinking": "t", "signature": "unknown"}]},
    })
    history.append({"role": "user", "content": "again"})

    def refuse(**request):
        raise BadRequestError(_REFUSAL % 2)

    api.messages = SimpleNamespace(create=refuse)
    api.beta = SimpleNamespace(messages=SimpleNamespace(create=refuse))
    with pytest.raises(BadRequestError):
        provider.complete(model=MODEL, messages=history, tools=TOOLS)


def test_a_thought_only_answer_keeps_a_message_when_its_thought_goes():
    api = _BindingApi()
    provider, history = _provider(api), _conversation()
    history.append({
        "role": "assistant", "content": "",
        "_anthropic": {"blocks": [{"type": "thinking", "thinking": "t", "signature": "x"}], "prefix": "not-this-one"},
    })
    history.append({"role": "user", "content": "next"})
    turn = _ask(provider, history)

    assert turn.thinking_dropped == {"stale": 1}
    assert api.requests[-1]["messages"][1] == {"role": "assistant", "content": [{"type": "text", "text": "(no answer text)"}]}


def test_the_subscription_keeps_its_own_betas_beside_the_controls():
    """The SDK's `betas` replaces the client's default `anthropic-beta` header, which on the
    subscription carries the OAuth beta every call needs."""
    api = _BindingApi()
    provider = AnthropicSubscriptionProvider(client=api, thinking_budget=8192)
    provider.complete(model=MODEL, messages=_conversation(), tools=TOOLS)

    betas = api.requests[-1]["betas"]
    assert betas[:2] == ["oauth-2025-04-20", "interleaved-thinking-2025-05-14"] and binding.BETA in betas


def test_the_session_log_says_when_a_step_went_without_its_thinking(tmp_path):
    """The model saw less than the transcript holds: the step's log row says how much."""
    import asyncio

    from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient, StreamChunk
    from harness.loop import TurnEngine
    from harness.safety.permissions import PermissionEngine
    from harness.session.session_log import SessionLog
    from harness.tools import ToolRegistry

    class _Answers(ProviderClient):
        def stream(self, *, model, messages, tools=None, **settings):
            yield StreamChunk(turn=AssistantTurn(text="done", thinking_dropped={"stale": 2}))

        def complete(self, **_):
            raise AssertionError("the engine streams")

        def capabilities(self, model):
            return ModelCapabilities()

    engine = TurnEngine(
        provider=_Answers(), registry=ToolRegistry(), permissions=PermissionEngine(workspace_root=tmp_path), model=MODEL
    )
    engine.log = SessionLog(tmp_path / "events.jsonl")

    async def go():
        return [event async for event in engine.run("hello")]

    asyncio.run(go())
    rows = [row for row in engine.log.read() if row["type"] == "thinking/dropped"]
    assert len(rows) == 1 and rows[0]["stale"] == 2 and rows[0]["model"] == MODEL


def test_a_request_without_thinking_carries_no_controls():
    api = _BindingApi()
    AnthropicProvider(client=api).complete(model="claude-haiku-4-5", messages=_conversation())
    assert "betas" not in api.requests[-1] and "thinking" not in api.requests[-1]
