"""The native Gemini provider: history into `contents`, tool schemas Gemini accepts, a reply into a
turn, streaming, thought signatures kept for replay, and the endpoint the SDK client is built on.

No SDK call leaves the test: `_Models` stands in for `client.models.generate_content[_stream]`,
built from `SimpleNamespace` like the SDK's objects.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from harness.llm.providers import GeminiProvider, capabilities_for
from harness.llm.providers.gemini.convert import convert_messages
from harness.llm.providers.gemini.schema import convert_tools, sanitize_schema

ASK = [{"role": "user", "content": "x"}]


class _Models:
    """`models.generate_content` answers with `reply`, `generate_content_stream` with `chunks`;
    the last request is kept."""

    def __init__(self, reply=None, chunks=()):
        self.request: dict = {}

        def answer(**request):
            self.request = request
            return reply

        def stream(**request):
            self.request = request
            return iter(chunks)

        self.models = SimpleNamespace(generate_content=answer, generate_content_stream=stream)


def _reply(*parts, finish="STOP"):
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=list(parts)), finish_reason=finish)])


def _text(text, sig=None):
    return SimpleNamespace(text=text, function_call=None, thought_signature=sig)


def _call(name, args, sig=None):
    return SimpleNamespace(text=None, function_call=SimpleNamespace(name=name, args=args), thought_signature=sig)


def _thought(text, sig=None):
    return SimpleNamespace(text=text, function_call=None, thought=True, thought_signature=sig)


def _asked_for(*calls, content="", **extra):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}} for cid, name in calls],
        **extra,
    }


def _complete(reply, model="m", messages=ASK, **settings):
    client = _Models(reply=reply)
    turn = GeminiProvider(client=client).complete(model=model, messages=messages, **settings)
    return turn, client.request


def _stream(*chunks, model="m"):
    return list(GeminiProvider(client=_Models(chunks=chunks)).stream(model=model, messages=ASK))


def _sealed(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# -- history into contents -----------------------------------------------------------------------


def test_the_leading_system_message_is_lifted_out_and_text_becomes_a_part():
    system, contents = convert_messages([{"role": "system", "content": "be helpful"}, {"role": "user", "content": "hi"}])
    assert system == "be helpful" and contents == [{"role": "user", "parts": [{"text": "hi"}]}]


def test_the_assistant_is_the_model_role_and_a_tool_only_turn_sends_no_empty_text():
    _, contents = convert_messages([{"role": "user", "content": "do it"}, {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "call_0", "type": "function", "function": {"name": "f", "arguments": '{"x": 1}'}}],
    }])
    assert contents[1] == {**contents[1], "role": "model", "parts": [{"function_call": {"name": "f", "args": {"x": 1}}}]}


def test_results_are_named_by_their_function_and_parallel_ones_share_a_turn():
    """Gemini's calls carry no ids, so a result is matched back to its function's name."""
    _, contents = convert_messages([
        {"role": "user", "content": "go"},
        _asked_for(("call_0", "a"), ("call_1", "b")),
        {"role": "tool", "tool_call_id": "call_0", "content": '{"ok": true}'},
        {"role": "tool", "tool_call_id": "call_1", "content": "plain text"},
    ])
    assert [c["role"] for c in contents] == ["user", "model", "user"]
    assert [p["function_response"] for p in contents[2]["parts"]] == [
        {"name": "a", "response": {"ok": True}},  # a JSON result goes as it is
        {"name": "b", "response": {"result": "plain text"}},  # text is wrapped
    ]


def test_an_id_reused_by_a_later_turn_names_that_turns_function():
    """Synthesized ids restart at call_0 every turn; a result follows its own turn."""
    _, contents = convert_messages([
        {"role": "user", "content": "go"},
        _asked_for(("call_0", "first")),
        {"role": "tool", "tool_call_id": "call_0", "content": "r1"},
        _asked_for(("call_0", "second")),
        {"role": "tool", "tool_call_id": "call_0", "content": "r2"},
    ])
    named = [p["function_response"]["name"] for c in contents for p in c["parts"] if "function_response" in p]
    assert named == ["first", "second"]


def test_convert_steering_user_message_lands_after_the_tool_results():
    """This used to assert the opposite — that the steering text merged INTO the
    function-response turn. It came in with the initial import and carried no reason, and
    the live API contradicts it: a turn mixing `function_response` with text is refused
    (2026-09-10, measured with a real thought signature), while the same two as separate
    user turns are accepted. What the fold was actually for is kept — parallel results
    still ride together in one turn.
    """
    _, contents = convert_messages([
        {"role": "user", "content": "go"},
        _asked_for(("c1", "a")),
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "user", "content": "actually, stop"},
    ])
    assert "function_response" in contents[2]["parts"][0]
    assert len(contents[2]["parts"]) == 1, "nothing else may ride with a function response"
    assert contents[3] == {"role": "user", "parts": [{"text": "actually, stop"}]}


def test_an_inline_image_goes_as_inline_data_and_a_linked_one_as_a_note():
    _, contents = convert_messages([{"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
    ]}])
    assert contents[0]["parts"][1:] == [
        {"inline_data": {"mime_type": "image/png", "data": "iVBORw0KGgo="}},
        {"text": "[unsupported image attachment]"},
    ]


def test_a_pdf_goes_as_inline_data_and_an_unreadable_file_as_a_note():
    from harness.llm.providers.gemini.convert import _user_parts

    parts = _user_parts([
        {"type": "text", "text": "summarize"},
        {"type": "file", "file": {"filename": "report.pdf", "file_data": "data:application/pdf;base64,JVBERi0="}},
        {"type": "file", "file": {"file_data": "not-a-url"}},
    ])
    assert parts[1:] == [{"inline_data": {"mime_type": "application/pdf", "data": "JVBERi0="}}, {"text": "[unsupported file attachment]"}]


def test_the_history_opens_with_the_user_and_an_empty_one_is_refused():
    _, opened = convert_messages([{"role": "assistant", "content": "an answer with nothing asked before it"}])
    assert opened[0] == {"role": "user", "parts": [{"text": "(continued)"}]}  # Gemini needs a user turn first
    with pytest.raises(ValueError):
        convert_messages([{"role": "assistant", "content": ""}])  # nothing at all to send


def test_a_request_never_ends_on_a_model_turn():
    """Gemini answers `400 INVALID_ARGUMENT: Requests ending with a model turn are not
    supported` — a hard failure that kills the turn, not a degradation (owner-hit
    2026-09-08). Every other provider takes a trailing assistant message, so this vendor
    rule is enforced here rather than remembered by each caller upstream."""
    _, contents = convert_messages([{"role": "user", "content": "hi"}, {"role": "assistant", "content": "there"}])
    assert contents[-1]["role"] == "user"
    # the real answer is kept — the guard adds a turn, it does not replace one
    assert contents[-2] == {"role": "model", "parts": [{"text": "there"}]}


def test_a_dangling_tool_call_still_produces_a_valid_request():
    """The shape most likely to reach it: an assistant turn whose tool results never
    came back (interrupted, resumed, or cut by a compaction boundary)."""
    _, contents = convert_messages([{"role": "user", "content": "look it up"}, _asked_for(("call_0", "search"))])
    assert contents[-1]["role"] == "user"
    assert "function_call" in contents[-2]["parts"][0]


def test_a_message_appended_after_tool_results_gets_its_own_turn():
    """The engine's todo nudge (`TODO_NUDGE_TEXT`) is a plain user message appended right
    after a step's tool results. Tool results ride as `user` turns, so folding by role
    alone put its text INSIDE the function-response turn — and Vertex rejects a turn that
    mixes the two, then reports the model turn before it as the last one:
    `400 INVALID_ARGUMENT: Requests ending with a model turn are not supported`.

    Measured on the live API (2026-09-10), replaying a real function call with its real
    thought signature: the mixed turn is that 400, the split one is accepted. Every 400 of
    this shape in the owner's session store followed a nudge, and every one of them was a
    Vertex Gemini model.
    """
    _, contents = convert_messages([
        {"role": "user", "content": "stats please"},
        _asked_for(("call_0", "a"), ("call_1", "b"), content="on it"),
        {"role": "tool", "tool_call_id": "call_0", "content": "one"},
        {"role": "tool", "tool_call_id": "call_1", "content": "two"},
        {"role": "user", "content": "System note: this turn is running long."},
    ])
    responses, note = contents[-2], contents[-1]
    assert responses["role"] == "user" and note["role"] == "user"
    # The results still travel together — the split is between kinds, not one turn per call.
    assert [set(p) for p in responses["parts"]] == [{"function_response"}] * 2
    assert note["parts"] == [{"text": "System note: this turn is running long."}]


def test_an_empty_turn_is_not_a_response_turn():
    """`all([])` is True, so the part-less case has to be answered explicitly. It cannot
    arrive through `convert_messages` today — an empty message is dropped before the fold,
    which is why this is pinned on the helper rather than through a conversion — but the
    rule it encodes is what keeps text out of a response turn."""
    from harness.llm.providers.gemini.convert import carries_responses

    assert carries_responses({"parts": []}) is False
    assert carries_responses({"parts": [{"function_response": {}}]}) is True
    assert carries_responses({"parts": [{"function_response": {}}, {"text": "x"}]}) is False


def test_ordinary_same_role_text_still_folds():
    """The split is only around function responses. Two plain user messages in a row —
    a resumed turn, an attachment stamped beside a question — stay one turn, which is
    what keeps the common history shape unchanged."""
    _, contents = convert_messages([{"role": "user", "content": "first"}, {"role": "user", "content": "second"}])
    assert contents == [{"role": "user", "parts": [{"text": "first"}, {"text": "second"}]}]


def test_the_guard_leaves_an_ordinary_request_alone():
    """It must not append anything to the overwhelmingly common shape — a history that
    already ends with the user, or with a tool result (which rides as a user turn)."""
    _, contents = convert_messages([
        {"role": "user", "content": "hi"}, {"role": "assistant", "content": "there"}, {"role": "user", "content": "and then?"},
    ])
    assert contents[-1] == {"role": "user", "parts": [{"text": "and then?"}]} and len(contents) == 3

    _, contents = convert_messages([
        {"role": "user", "content": "look it up"},
        _asked_for(("call_0", "search")),
        {"role": "tool", "tool_call_id": "call_0", "content": "{}"},
    ])
    assert contents[-1]["role"] == "user" and "function_response" in contents[-1]["parts"][0]
    assert len(contents) == 3, "a tool result already IS the user turn Gemini wants"


def test_kept_signatures_go_back_on_the_parts_they_came_with():
    _, contents = convert_messages([
        {"role": "user", "content": "do it"},
        _asked_for(("call_0", "a"), ("call_1", "b"), content="on it", _gemini={"text_sig": "dHNpZw==", "call_sigs": [None, "Y3NpZw=="]}),
    ])
    # Found by role: a history ending on the model gains a trailing user turn.
    text, first, second = next(c for c in contents if c["role"] == "model")["parts"]
    assert text == {"text": "on it", "thought_signature": "dHNpZw=="}
    assert "thought_signature" not in first
    assert second["function_call"]["name"] == "b" and second["thought_signature"] == "Y3NpZw=="


def test_a_part_carrying_a_signature_validates_as_the_sdks_part():
    """The SDK's base64 bytes-validation is what decodes the string on send."""
    types = pytest.importorskip("google.genai.types")
    _, contents = convert_messages([{"role": "user", "content": "go"}, _asked_for(("call_0", "a"), _gemini={"text_sig": None, "call_sigs": ["c2ln"]})])
    part = types.Part.model_validate(next(c for c in contents if c["role"] == "model")["parts"][0])
    assert part.thought_signature == b"sig"


