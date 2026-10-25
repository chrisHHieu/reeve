"""Token usage: what each provider reports, read into one shape (fresh input, output, cache read and
cache write), and how the engine carries it to the event, the saved message and the context gauge.

Each provider's answer is built from `SimpleNamespace` the way its SDK's objects read; Bedrock's
Converse stream is plain dicts, as boto3 gives it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aisuite as ai

from harness.llm.providers import AssistantTurn, ModelCapabilities, ProviderClient
from harness.llm.providers.base import TokenUsage
from harness.llm.providers.bedrock.converse import ConverseClient
from harness.llm.providers.chat import OpenAIProvider
from harness.llm.providers.chat.usage import usage_from
from harness.llm.providers.claude import AnthropicProvider
from harness.llm.providers.gemini import GeminiProvider
from harness.llm.providers.matrix import model_context_windows
from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.session.events import EventType
from harness.tools import ToolRegistry

ASK = [{"role": "user", "content": "x"}]


def _streamed_turn(provider):
    return list(provider.stream(model="m", messages=ASK))[-1].turn


def test_the_context_a_request_used_is_everything_on_its_prompt_side():
    usage = TokenUsage(input=100, output=50, cache_read=300, cache_write=20)
    assert usage.context_tokens == 420
    assert usage.as_dict() == {"input": 100, "output": 50, "cache_read": 300, "cache_write": 20}


# -- Anthropic -----------------------------------------------------------------------------------


def _anthropic(answer):
    create = lambda **request: answer  # noqa: E731
    return AnthropicProvider(client=SimpleNamespace(messages=SimpleNamespace(create=create), beta=SimpleNamespace(messages=SimpleNamespace(create=create))))


def test_an_anthropic_stream_takes_input_from_its_start_and_output_from_its_last_delta():
    started = SimpleNamespace(usage=SimpleNamespace(input_tokens=7, output_tokens=1, cache_read_input_tokens=100, cache_creation_input_tokens=25))
    turn = _streamed_turn(_anthropic([
        SimpleNamespace(type="message_start", message=started),
        SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text")),
        SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text="hi")),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn"), usage=SimpleNamespace(output_tokens=42)),
        SimpleNamespace(type="message_stop"),
    ]))
    assert turn.usage == TokenUsage(input=7, output=42, cache_read=100, cache_write=25)


def test_an_anthropic_reply_carries_its_usage_and_a_stream_without_one_has_none():
    reply = SimpleNamespace(content=[SimpleNamespace(type="text", text="hi")], stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    assert _anthropic(reply).complete(model="m", messages=ASK).usage == TokenUsage(input=10, output=5)
    bare = [SimpleNamespace(type="message_start"), SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn"))]
    assert _streamed_turn(_anthropic(bare)).usage is None


# -- the OpenAI wire -----------------------------------------------------------------------------


class _Chat:
    def __init__(self, answer, *, refuses_stream_options=False):
        self.requests = []

        def create(**request):
            self.requests.append(request)
            if refuses_stream_options and "stream_options" in request:
                raise RuntimeError("unknown parameter: 'stream_options'")
            return answer

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _pieces(usage):
    said = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop")], usage=None)
    # Usage comes on a last chunk with no choices (the include_usage contract).
    return [said, SimpleNamespace(choices=[], usage=usage)]


def test_a_stream_asks_for_usage_and_the_cached_share_is_taken_out_of_the_input():
    client = _Chat(_pieces(SimpleNamespace(prompt_tokens=140, completion_tokens=9, prompt_tokens_details=SimpleNamespace(cached_tokens=40))))
    turn = _streamed_turn(OpenAIProvider(client=client))
    assert client.requests[0]["stream_options"] == {"include_usage": True}
    assert turn.usage == TokenUsage(input=100, output=9, cache_read=40)


def test_a_deepseek_cache_hit_is_counted_as_cache_read():
    """DeepSeek reports the number flat as `prompt_cache_hit_tokens` rather than nesting it
    in `prompt_tokens_details`. Uncounted hits also disabled cache-aware compaction replay,
    which gates on `cache_read` (engine/compaction.py)."""
    deepseek = SimpleNamespace(prompt_tokens=283, completion_tokens=69, prompt_cache_hit_tokens=256)
    assert usage_from(deepseek) == TokenUsage(input=27, output=69, cache_read=256)
    assert _streamed_turn(OpenAIProvider(client=_Chat(_pieces(deepseek)))).usage == TokenUsage(input=27, output=69, cache_read=256)


def test_the_openai_spelling_wins_over_the_flat_one():
    both = SimpleNamespace(prompt_tokens=283, completion_tokens=69, prompt_tokens_details=SimpleNamespace(cached_tokens=256), prompt_cache_hit_tokens=1)
    assert usage_from(both) == TokenUsage(input=27, output=69, cache_read=256)


def test_a_server_that_refuses_stream_options_is_asked_again_without_them():
    client = _Chat(_pieces(None), refuses_stream_options=True)
    turn = _streamed_turn(OpenAIProvider(client=client))
    assert "stream_options" not in client.requests[-1] and turn.text == "hi"  # only the count is lost


def test_a_reply_without_cache_details_counts_all_its_input_as_fresh():
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop")], usage=SimpleNamespace(prompt_tokens=30, completion_tokens=4, prompt_tokens_details=None))
    assert OpenAIProvider(client=_Chat(reply)).complete(model="m", messages=ASK).usage == TokenUsage(input=30, output=4)


# -- Gemini and Bedrock --------------------------------------------------------------------------


def test_gemini_reports_running_totals_so_the_last_chunk_is_the_count_and_thinking_is_output():
    def chunk(text, output, thoughts):
        usage = SimpleNamespace(prompt_token_count=90, candidates_token_count=output, cached_content_token_count=50, thoughts_token_count=thoughts)
        candidate = SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=text, function_call=None)]), finish_reason=SimpleNamespace(name="STOP"))
        return SimpleNamespace(candidates=[candidate], usage_metadata=usage)

    chunks = [chunk("he", 1, 0), chunk("y", 12, 6)]
    models = SimpleNamespace(generate_content=lambda **r: chunks[0], generate_content_stream=lambda **r: iter(chunks))
    assert _streamed_turn(GeminiProvider(client=SimpleNamespace(models=models))).usage == TokenUsage(input=40, output=18, cache_read=50)


def test_bedrocks_converse_stream_reports_usage_in_its_metadata_event():
    events = [
        {"contentBlockDelta": {"delta": {"text": "hi"}, "contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 11, "outputTokens": 3, "cacheReadInputTokens": 8, "cacheWriteInputTokens": 2}}},
    ]
    client = ConverseClient(client=SimpleNamespace(converse_stream=lambda **request: {"stream": events}))
    assert _streamed_turn(client).usage == TokenUsage(input=11, output=3, cache_read=8, cache_write=2)


# -- the engine ----------------------------------------------------------------------------------


class _Counted(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="done", finish_reason="stop", usage=TokenUsage(input=100, output=20, cache_read=5))

    def capabilities(self, model):
        return ModelCapabilities()


def _turn(tmp_path):
    registry = ToolRegistry()
    registry.register_all(ai.toolkits.files(root=str(tmp_path), allow_write=True))
    engine = TurnEngine(provider=_Counted(), registry=registry, permissions=PermissionEngine(workspace_root=tmp_path), model="gpt-5.5")

    async def run():
        return [event async for event in engine.run("hello")]

    events = asyncio.run(run())
    said = next(e for e in events if e.type == EventType.ASSISTANT_MESSAGE).data["usage"]
    saved = next(m for m in engine.messages if m.get("role") == "assistant")["usage"]
    return engine, said, saved


def test_the_event_and_the_saved_message_carry_the_count_and_the_model(tmp_path):
    _, said, saved = _turn(tmp_path)
    expected = {"model": "gpt-5.5", "input": 100, "output": 20, "cache_read": 5, "cache_write": 0}
    # The billed numbers are exact; added keys (the model, the context makeup) are left to readers that know them.
    assert expected.items() <= said.items() and expected.items() <= saved.items()


def test_usage_sidecar_carries_the_context_breakdown(tmp_path):
    """The additive makeup-panel estimate rides INSIDE the usage sidecar on both the
    event and the persisted message — measured from the request this usage was billed
    for. Keyed categories; every panel category present even when zero."""
    _, said, saved = _turn(tmp_path)
    for usage in (said, saved):
        makeup = usage["context_breakdown"]
        assert set(makeup) == {"messages", "mcp_tools", "system_tools", "skills", "instructions", "system_prompt", "meta_context"}
        assert makeup["messages"] > 0 and makeup["system_tools"] > 0  # "hello" and the file tools were sent
        assert makeup["skills"] == 0  # no context provider on this engine


def test_the_count_is_never_sent_back_to_a_model(tmp_path):
    engine, _, _ = _turn(tmp_path)
    assert all("usage" not in m for m in engine._outbound_messages())


def test_only_verified_context_windows_are_known():
    windows = model_context_windows()
    assert windows["anthropic:claude-fable-5"] == 1_000_000
    assert "together:thinkingmachines/Inkling" not in windows
    assert all(isinstance(v, int) and v > 0 for v in windows.values())
