"""Every provider, through its REAL SDK, over a REAL socket — offline.

The rest of the suite injects fake SDK clients, so an SDK upgrade that changes the
transport or the reply parsing is invisible to it: `anthropic` 1.0 (httpx2) broke every
Anthropic-wire request and 4,500 tests stayed green. Here each provider builds its own SDK
client exactly as in production, points it at `provider_wire_fakes.WireServer`, and must
turn a text reply and a tool-call reply — whole and streamed — into the same
`AssistantTurn`: the text, the call, and the usage split (fresh / cache read / output).
"""

from __future__ import annotations

import pytest

from harness.llm.providers.claude import AnthropicProvider
from harness.llm.providers.gemini import GeminiProvider
from harness.llm.providers.chat import OpenAIProvider
from harness.llm.providers.responses import OpenAIResponsesProvider

from provider_wire_fakes import TEXT, TOOL, USAGE, WireServer

MESSAGES = [{"role": "user", "content": "ping"}]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
]

PROVIDERS = {
    "openai-chat": lambda url: OpenAIProvider(api_key="sk-test", base_url=f"{url}/v1"),
    "openai-responses": lambda url: OpenAIResponsesProvider(api_key="sk-test", base_url=f"{url}/v1"),
    "anthropic": lambda url: AnthropicProvider(api_key="sk-ant-test", base_url=url),
    "gemini": lambda url: GeminiProvider(api_key="g-test", base_url=url),
}


def _drive(provider, mode: str, tools):
    if mode == "complete":
        return provider.complete(model="m", messages=MESSAGES, tools=tools)
    streamed, final = [], None
    for chunk in provider.stream(model="m", messages=MESSAGES, tools=tools):
        if getattr(chunk, "text_delta", None):
            streamed.append(chunk.text_delta)
        if getattr(chunk, "turn", None) is not None:
            final = chunk.turn
    assert final is not None, "the stream never delivered its final turn"
    if streamed and not tools:
        assert "".join(streamed) == TEXT
    return final


@pytest.mark.parametrize("mode", ["complete", "stream"])
@pytest.mark.parametrize("wire", sorted(PROVIDERS))
def test_a_text_reply_round_trips_through_the_real_sdk(wire, mode):
    with WireServer(tool=False) as server:
        turn = _drive(PROVIDERS[wire](server.url), mode, None)
    assert (turn.text or "").strip() == TEXT
    assert not turn.tool_calls
    usage = turn.usage
    assert usage is not None, f"{wire} parsed no usage"
    assert usage.output == USAGE["output"]
    assert usage.input + usage.cache_read + usage.cache_write == USAGE["input"] + USAGE["cache_read"]
    assert usage.cache_read == USAGE["cache_read"], f"{wire} lost the cache split"
    assert server.requests, "nothing reached the socket"


@pytest.mark.parametrize("mode", ["complete", "stream"])
@pytest.mark.parametrize("wire", sorted(PROVIDERS))
def test_a_tool_call_round_trips_through_the_real_sdk(wire, mode):
    with WireServer(tool=True) as server:
        turn = _drive(PROVIDERS[wire](server.url), mode, TOOLS)
    assert [c.name for c in turn.tool_calls] == [TOOL["name"]]
    assert turn.tool_calls[0].arguments == TOOL["arguments"]
    sent = server.requests[-1]["body"]
    assert "read_file" in str(sent), f"{wire} did not send the tool schema"


def test_prompt_counts_that_arrive_in_message_delta_are_read():
    """MiniMax on opencode-go's Anthropic wire opens the stream with input_tokens=0 and
    sends the real prompt counts in `message_delta`. Read from `message_start` alone the
    session looked empty (input 0), so compaction could never fire."""
    with WireServer(anthropic_delta_usage=True) as server:
        turn = _drive(PROVIDERS["anthropic"](server.url), "stream", None)
    usage = turn.usage
    assert (usage.input, usage.cache_read, usage.output) == (
        USAGE["input"], USAGE["cache_read"], USAGE["output"]
    )