# -- tool schemas --------------------------------------------------------------------------------


def test_tools_are_one_list_of_function_declarations():
    [tool] = convert_tools([
        {"type": "function", "function": {"name": "bare"}},
        {"type": "function", "function": {"name": "full", "description": "does things", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}}}},
    ])
    bare, full = tool["function_declarations"]
    assert bare == {"name": "bare"}  # no parameters, no `parameters` key
    assert full["description"] == "does things" and full["parameters"]["properties"] == {"x": {"type": "integer"}}
    assert convert_tools(None) == []


def test_schema_keys_gemini_refuses_are_removed_at_every_depth():
    cleaned = sanitize_schema({
        "type": "object",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": {"type": "object", "additionalProperties": True, "properties": {"x": {"type": "string", "examples": ["a"]}}}}},
        "required": ["items"],
    })
    assert "$schema" not in cleaned and "additionalProperties" not in cleaned
    inner = cleaned["properties"]["items"]["items"]
    assert "additionalProperties" not in inner and "examples" not in inner["properties"]["x"]
    assert cleaned["required"] == ["items"]


def test_a_union_type_becomes_nullable_or_any_of():
    """Owner-hit 2026-07-23: an MCP server's `type: ['string', 'number']` refused every Gemini turn."""
    cleaned = sanitize_schema({"type": "object", "properties": {
        "compareValue": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": ["string", "number"]}}]},
        "maybe": {"type": ["string", "null"]},
        "nothing": {"type": ["null"]},
    }})
    props = cleaned["properties"]
    assert props["compareValue"]["anyOf"][1]["items"] == {"anyOf": [{"type": "string"}, {"type": "number"}]}
    assert props["maybe"] == {"type": "string", "nullable": True}
    assert props["nothing"] == {"nullable": True}


