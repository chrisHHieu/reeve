"""The native Anthropic provider: history into Messages API blocks, a reply into a turn, streaming,
extended thinking kept for replay, the refusal fallback, and server-side context editing.

No SDK calls leave the test: `_Messages` stands in for `client.messages.create` (and the beta
endpoint Fable and context editing go through), built from `SimpleNamespace` like the SDK's objects.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from harness.llm.providers import AnthropicProvider, capabilities_for
from harness.llm.providers.claude.convert import convert_messages, convert_tools
from harness.llm.providers.claude.thinking import DEFAULT_MAX_TOKENS

ASK = [{"role": "user", "content": "x"}]


class _Messages:
    """`messages.create` and `beta.messages.create`: answers with `reply`, or the `events` iterator
    when the request streams, and keeps the last request."""

    def __init__(self, reply=None, events=()):
        self.request: dict = {}

        def create(**request):
            self.request = request
            return iter(events) if request.get("stream") else reply

        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def _block(kind, **fields):
    return SimpleNamespace(type=kind, **fields)


def _reply(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks) or [_block("text", text="hello")], stop_reason=stop)


def _asked_for(*calls):
    """An assistant turn that only called tools, stored the way the engine stores one."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": args}} for cid, name, args in calls],
    }


def _delta(index, **delta):
    return SimpleNamespace(type="content_block_delta", index=index, delta=SimpleNamespace(**delta))


def _opens(index, kind, **fields):
    return SimpleNamespace(type="content_block_start", index=index, content_block=SimpleNamespace(type=kind, **fields))


def _stops(reason):
    return SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=reason))


def _complete(reply=None, model="m", messages=ASK, **provider):
    client = _Messages(reply=reply if reply is not None else _reply())
    turn = AnthropicProvider(client=client, **provider).complete(model=model, messages=messages)
    return turn, client.request


def _stream(*events, **provider):
    return list(AnthropicProvider(client=_Messages(events=events), **provider).stream(model="m", messages=ASK))


# -- history into Messages blocks ----------------------------------------------------------------


def test_the_leading_system_message_is_lifted_out_and_text_becomes_a_block():
    system, messages = convert_messages([{"role": "system", "content": "be helpful"}, {"role": "user", "content": "hi"}])
    assert system == "be helpful"
    assert messages == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


def test_a_tool_only_turn_sends_no_empty_text_block():
    _, messages = convert_messages([{"role": "user", "content": "do it"}, _asked_for(("c1", "f", '{"x": 1}'))])
    assert messages[1]["content"] == [{"type": "tool_use", "id": "c1", "name": "f", "input": {"x": 1}}]


def test_the_results_of_parallel_calls_share_one_user_message():
    _, messages = convert_messages([
        {"role": "user", "content": "go"},
        _asked_for(("c1", "a", "{}"), ("c2", "b", "{}")),
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "tool", "tool_call_id": "c2", "content": "r2"},
    ])
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert [(b["type"], b["tool_use_id"], b["content"]) for b in messages[2]["content"]] == [
        ("tool_result", "c1", "r1"), ("tool_result", "c2", "r2"),
    ]


def test_a_message_typed_after_the_results_joins_them_in_the_same_user_message():
    _, messages = convert_messages([
        {"role": "user", "content": "go"},
        _asked_for(("c1", "a", "{}")),
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "user", "content": "actually, stop"},
    ])
    blocks = messages[2]["content"]
    assert [b["type"] for b in blocks] == ["tool_result", "text"] and blocks[1]["text"] == "actually, stop"


def test_images_go_inline_from_a_data_url_by_reference_from_a_link_and_otherwise_as_a_note():
    _, messages = convert_messages([{
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
            {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
            {"type": "image_url", "image_url": {"url": "not-a-url"}},
        ],
    }])
    _, inline, linked, unreadable = messages[0]["content"]
    assert inline == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}}
    assert linked["source"] == {"type": "url", "url": "https://x/y.png"}
    assert unreadable == {"type": "text", "text": "[unsupported image attachment]"}


def test_a_pdf_becomes_a_document_and_an_unreadable_file_a_note():
    _, messages = convert_messages([{
        "role": "user",
        "content": [
            {"type": "text", "text": "summarize"},
            {"type": "file", "file": {"filename": "report.pdf", "file_data": "data:application/pdf;base64,JVBERi0="}},
            {"type": "file", "file": {"file_data": "not-a-url"}},
        ],
    }])
    _, document, unreadable = messages[0]["content"]
    assert document == {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0="},
        "title": "report.pdf",
    }
    assert unreadable == {"type": "text", "text": "[unsupported file attachment]"}


