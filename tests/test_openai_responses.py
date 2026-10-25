"""The OpenAI Responses provider: how history becomes Responses input, how a reply becomes a turn,
how reasoning is kept so the next request can replay it, and how a rejected parameter is retried away.

No SDK and no network: `_Responses` stands in for `client.responses.create`, built from dicts and
`SimpleNamespace` the way the SDK's own objects read.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from harness.llm.providers.responses import OpenAIResponsesProvider
from harness.llm.providers.responses.convert import convert_messages, convert_tools
from harness.llm.providers.responses.request import repair

ASK = [{"role": "user", "content": "x"}]


class _Responses:
    """`responses.create`: raises the queued errors first, then answers with `reply`, or with the
    `events` iterator when the request streams. Every request is kept."""

    def __init__(self, reply=None, events=(), errors=()):
        self.requests: list[dict] = []
        pending = list(errors)

        def create(**request):
            self.requests.append(request)
            if pending:
                raise pending.pop(0)
            return iter(events) if request.get("stream") else reply

        self.responses = SimpleNamespace(create=create)

    @property
    def last(self) -> dict:
        return self.requests[-1]


def _reply(*items, status="completed", why=None, usage=None):
    reply = SimpleNamespace(output=list(items), status=status, incomplete_details=why)
    if usage is not None:
        reply.usage = usage
    return reply


def _said(text):
    return {"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def _thought(*summary, sealed="enc-blob"):
    item = {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": s} for s in summary]}
    if sealed:
        item["encrypted_content"] = sealed
    return item


def _call(call_id, name, arguments):
    return {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name, "arguments": arguments}


def _event(kind, **fields):
    return SimpleNamespace(type=kind, **fields)


def _usage(fresh_and_cached, output, cached=None):
    details = {"input_tokens_details": SimpleNamespace(cached_tokens=cached)} if cached is not None else {}
    return SimpleNamespace(input_tokens=fresh_and_cached, output_tokens=output, **details)


def _complete(reply, messages=ASK, **settings):
    client = _Responses(reply=reply)
    turn = OpenAIResponsesProvider(client=client).complete(model="m", messages=messages, **settings)
    return turn, client


def _streamed(*events):
    return list(OpenAIResponsesProvider(client=_Responses(events=events)).stream(model="m", messages=ASK))


# -- the client ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ({"api_key": "ark-key", "base_url": "https://ark.example/api/v3/"}, {"api_key": "ark-key", "base_url": "https://ark.example/api/v3"}),
        # Stock OpenAI is never handed a vendor's base URL.
        ({"api_key": "openai-key"}, {"api_key": "openai-key"}),
    ],
)
def test_the_sdk_client_gets_the_key_and_only_a_base_url_that_was_given(monkeypatch, given, sent):
    built: dict = {}
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: built.update(kwargs) or SimpleNamespace())
    OpenAIResponsesProvider(**given)._ensure_client()
    assert built == sent


def test_without_a_key_the_client_is_refused_by_name(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No model API key"):
        OpenAIResponsesProvider()._ensure_client()


def test_a_blank_endpoint_routes_openai_to_responses_and_a_custom_one_to_chat_completions():
    from harness.llm.providers import OpenAIProvider
    from harness.llm.providers.registry import build_provider_client

    for settings in ({}, {"base_url": "   "}):
        assert isinstance(build_provider_client("openai", settings, None), OpenAIResponsesProvider)
    # Azure, vLLM or any compatible gateway keep Chat Completions, as do Ollama and the vendors.
    assert isinstance(build_provider_client("openai", {"base_url": "https://my.azure.example/openai/v1"}, None), OpenAIProvider)
    assert isinstance(build_provider_client("ollama", {}, None), OpenAIProvider)
    assert isinstance(build_provider_client("deepseek", {"api_key": "sk-x"}, None), OpenAIProvider)


# -- history into Responses input ----------------------------------------------------------------


def test_leading_system_messages_become_the_instructions_and_a_later_one_stays_in_place():
    instructions, items = convert_messages(
        [{"role": "system", "content": "be helpful"}, {"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    )
    assert instructions == "be helpful\n\nbe brief" and items == [{"role": "user", "content": "hi"}]
    _, items = convert_messages([{"role": "user", "content": "hi"}, {"role": "system", "content": "steering"}])
    assert items[1] == {"role": "system", "content": "steering"}


def test_user_text_images_and_files_become_input_parts():
    png, pdf = "data:image/png;base64,iVBORw0KGgo=", "data:application/pdf;base64,JVBERi0="
    _, items = convert_messages([{
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": png}},
            {"type": "file", "file": {"filename": "report.pdf", "file_data": pdf}},
        ],
    }])
    assert items[0]["content"] == [
        {"type": "input_text", "text": "what is this"},
        {"type": "input_image", "image_url": png},
        {"type": "input_file", "filename": "report.pdf", "file_data": pdf},
    ]


def test_history_from_another_provider_is_rebuilt_and_its_call_ids_still_pair():
    _, items = convert_messages([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "on it", "tool_calls": [{"id": "toolu_abc", "type": "function", "function": {"name": "f", "arguments": '{"x": 1}'}}]},
        {"role": "tool", "tool_call_id": "toolu_abc", "content": '{"ok": true}'},
    ])
    assert items[1:] == [
        {"role": "assistant", "content": "on it"},
        {"type": "function_call", "call_id": "toolu_abc", "name": "f", "arguments": '{"x": 1}'},
        {"type": "function_call_output", "call_id": "toolu_abc", "output": '{"ok": true}'},
    ]


def test_a_turn_this_provider_wrote_is_replayed_from_its_own_items_and_nothing_is_added():
    kept = [_thought("thinking", sealed="blob"), _said("on it"), _call("call_1", "f", "{}")]
    _, items = convert_messages([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "on it", "tool_calls": [{"id": "call_1", "function": {"name": "f", "arguments": "{}"}}], "_openai": {"items": kept}},
        {"role": "tool", "tool_call_id": "call_1", "content": "done"},
    ])
    assert items[1:4] == kept and items[4]["type"] == "function_call_output"


def test_another_providers_sidecar_is_ignored_and_an_empty_text_adds_no_message():
    _, items = convert_messages([{"role": "user", "content": "go"}, {"role": "assistant", "content": "hi", "_gemini": {"text_sig": "abc"}}])
    assert items[1] == {"role": "assistant", "content": "hi"}
    _, items = convert_messages([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}]},
    ])
    assert [item.get("type") for item in items[1:]] == ["function_call"]


def test_tool_schemas_are_flat_and_never_strict():
    tools = convert_tools([
        {"type": "function", "function": {"name": "bare"}},
        {"type": "function", "function": {"name": "full", "description": "does things", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}},
    ])
    assert tools[0] == {"type": "function", "name": "bare", "strict": False}
    assert tools[1]["name"] == "full" and "function" not in tools[1]
    assert tools[1]["parameters"]["properties"] == {"x": {"type": "integer"}}
    assert convert_tools(None) == []


def test_a_tool_is_not_held_to_a_strict_schema():
    """The Responses API is strict unless told otherwise, and strict forbids what a schema does not
    list: gpt-6-luna could send `show_visual` only `{"blocks":[{"type":"chart"}]}` (a real session,
    2026-10-09, ten refusals in a row). Every tool says it is not strict, as on the chat wire."""
    from harness.tools.visual import visual_tools

    [show] = visual_tools(".")
    [entry] = convert_tools([show.__tool_schema__])
    assert entry["strict"] is False
    block = entry["parameters"]["properties"]["blocks"]["items"]["properties"]
    assert list(block) == ["type"]


# -- the request ---------------------------------------------------------------------------------


def test_a_request_keeps_nothing_on_the_server_and_asks_for_sealed_reasoning():
    client = _Responses(reply=_reply(_said("hello")))
    turn = OpenAIResponsesProvider(client=client).complete(model="gpt-5.6-sol", messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
    assert (turn.text, turn.finish_reason, turn.has_tool_calls, turn.extras) == ("hello", "stop", False, {})
    sent = client.last
    assert (sent["model"], sent["instructions"], sent["store"]) == ("gpt-5.6-sol", "sys", False)
    assert sent["include"] == ["reasoning.encrypted_content"] and sent["reasoning"] == {"summary": "auto"}


def test_a_provider_that_rejects_reasoning_summaries_still_gets_sealed_reasoning():
    """BytePlus accepts encrypted reasoning output but rejects reasoning.summary."""
    client = _Responses(reply=_reply(_said("hello")))
    OpenAIResponsesProvider(client=client, reasoning_summary=False).complete(model="m", messages=ASK)
    assert "reasoning" not in client.last and client.last["include"] == ["reasoning.encrypted_content"]
    with pytest.raises(TypeError, match="reasoning_summary must be a bool"):
        OpenAIResponsesProvider(client=SimpleNamespace(), reasoning_summary="auto")


def test_settings_are_renamed_or_dropped_to_what_the_responses_api_takes():
    _, client = _complete(_reply(_said("x")), temperature=0.2, max_tokens=512, frequency_penalty=0.5, reasoning_effort="high")
    sent = client.last
    assert sent["temperature"] == 0.2 and sent["max_output_tokens"] == 512
    assert not {"max_tokens", "frequency_penalty", "reasoning_effort"} & set(sent)


def test_tools_are_sent_flat():
    _, client = _complete(_reply(_said("x")), tools=[{"type": "function", "function": {"name": "f"}}])
    assert client.last["tools"] == [{"type": "function", "name": "f", "strict": False}]


# -- a reply into a turn -------------------------------------------------------------------------


def test_usage_counts_cached_input_apart_from_fresh_input():
    """The Responses API counts cached tokens inside `input_tokens`; every adapter reports fresh
    input and the cached share apart. Before this, Responses-routed models metered as 0 tokens."""
    turn, _ = _complete(_reply(_said("hello"), usage=_usage(1500, 80, cached=1400)))
    assert (turn.usage.input, turn.usage.output, turn.usage.cache_read) == (100, 80, 1400)
    # An older server may leave out the details, or the whole usage: never a crash, never a made-up zero.
    turn, _ = _complete(_reply(_said("x"), usage=_usage(500, 20)))
    assert (turn.usage.input, turn.usage.output, turn.usage.cache_read) == (500, 20, 0)
    assert _complete(_reply(_said("y")))[0].usage is None


def test_function_calls_keep_their_ids_and_unreadable_arguments_are_kept_raw():
    turn, _ = _complete(_reply(_said("on it"), _call("call_a", "write_file", '{"path": "a.txt"}'), _call("call_b", "read_file", "not json")))
    assert (turn.text, turn.finish_reason) == ("on it", "tool_calls")
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("call_a", "write_file", {"path": "a.txt"}),
        ("call_b", "read_file", {"_raw": "not json"}),
    ]


def test_the_sdks_typed_objects_read_like_dicts():
    message = SimpleNamespace(type="message", id="msg_1", role="assistant", content=[SimpleNamespace(type="output_text", text="hi", annotations=None)])
    call = SimpleNamespace(type="function_call", id="fc_1", call_id="call_1", name="f", arguments='{"a": 1}')
    turn, _ = _complete(SimpleNamespace(output=[message, call], status="completed", incomplete_details=None))
    assert turn.text == "hi" and turn.tool_calls[0].id == "call_1" and turn.tool_calls[0].arguments == {"a": 1}


def test_reasoning_is_shown_and_kept_for_replay_only_when_it_is_sealed():
    items = [_thought("plan a", " then b", sealed="blob"), _said("answer"), _call("call_1", "f", "{}")]
    turn, _ = _complete(_reply(*items))
    assert turn.reasoning == "plan a then b" and turn.extras["_openai"]["items"] == items
    # Without its sealed content, replaying it under store=false would be refused: shown, not kept.
    turn, _ = _complete(_reply(_thought("hmm", sealed=None), _call("call_1", "f", "{}")))
    assert turn.reasoning == "hmm"
    assert [item["type"] for item in turn.extras["_openai"]["items"]] == ["function_call"]
    assert _complete(_reply(_said("plain")))[0].extras == {}


def test_a_reply_cut_at_the_output_limit_ends_on_length():
    turn, _ = _complete(_reply(_said("truncat"), status="incomplete", why=SimpleNamespace(reason="max_output_tokens")))
    assert turn.finish_reason == "length"


def test_what_one_turn_kept_is_exactly_what_the_next_request_replays():
    items = [_thought("plan", sealed="blob"), _call("call_1", "f", "{}")]
    turn, _ = _complete(_reply(*items), messages=[{"role": "user", "content": "go"}])
    # Stored the way the engine stores an assistant message: canonical fields plus extras.
    stored = {
        "role": "assistant",
        "content": turn.text or "",
        "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in turn.tool_calls],
        **turn.extras,
    }
    _, client = _complete(_reply(_said("done")), messages=[{"role": "user", "content": "go"}, stored, {"role": "tool", "tool_call_id": "call_1", "content": "ok"}])
    sent = client.last["input"]
    assert sent[1:3] == items
    assert sent[3] == {"type": "function_call_output", "call_id": "call_1", "output": "ok"}


# -- a rejected parameter ------------------------------------------------------------------------


def test_the_parameter_an_error_names_is_dropped_from_a_copy_of_the_request():
    request = {"model": "m", "input": [], "temperature": 0.2}
    assert "temperature" not in repair(request, Exception("Unsupported parameter: 'temperature' is not supported"))
    assert request["temperature"] == 0.2
    # A dotted name drops its top-level parameter.
    dotted = repair({"model": "m", "input": [], "reasoning": {"summary": "auto"}}, Exception("Unsupported parameter: 'reasoning.summary'"))
    assert "reasoning" not in dotted
    with pytest.raises(Exception, match="rate limit"):
        repair({"model": "m", "input": []}, Exception("rate limit exceeded"))


def test_a_model_that_rejects_reasoning_and_include_is_asked_again_without_them():
    client = _Responses(
        reply=_reply(_said("ok")),
        errors=[Exception("Unsupported parameter: 'reasoning'"), Exception("Unsupported value: 'include[0]'")],
    )
    turn = OpenAIResponsesProvider(client=client).complete(model="gpt-4.1", messages=ASK)
    assert turn.text == "ok" and len(client.requests) == 3
    assert "reasoning" not in client.last and "include" not in client.last


# -- streaming -----------------------------------------------------------------------------------


def test_a_stream_asks_to_stream():
    client = _Responses()
    list(OpenAIResponsesProvider(client=client).stream(model="m", messages=ASK))
    assert client.last["stream"] is True


def test_a_stream_yields_its_deltas_then_the_turn_the_completed_event_holds():
    chunks = _streamed(
        _event("response.created"),
        _event("response.reasoning_summary_text.delta", delta="mull "),
        _event("response.reasoning_summary_text.delta", delta="it over"),
        _event("response.output_text.delta", delta="hel"),
        _event("response.output_text.delta", delta="lo"),
        _event("response.completed", response=_reply(_thought("mull it over", sealed="blob"), _said("hello"))),
    )
    assert [c.reasoning_delta for c in chunks if c.reasoning_delta] == ["mull ", "it over"]
    assert [c.text_delta for c in chunks if c.text_delta] == ["hel", "lo"]
    turn = chunks[-1].turn
    assert (turn.text, turn.reasoning, turn.finish_reason) == ("hello", "mull it over", "stop")
    # Sealed reasoning is kept for replay even when no function was called.
    assert [item["type"] for item in turn.extras["_openai"]["items"]] == ["reasoning", "message"]


def test_a_streamed_turn_carries_its_calls_and_what_it_keeps_for_replay():
    turn = _streamed(_event("response.completed", response=_reply(_thought("plan", sealed="blob"), _call("call_1", "f", '{"x": 1}'))))[-1].turn
    assert turn.finish_reason == "tool_calls" and turn.tool_calls[0].arguments == {"x": 1}
    assert [item["type"] for item in turn.extras["_openai"]["items"]] == ["reasoning", "function_call"]


def test_a_stream_whose_final_event_lists_nothing_is_rebuilt_from_the_items_it_streamed():
    """On the subscription backend response.completed carries an empty `output`: the items only
    ever come as they stream. Built from that event alone a turn loses its text and its calls without
    a word (it happened: the deltas showed, and an empty turn was saved), so it is built from the
    output_item.done events."""
    turn = _streamed(
        _event("response.output_text.delta", delta="pong"),
        _event("response.output_item.done", item=_said("pong")),
        _event("response.output_item.done", item=_call("call_1", "f", '{"x": 1}')),
        _event("response.completed", response=_reply()),
    )[-1].turn
    assert (turn.text, turn.finish_reason, turn.tool_calls[0].arguments) == ("pong", "tool_calls", {"x": 1})


def test_a_stream_with_no_items_anywhere_is_its_deltas():
    turn = _streamed(
        _event("response.output_text.delta", delta="po"),
        _event("response.output_text.delta", delta="ng"),
        _event("response.completed", response=_reply()),
    )[-1].turn
    assert (turn.text, turn.finish_reason) == ("pong", "stop")


def test_a_stream_that_never_ends_keeps_its_text_and_invents_no_usage():
    turn = _streamed(_event("response.output_text.delta", delta="partial"))[-1].turn
    assert (turn.text, turn.finish_reason, turn.usage) == ("partial", None, None)


def test_a_streams_usage_is_read_from_its_completed_event():
    final = _reply(_said("done"), usage=_usage(1430, 65, cached=1408))
    turn = _streamed(_event("response.output_text.delta", delta="done"), _event("response.completed", response=final))[-1].turn
    assert (turn.usage.input, turn.usage.output, turn.usage.cache_read) == (22, 65, 1408)