def test_the_union_shape_that_failed_passes_the_sdks_config_validation():
    types = pytest.importorskip("google.genai.types")
    value = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "array", "items": {"type": "string"}}, {"type": "array", "items": {"type": ["string", "number"]}}]}
    tools = convert_tools([{"type": "function", "function": {"name": "mcp__monday__search", "parameters": {
        "type": "object",
        "properties": {"filters": {"type": "array", "items": {"type": "object", "properties": {"compareValue": value}}}},
    }}}])
    assert types.GenerateContentConfig.model_validate({"tools": tools}).tools


def test_sanitize_coerces_non_string_enums():
    """`Schema.enum` is `list[str]` in the SDK, whatever the parameter's type is.

    Owner-hit 2026-09-07: TCBS's `tcinvest-getHistoryRecommendation` declares
    `fRecommend` as `{"type": "integer", "enum": [0, 1, 2, 3, 4]}` — valid JSON Schema,
    accepted by every other provider — and google-genai's pydantic validation rejected it
    before any HTTP call, killing every Gemini turn with TCBS connected.
    """
    cleaned = sanitize_schema({"type": "object", "properties": {
        "fRecommend": {"type": "integer", "enum": [0, 1, 2, 3, 4], "description": "Recommendation type: 0=All, 1=Buy."},
        "loose": {"enum": [1, "two"]},
        "flag": {"type": "boolean", "enum": [True, False]},
        "fTime": {"type": "string", "enum": ["1M", "A"]},
    }})
    number = cleaned["properties"]["fRecommend"]
    # the type it declared is the type the tool gets back; only the enum key goes
    assert number["type"] == "integer" and "enum" not in number
    assert number["description"] == "Recommendation type: 0=All, 1=Buy. Allowed values: 0, 1, 2, 3, 4."
    # no declared type means the value goes out as a string anyway — stringify, don't drop
    assert cleaned["properties"]["loose"]["enum"] == ["1", "two"]
    flag = cleaned["properties"]["flag"]
    assert "enum" not in flag and flag["description"] == "Allowed values: true, false."
    # a string enum is untouched
    assert cleaned["properties"]["fTime"]["enum"] == ["1M", "A"]