def test_an_empty_assistant_message_is_dropped_and_the_history_still_opens_with_the_user():
    _, messages = convert_messages([{"role": "assistant", "content": ""}, {"role": "assistant", "content": "hi"}])
    assert messages[0] == {"role": "user", "content": [{"type": "text", "text": "(continued)"}]}
    assert messages[1]["role"] == "assistant"
    with pytest.raises(ValueError):
        convert_messages([{"role": "assistant", "content": ""}])


def test_arguments_that_are_not_json_are_sent_raw():
    _, messages = convert_messages([{"role": "user", "content": "go"}, _asked_for(("c1", "a", "{bad"))])
    assert messages[1]["content"][0]["input"] == {"_raw": "{bad"}


def test_kept_thinking_is_replayed_ahead_of_the_tool_use_it_led_to():
    asked = {**_asked_for(("t1", "a", "{}")), "_anthropic": {"blocks": [{"type": "thinking", "thinking": "plan", "signature": "S"}]}}
    _, messages = convert_messages([{"role": "user", "content": "go"}, asked, {"role": "tool", "tool_call_id": "t1", "content": "ok"}])
    first, second = messages[1]["content"][:2]
    assert first == {"type": "thinking", "thinking": "plan", "signature": "S"} and second["type"] == "tool_use"


def test_a_tool_schema_gets_an_empty_object_schema_when_it_has_none():
    bare, full = convert_tools([
        {"type": "function", "function": {"name": "bare"}},
        {"type": "function", "function": {"name": "full", "description": "does things", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}},
    ])
    assert bare == {"name": "bare", "input_schema": {"type": "object", "properties": {}}}
    assert full["description"] == "does things" and full["input_schema"]["properties"] == {"x": {"type": "integer"}}


# -- the request ---------------------------------------------------------------------------------


def test_a_plain_request_sends_the_system_as_a_cacheable_block_and_the_required_max_tokens():
    turn, sent = _complete(_reply(_block("text", text="hello")), model="claude-sonnet-4-6", messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
    assert (turn.text, turn.finish_reason, turn.has_tool_calls) == ("hello", "stop", False)
    assert sent["model"] == "claude-sonnet-4-6"
    # A block list, so its last block can carry the prompt-cache breakpoint.
    assert sent["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert sent["max_tokens"] == DEFAULT_MAX_TOKENS and "tools" not in sent


def test_settings_are_renamed_or_dropped_to_what_the_messages_api_takes():
    client = _Messages(reply=_reply())
    AnthropicProvider(client=client).complete(model="m", messages=ASK, temperature=0.2, max_tokens=512, stop="END", frequency_penalty=0.5)
    sent = client.request
    assert (sent["temperature"], sent["max_tokens"], sent["stop_sequences"]) == (0.2, 512, ["END"])
    assert "frequency_penalty" not in sent and "stop" not in sent


def test_tools_are_sent_in_the_messages_api_shape():
    client = _Messages(reply=_reply())
    tools = [{"type": "function", "function": {"name": "f", "description": "d", "parameters": {"type": "object"}}}]
    AnthropicProvider(client=client).complete(model="m", messages=ASK, tools=tools)
    assert client.request["tools"] == [{"name": "f", "description": "d", "input_schema": {"type": "object"}}]


def test_without_a_key_the_client_is_refused_by_name(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Anthropic"):
        AnthropicProvider()._ensure_client()


# -- a reply into a turn -------------------------------------------------------------------------


def test_tool_use_blocks_become_tool_calls():
    turn, _ = _complete(_reply(_block("text", text="on it"), _block("tool_use", id="c1", name="write_file", input={"path": "a.txt"}), stop="tool_use"))
    assert (turn.text, turn.finish_reason) == ("on it", "tool_calls")
    call = turn.tool_calls[0]
    assert (call.id, call.name, call.arguments) == ("c1", "write_file", {"path": "a.txt"})


@pytest.mark.parametrize(
    ("stop", "finish"),
    # A refusal is not a finish reason: it raises (`test_a_refusal_that_survives_the_fallback…`).
    [("end_turn", "stop"), ("tool_use", "tool_calls"), ("max_tokens", "length"), ("stop_sequence", "stop"), ("something_new", "something_new")],
)
def test_stop_reasons_map_to_the_engines_finish_reasons(stop, finish):
    assert _complete(_reply(stop=stop))[0].finish_reason == finish


def test_thinking_is_shown_and_kept_for_replay_and_redacted_thinking_is_kept_but_not_shown():
    turn, _ = _complete(
        _reply(_block("thinking", thinking="pondering...", signature="SIG1"), _block("redacted_thinking", data="OPAQUE"), _block("text", text="the answer")),
        model="claude-fable-5", thinking_budget=4096,
    )
    assert (turn.text, turn.reasoning) == ("the answer", "pondering...")
    assert turn.extras["_anthropic"]["blocks"] == [
        {"type": "thinking", "thinking": "pondering...", "signature": "SIG1"},
        {"type": "redacted_thinking", "data": "OPAQUE"},
    ]


def test_a_refusal_that_survives_the_fallback_is_an_error_not_a_blank_reply():
    """Owner-hit 2026-07-23: a blank reply gave no notice and no Retry."""
    refused = SimpleNamespace(content=[], stop_reason="refusal", stop_details=SimpleNamespace(category="cyber", explanation="blocked"))
    with pytest.raises(RuntimeError) as raised:
        _complete(refused, model="claude-fable-5", thinking_budget=8192)
    assert "safety filter" in str(raised.value) and "cyber" in str(raised.value)


# -- extended thinking and the refusal fallback --------------------------------------------------


@pytest.mark.parametrize("model", ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-4-6"])
def test_the_newer_families_think_adaptively_with_a_summary_and_no_sampling_settings(model):
    """Owner-hit 2026-07-23: these refuse `budget_tokens`, and without `display: summarized` the
    trace arrives empty."""
    client = _Messages(reply=_reply())
    AnthropicProvider(client=client, thinking_budget=8192).complete(model=model, messages=ASK, temperature=0.2, top_p=0.9)
    # `block_binding` rides beside any thinking (tested in test_claude_thinking_binding.py).
    thinking = {k: v for k, v in client.request["thinking"].items() if k != "block_binding"}
    assert thinking == {"type": "adaptive", "display": "summarized"}
    assert "temperature" not in client.request and "top_p" not in client.request


def test_an_older_model_thinks_on_a_budget_below_its_max_tokens():
    client = _Messages(reply=_reply())
    AnthropicProvider(client=client, thinking_budget=8192).complete(model="claude-haiku-4-5", messages=ASK, temperature=0.2)
    thinking = {k: v for k, v in client.request["thinking"].items() if k != "block_binding"}
    assert thinking == {"type": "enabled", "budget_tokens": 8192}
    assert client.request["max_tokens"] > 8192 and "temperature" not in client.request


def test_thinking_is_on_unless_the_hidden_profile_budget_turns_it_off():
    """There is no setting for it (owner, 2026-07-23); the profile's `thinking_budget` is a hidden
    override and 0 is off."""
    from harness.llm.providers.claude.thinking import DEFAULT_THINKING_BUDGET
    from harness.llm.providers.registry import build_provider_client

    assert build_provider_client("anthropic", {}, None).thinking_budget == DEFAULT_THINKING_BUDGET
    assert build_provider_client("anthropic", {"thinking_budget": "2048"}, None).thinking_budget == 2048
    assert build_provider_client("anthropic", {"thinking_budget": "0"}, None).thinking_budget == 0
    assert "thinking" not in _complete(model="claude-fable-5")[1]


def test_fable_asks_the_server_to_fall_back_to_opus_and_other_models_do_not():
    """Fable's classifiers can decline a benign request; the fallback re-serves it in the same call."""
    _, fable = _complete(model="claude-fable-5", thinking_budget=8192)
    assert fable["betas"][0] == "server-side-fallback-2026-06-01" and fable["fallbacks"] == [{"model": "claude-opus-4-8"}]
    _, opus = _complete(model="claude-opus-4-8")
    assert "betas" not in opus and "fallbacks" not in opus


# -- streaming -----------------------------------------------------------------------------------


def test_a_stream_asks_to_stream_with_the_required_max_tokens():
    client = _Messages()
    list(AnthropicProvider(client=client).stream(model="m", messages=ASK))
    assert client.request["stream"] is True and client.request["max_tokens"] == DEFAULT_MAX_TOKENS


def test_a_stream_yields_its_text_then_the_turn():
    chunks = _stream(
        SimpleNamespace(type="message_start"),
        _opens(0, "text"),
        _delta(0, type="text_delta", text="hel"),
        _delta(0, type="text_delta", text="lo"),
        SimpleNamespace(type="content_block_stop", index=0),
        _stops("end_turn"),
        SimpleNamespace(type="message_stop"),
    )
    assert [c.text_delta for c in chunks[:-1]] == ["hel", "lo"]
    turn = chunks[-1].turn
    assert (turn.text, turn.finish_reason, turn.has_tool_calls) == ("hello", "stop", False)


def test_a_streamed_tool_calls_json_is_joined_from_its_pieces():
    turn = _stream(
        _opens(0, "tool_use", id="c1", name="write_file"),
        _delta(0, type="input_json_delta", partial_json='{"path": "a'),
        _delta(0, type="input_json_delta", partial_json='.txt", "content": "hi"}'),
        SimpleNamespace(type="content_block_stop", index=0),
        _stops("tool_use"),
    )[-1].turn
    assert turn.finish_reason == "tool_calls"
    assert (turn.tool_calls[0].id, turn.tool_calls[0].arguments) == ("c1", {"path": "a.txt", "content": "hi"})


def test_a_stream_of_text_then_a_call_with_no_arguments():
    chunks = _stream(
        _opens(0, "text"),
        _delta(0, type="text_delta", text="working"),
        _opens(1, "tool_use", id="c1", name="f"),
        _delta(1, type="input_json_delta", partial_json=""),
        _stops("tool_use"),
    )
    assert chunks[0].text_delta == "working"
    turn = chunks[-1].turn
    assert turn.text == "working" and turn.tool_calls[0].arguments == {}


def test_streamed_thinking_is_joined_with_its_signature_and_kept():
    chunks = _stream(
        _opens(0, "thinking", thinking="", signature=""),
        _delta(0, type="thinking_delta", thinking="step one. "),
        _delta(0, type="thinking_delta", thinking="step two."),
        _delta(0, type="signature_delta", signature="SIGSTREAM"),
        SimpleNamespace(type="content_block_stop", index=0),
        _opens(1, "text"),
        _delta(1, type="text_delta", text="done"),
        _stops("end_turn"),
        thinking_budget=4096,
    )
    assert [c.reasoning_delta for c in chunks if c.reasoning_delta] == ["step one. ", "step two."]
    turn = chunks[-1].turn
    assert (turn.text, turn.reasoning) == ("done", "step one. step two.")
    assert turn.extras["_anthropic"]["blocks"] == [{"type": "thinking", "thinking": "step one. step two.", "signature": "SIGSTREAM"}]


# -- building the provider -----------------------------------------------------------------------


def test_the_registry_builds_the_native_provider_with_or_without_a_key():
    from harness.llm.providers.registry import build_provider_client

    provider = build_provider_client("anthropic", {"api_key": "sk-ant-x"}, None)
    assert isinstance(provider, AnthropicProvider) and provider._api_key == "sk-ant-x"
    # A missing key is found, or not, at the first call.
    assert isinstance(build_provider_client("anthropic", {}, None), AnthropicProvider)


def test_the_key_comes_from_the_environment_then_the_secret_store(monkeypatch):
    from harness.llm.providers.claude.credentials import resolve_api_key

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert resolve_api_key() == "sk-ant-env"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    stored = SimpleNamespace(get=lambda name: {"api_key": "sk-ant-stored"} if name == "provider:anthropic" else None)
    assert resolve_api_key(stored) == "sk-ant-stored"
    assert resolve_api_key(None) is None


def test_the_native_provider_takes_parallel_calls_images_and_streams():
    caps = capabilities_for("anthropic:claude-sonnet-4-6")
    assert caps.tools and caps.vision and caps.streaming and caps.parallel_tool_calls is True


# -- server-side context editing ([context_editing]) --------------------------------


class BadRequestError(Exception):
    """The name matters: `_is_context_editing_rejection` reads `type(exc).__name__`."""


class _Rejecting(_Messages):
    """Keeps every attempt and answers with `refuse(request)` raised, or a plain reply."""

    def __init__(self, refuse):
        super().__init__()
        self.calls: list[dict] = []

        def create(**request):
            self.calls.append(dict(request))
            error = refuse(request)
            if error:
                raise BadRequestError(error)
            return _reply(_block("text", text="ok"))

        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def _edit_types(request):
    return [edit["type"] for edit in (request.get("context_management") or {}).get("edits") or []]


def test_context_editing_is_off_on_a_direct_constructor():
    """Injected clients (Vertex/Bedrock/OpenCode Go) and plain constructions stay opted
    out — only the config-driven native build turns it on."""
    _, sent = _complete(model="claude-sonnet-4-6")
    assert "context_management" not in sent and "betas" not in sent


def test_the_config_table_enables_context_editing_on_the_native_build(monkeypatch):
    import harness.config.config as config_module
    from harness.llm.providers.registry import build_provider_client

    monkeypatch.setattr(config_module, "load_config", lambda *a, **k: config_module.Config())
    provider = build_provider_client("anthropic", {"api_key": "k"}, None)
    assert provider.context_editing is not None
    assert provider.context_editing["trigger_input_tokens"] == 100_000


def test_context_editing_rides_the_beta_endpoint_thinking_first():
    _, sent = _complete(model="claude-sonnet-4-6", context_editing={})
    assert sent["betas"] == ["context-management-2025-06-27"]
    edits = sent["context_management"]["edits"]
    assert edits[0] == {"type": "clear_thinking_20251015", "keep": "all"}
    assert edits[1] == {
        "type": "clear_tool_uses_20250919",
        "trigger": {"type": "input_tokens", "value": 100_000},
        "keep": {"type": "tool_uses", "value": 3},
        # The cache guard: a minimal saving is not worth invalidating the prefix for.
        "clear_at_least": {"type": "input_tokens", "value": 15_000},
        "clear_tool_inputs": False,
    }


def test_zero_clear_at_least_lets_the_api_decide():
    _, sent = _complete(context_editing={"clear_at_least_tokens": 0})
    assert "clear_at_least" not in sent["context_management"]["edits"][-1]


def test_context_editing_knobs_shape_the_edit():
    _, sent = _complete(context_editing={
        "trigger_input_tokens": 60_000,
        "keep_tool_uses": 1,
        "clear_at_least_tokens": 20_000,
        "exclude_tools": ["read_file", "grep"],
        "clear_tool_inputs": True,
        "clear_thinking": False,
    })
    [edit] = sent["context_management"]["edits"]
    assert edit["type"] == "clear_tool_uses_20250919"
    assert (edit["trigger"]["value"], edit["keep"]["value"]) == (60_000, 1)
    assert edit["clear_at_least"] == {"type": "input_tokens", "value": 20_000}
    assert edit["exclude_tools"] == ["read_file", "grep"] and edit["clear_tool_inputs"] is True


def test_context_editing_can_be_turned_off():
    _, sent = _complete(context_editing={"enabled": False})
    assert "context_management" not in sent and "betas" not in sent


def test_context_editing_and_the_refusal_fallback_share_the_beta_endpoint():
    _, sent = _complete(model="claude-fable-5", context_editing={})
    assert sent["betas"] == ["server-side-fallback-2026-06-01", "context-management-2025-06-27"]
    assert sent["fallbacks"] == [{"model": "claude-opus-4-8"}]


def test_a_rejection_latches_context_editing_off_and_retries_once():
    client = _Rejecting(lambda r: "invalid_request_error: context_management is not supported" if "context_management" in r else None)
    provider = AnthropicProvider(client=client, context_editing={})
    assert provider.complete(model="m", messages=ASK).text == "ok"  # the optimisation never costs the turn
    assert len(client.calls) == 2
    assert "context_management" in client.calls[0]
    assert "context_management" not in client.calls[1] and "betas" not in client.calls[1]
    assert provider._context_editing_disabled is True
    # The latch holds: the next call does not pay for the discovery again.
    provider.complete(model="m", messages=ASK)
    assert len(client.calls) == 3
    assert "context_management" not in client.calls[2] and "betas" not in client.calls[2]


def test_a_thinking_only_rejection_keeps_tool_clearing():
    client = _Rejecting(lambda r: "invalid_request_error: clear_thinking is not supported for this model" if "clear_thinking_20251015" in _edit_types(r) else None)
    provider = AnthropicProvider(client=client, context_editing={})
    assert provider.complete(model="claude-3-5-haiku", messages=ASK).text == "ok"
    assert len(client.calls) == 2
    assert _edit_types(client.calls[0]) == ["clear_thinking_20251015", "clear_tool_uses_20250919"]
    assert _edit_types(client.calls[1]) == ["clear_tool_uses_20250919"]
    assert provider._clear_thinking_disabled is True and provider._context_editing_disabled is False
    # Latched: the next request goes straight to the tool-only edit.
    provider.complete(model="claude-3-5-haiku", messages=ASK)
    assert _edit_types(client.calls[2]) == ["clear_tool_uses_20250919"]


def test_a_400_that_is_not_about_context_editing_still_raises():
    provider = AnthropicProvider(client=_Rejecting(lambda r: "invalid_request_error: max_tokens too large"), context_editing={})
    with pytest.raises(BadRequestError, match="max_tokens"):
        provider.complete(model="m", messages=ASK)
    assert provider._context_editing_disabled is False


def test_applied_edits_are_logged(caplog):
    reply = _reply(_block("text", text="ok"))
    reply.context_management = SimpleNamespace(applied_edits=[SimpleNamespace(cleared_tool_uses=4, cleared_input_tokens=52_000)])
    with caplog.at_level(logging.INFO, logger="harness.llm.providers.anthropic"):
        _complete(reply, context_editing={})
    assert "cleared 4 tool uses" in caplog.text and "52000" in caplog.text


def test_normalize_context_editing_degrades_typos():
    from harness.llm.providers.claude.context_editing import CONTEXT_EDITING_DEFAULTS, normalize_context_editing

    assert normalize_context_editing(None) is None
    assert normalize_context_editing(False) is None
    assert normalize_context_editing({"enabled": False}) is None
    spec = normalize_context_editing({"trigger_input_tokens": "nope", "keep_tool_uses": -2, "exclude_tools": "read_file"})
    assert spec["trigger_input_tokens"] == CONTEXT_EDITING_DEFAULTS["trigger_input_tokens"]
    assert spec["keep_tool_uses"] == 0
    assert spec["exclude_tools"] == []


# -- a custom endpoint reaches the SDK -------------------------------------------


def _capture_client(monkeypatch) -> dict:
    """Replace the lazily-imported `Anthropic` constructor with a recorder, so the test can
    assert what the provider would have built without needing the SDK's network surface."""
    import anthropic

    seen: dict = {}

    class _Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", _Recorder)
    return seen


def test_an_explicit_base_url_reaches_the_client(monkeypatch):
    seen = _capture_client(monkeypatch)
    AnthropicProvider(api_key="sk-ant-x", base_url="https://proxy.lan")._ensure_client()
    assert seen["base_url"] == "https://proxy.lan" and seen["api_key"] == "sk-ant-x"


def test_the_profile_endpoint_is_used_when_no_argument_is_given(monkeypatch):
    seen = _capture_client(monkeypatch)
    stored = SimpleNamespace(get=lambda key: {"base_url": "https://from-store.lan"} if key == "provider:anthropic" else None)
    AnthropicProvider(api_key="sk-ant-x", secrets=stored)._ensure_client()
    assert seen["base_url"] == "https://from-store.lan"


def test_no_endpoint_means_no_kwarg_at_all(monkeypatch):
    """Omitting the kwarg (rather than passing None) is what leaves the SDK's own
    ANTHROPIC_BASE_URL handling intact."""
    seen = _capture_client(monkeypatch)
    AnthropicProvider(api_key="sk-ant-x")._ensure_client()
    assert "base_url" not in seen


def test_a_malformed_endpoint_fails_with_a_message_naming_the_field(monkeypatch):
    _capture_client(monkeypatch)
    with pytest.raises(RuntimeError) as err:
        AnthropicProvider(api_key="sk-ant-x", base_url="proxy.lan")._ensure_client()
    assert "http(s)" in str(err.value) and "Configure Models" in str(err.value)


def test_trailing_slashes_are_trimmed_before_reaching_the_sdk(monkeypatch):
    seen = _capture_client(monkeypatch)
    AnthropicProvider(api_key="sk-ant-x", base_url="https://proxy.lan///")._ensure_client()
    assert seen["base_url"] == "https://proxy.lan"


def test_an_injected_client_is_untouched_by_base_url(monkeypatch):
    """Vertex injects its own `AnthropicVertex`; its endpoint is the project's, not a URL."""
    seen = _capture_client(monkeypatch)
    injected = _Messages()
    assert AnthropicProvider(client=injected, base_url="https://proxy.lan")._ensure_client() is injected
    assert seen == {}