def test_integer_enum_schema_validates_as_sdk_config():
    """The exact TCBS shape must pass the SDK's GenerateContentConfig validation."""
    types = pytest.importorskip("google.genai.types")
    tools = convert_tools([{"type": "function", "function": {
        "name": "mcp__tcbs__tcinvest-getHistoryRecommendation",
        "parameters": {
            "type": "object",
            "properties": {"fRecommend": {"type": "integer", "enum": [0, 1, 2, 3, 4], "default": 0, "description": "Recommendation type: 0=All, 1=Buy"}},
            "required": ["fRecommend"],
        },
    }}])
    assert types.GenerateContentConfig.model_validate({"tools": tools}).tools


# -- the request ---------------------------------------------------------------------------------


def test_a_plain_request_carries_the_system_instruction_and_no_tools():
    turn, sent = _complete(_reply(_text("hello")), model="gemini-2.5-flash", messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
    assert (turn.text, turn.finish_reason, turn.has_tool_calls) == ("hello", "stop", False)
    assert sent["model"] == "gemini-2.5-flash" and sent["config"]["system_instruction"] == "sys"
    assert "tools" not in sent["config"]


def test_settings_are_renamed_or_dropped_to_what_gemini_takes():
    _, sent = _complete(_reply(_text("x")), temperature=0.2, max_tokens=512, stop="END", frequency_penalty=0.5)
    config = sent["config"]
    assert (config["temperature"], config["max_output_tokens"], config["stop_sequences"]) == (0.2, 512, ["END"])
    assert not {"frequency_penalty", "stop", "max_tokens"} & set(config)


def test_tools_ride_in_the_config():
    _, sent = _complete(_reply(_text("x")), tools=[{"type": "function", "function": {"name": "f", "parameters": {"type": "object", "properties": {"a": {"type": "string"}}}}}])
    assert sent["config"]["tools"][0]["function_declarations"][0]["name"] == "f"


def test_without_a_key_the_client_is_refused_by_name(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Gemini"):
        GeminiProvider()._ensure_client()


def test_thought_summaries_are_asked_for_and_read_as_reasoning():
    turn, sent = _complete(_reply(_thought("plotting a plan"), _text("the answer")), model="gemini-3.6-flash")
    assert sent["config"]["thinking_config"] == {"include_thoughts": True}
    assert (turn.text, turn.reasoning) == ("the answer", "plotting a plan")


def test_reasoning_effort_none_turns_thinking_off():
    # Utility calls (session titling, memory extraction) ask for no thinking. Thought
    # tokens bill as output here, so a dropped kwarg means they silently eat a tight
    # max_output_tokens — a session title once came back as one truncated word.
    turn, sent = _complete(_reply(_text("ok")), model="gemini-3.6-flash", reasoning_effort="none", max_tokens=64)
    assert sent["config"]["thinking_config"] == {"include_thoughts": False, "thinking_budget": 0}
    # the OpenAI-shaped kwarg itself never leaks onto the wire
    assert "reasoning_effort" not in sent["config"]
    assert sent["config"]["max_output_tokens"] == 64
    assert turn.text == "ok"


# -- a reply into a turn -------------------------------------------------------------------------


def test_function_calls_get_ids_unique_across_the_session():
    turn, _ = _complete(_reply(_text("on it"), _call("write_file", {"path": "a.txt"}), _call("read_file", {"path": "b"})))
    assert (turn.text, turn.finish_reason) == ("on it", "tool_calls")  # STOP with calls is a tool turn
    assert [c.name for c in turn.tool_calls] == ["write_file", "read_file"]
    # Not the position in this reply (tests/test_call_id_reuse.py).
    ids = [c.id for c in turn.tool_calls]
    assert all(i.startswith("call_") for i in ids) and len(set(ids)) == 2
    assert turn.tool_calls[0].arguments == {"path": "a.txt"}


@pytest.mark.parametrize(("finish", "reason"), [("STOP", "stop"), ("MAX_TOKENS", "length"), ("SAFETY", "stop"), ("WEIRD_NEW", "weird_new")])
def test_finish_reasons_map_to_the_engines(finish, reason):
    assert _complete(_reply(_text("x"), finish=finish))[0].finish_reason == reason


def test_signatures_are_kept_for_replay_and_thought_text_never_reaches_the_answer():
    turn, _ = _complete(_reply(_thought("secret reasoning", sig=b"tsig"), _text("the answer"), _call("run_shell", {"command": "ls"}, sig=b"csig")))
    assert turn.text == "the answer" and turn.tool_calls[0].name == "run_shell"
    assert turn.extras["_gemini"] == {**turn.extras["_gemini"], "text_sig": _sealed(b"tsig"), "call_sigs": [_sealed(b"csig")]}
    assert _complete(_reply(_text("plain")))[0].extras == {}


# -- streaming -----------------------------------------------------------------------------------


def test_a_stream_yields_its_text_then_the_turn():
    chunks = _stream(_reply(_text("hel"), finish=None), _reply(_text("lo")))
    assert [c.text_delta for c in chunks[:-1]] == ["hel", "lo"]
    turn = chunks[-1].turn
    assert (turn.text, turn.finish_reason, turn.has_tool_calls) == ("hello", "stop", False)


def test_calls_streamed_in_separate_chunks_are_one_turns_calls():
    turn = _stream(_reply(_text("working"), finish=None), _reply(_call("f", {"x": 1}), finish=None), _reply(_call("g", {})))[-1].turn
    assert turn.finish_reason == "tool_calls" and [c.name for c in turn.tool_calls] == ["f", "g"]
    assert len({c.id for c in turn.tool_calls}) == 2 and turn.tool_calls[0].arguments == {"x": 1}


def test_the_sdks_enum_finish_reason_reads_like_its_name():
    assert _stream(_reply(_text("x"), finish=SimpleNamespace(name="MAX_TOKENS")))[-1].turn.finish_reason == "length"


def test_streamed_signatures_line_up_with_their_calls():
    turn = _stream(_reply(_text("hi "), finish=None), _reply(_call("t1", {}, sig=b"s1"), finish=None), _reply(_call("t2", {})))[-1].turn
    assert [c.name for c in turn.tool_calls] == ["t1", "t2"]
    assert turn.extras["_gemini"]["call_sigs"] == [_sealed(b"s1"), None]


def test_streamed_thoughts_arrive_as_reasoning():
    chunks = _stream(_reply(_thought("mull "), finish=None), _reply(_thought("it over"), finish=None), _reply(_text("done")), model="gemini-3.6-flash")
    assert [c.reasoning_delta for c in chunks if c.reasoning_delta] == ["mull ", "it over"]
    assert (chunks[-1].turn.text, chunks[-1].turn.reasoning) == ("done", "mull it over")


# -- building the provider -----------------------------------------------------------------------


def test_the_registry_builds_the_native_provider_with_or_without_a_key():
    from harness.llm.providers.registry import build_provider_client

    provider = build_provider_client("gemini", {"api_key": "AIza-x"}, None)
    assert isinstance(provider, GeminiProvider) and provider._api_key == "AIza-x"
    assert isinstance(build_provider_client("gemini", {}, None), GeminiProvider)


def test_the_key_comes_from_either_environment_name_then_the_secret_store(monkeypatch):
    from harness.llm.providers.gemini import resolve_api_key

    monkeypatch.setenv("GEMINI_API_KEY", "AIza-env")
    assert resolve_api_key() == "AIza-env"
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-google")  # the SDK's own name for it
    assert resolve_api_key() == "AIza-google"
    monkeypatch.delenv("GOOGLE_API_KEY")
    stored = SimpleNamespace(get=lambda name: {"api_key": "AIza-stored"} if name == "provider:gemini" else None)
    assert resolve_api_key(stored) == "AIza-stored"
    assert resolve_api_key(None) is None


def test_the_native_provider_takes_parallel_calls_images_and_streams():
    caps = capabilities_for("gemini:gemini-2.5-flash")
    assert caps.tools and caps.vision and caps.streaming and caps.parallel_tool_calls is True


# -- a custom endpoint reaches the SDK -------------------------------------------


def _capture_genai(monkeypatch) -> dict:
    """Replace `genai.Client` with a recorder — the provider imports it lazily, so patching
    the module attribute is enough and no network surface is needed."""
    from google import genai

    seen: dict = {}

    class _Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(genai, "Client", _Recorder)
    return seen


def test_an_explicit_base_url_reaches_the_client(monkeypatch):
    seen = _capture_genai(monkeypatch)
    GeminiProvider(api_key="AIza-x", base_url="https://gw.lan")._ensure_client()
    assert seen["http_options"].base_url == "https://gw.lan" and seen["api_key"] == "AIza-x"


def test_no_endpoint_means_no_http_options_at_all(monkeypatch):
    """An empty HttpOptions would replace whatever the SDK resolved for itself."""
    seen = _capture_genai(monkeypatch)
    GeminiProvider(api_key="AIza-x")._ensure_client()
    assert "http_options" not in seen


def test_the_profile_endpoint_is_used_when_no_argument_is_given(monkeypatch):
    seen = _capture_genai(monkeypatch)
    stored = SimpleNamespace(get=lambda key: {"base_url": "https://from-store.lan"} if key == "provider:gemini" else None)
    GeminiProvider(api_key="AIza-x", secrets=stored)._ensure_client()
    assert seen["http_options"].base_url == "https://from-store.lan"


def test_an_injected_client_is_untouched_by_base_url(monkeypatch):
    """Vertex injects its own client over a project-scoped endpoint."""
    seen = _capture_genai(monkeypatch)
    injected = SimpleNamespace()
    assert GeminiProvider(client=injected, base_url="https://gw.lan")._ensure_client() is injected
    assert seen == {}


def test_a_malformed_endpoint_fails_with_a_message_naming_the_field(monkeypatch):
    _capture_genai(monkeypatch)
    with pytest.raises(RuntimeError) as err:
        GeminiProvider(api_key="AIza-x", base_url="gw.lan")._ensure_client()
    assert "http(s)" in str(err.value) and "Configure Models" in str(err.value)
