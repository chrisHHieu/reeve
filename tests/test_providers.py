"""The OpenAI-compatible chat provider and the catalog around it: a reply into a turn, the request
retried when a model refuses a parameter, streaming, the vendors reached on the same wire, the
curated model matrix, and the OpenCode Go gateway that routes each model to its own wire.

No SDK call leaves the test: `_Chat` stands in for `client.chat.completions`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from harness.llm.providers import AssistantTurn, ModelCapabilities, OpenAIProvider, ToolCall, capabilities_for
from harness.llm.providers.registry import build_provider_client, get_descriptor
from harness.llm.providers.router import ProviderRouter

READ = [{"type": "function", "function": {"name": "read_file"}}]


def _message(content=None, tool_calls=None, finish="stop", **extra):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls, **extra), finish_reason=finish)])


def _piece(content=None, call=None, finish=None, **extra):
    delta = SimpleNamespace(content=content, tool_calls=[call] if call else None, **extra)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


class _Chat:
    """`chat.completions.create`: `refuse(request)` may return an error to raise; otherwise the
    request streams `pieces` or is answered with `reply`. Every request is kept."""

    def __init__(self, reply=None, pieces=(), refuse=lambda request: None):
        self.requests: list[dict] = []
        self._reply, self._pieces, self._refuse = reply, list(pieces), refuse
        self.chat = SimpleNamespace(completions=self)

    def create(self, **request):
        self.requests.append(request)
        error = self._refuse(request)
        if error:
            raise RuntimeError(error)
        return iter(self._pieces) if request.get("stream") else self._reply


def _openai(reply=None, **chat):
    client = _Chat(reply=reply if reply is not None else _message(content="x"), **chat)
    return OpenAIProvider(client=client), client


def _function(call_id, name, arguments, index=None):
    fields = {"index": index} if index is not None else {}
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments), **fields)


# -- a reply into a turn -------------------------------------------------------------------------


def test_a_text_reply_is_a_turn_with_no_calls():
    provider, _ = _openai(_message(content="hello there"))
    turn = provider.complete(model="gpt-5.5", messages=[{"role": "user", "content": "hi"}])
    assert isinstance(turn, AssistantTurn)
    assert (turn.text, turn.tool_calls, turn.has_tool_calls, turn.finish_reason) == ("hello there", [], False, "stop")


def test_tool_calls_are_read_and_the_tools_were_sent():
    provider, client = _openai(_message(tool_calls=[_function("call_1", "read_file", json.dumps({"path": "a.py"}))], finish="tool_calls"))
    turn = provider.complete(model="gpt-5.5", messages=[], tools=READ)
    assert turn.has_tool_calls and turn.tool_calls[0] == ToolCall(id="call_1", name="read_file", arguments={"path": "a.py"})
    assert "tools" in client.requests[0]


def test_arguments_that_are_not_json_are_kept_raw():
    provider, _ = _openai(_message(tool_calls=[_function("call_2", "x", "{not json")]))
    assert provider.complete(model="gpt-5.5", messages=[]).tool_calls[0].arguments == {"_raw": "{not json"}


def test_no_tools_means_no_tools_key_and_settings_pass_through():
    provider, client = _openai()
    provider.complete(model="gpt-5.5", messages=[])
    provider.complete(model="gpt-5.5", messages=[], temperature=0.2)
    assert "tools" not in client.requests[0] and client.requests[1]["temperature"] == 0.2


def test_a_reasoning_content_field_is_the_turns_reasoning():
    provider, _ = _openai(_message(content="Answer", reasoning_content="deep thought"))
    turn = provider.complete(model="deepseek-v4-pro", messages=[{"role": "user", "content": "x"}])
    assert (turn.text, turn.reasoning) == ("Answer", "deep thought")


def test_another_providers_sidecar_never_reaches_the_wire():
    """OpenAI and its compatible servers refuse fields they do not know."""
    provider, client = _openai(_message(content="ok"))
    provider.complete(model="gpt-5.5", messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "prev", "_gemini": {"call_sigs": ["x"]}}])
    assert client.requests[0]["messages"][1] == {"role": "assistant", "content": "prev"}


def test_a_request_always_names_a_ceiling_and_the_callers_wins():
    """Owner-hit 2026-08-15: left to its own default, Together capped Kimi K3 near 2k tokens, and
    every report write was cut mid-arguments."""
    from harness.llm.providers.chat.request import DEFAULT_MAX_TOKENS

    provider, client = _openai(_message(content="ok"))
    provider.complete(model="kimi-k3", messages=[])
    provider.complete(model="kimi-k3", messages=[], max_tokens=512)
    assert [r["max_tokens"] for r in client.requests] == [DEFAULT_MAX_TOKENS, 512]


def test_capabilities_are_known_by_model_with_or_without_the_prefix():
    assert capabilities_for("gpt-5.5").tools is True
    assert capabilities_for("openai:gpt-5.5").vision is True
    assert capabilities_for("o3-mini").parallel_tool_calls is False
    assert capabilities_for("deepseek-chat").tools is True
    caps = OpenAIProvider(client=_Chat(reply=_message())).capabilities("gpt-5.5")
    assert isinstance(caps, ModelCapabilities) and caps.tools is True


# -- a refused parameter -------------------------------------------------------------------------

# Owner repro 2026-07-14: the API defaults GPT-5.6 to effort "medium" and then refuses function tools
# on chat/completions unless effort is "none".
_EFFORT_REFUSAL = (
    "Error code: 400 - {'error': {'message': \"Function tools with reasoning_effort "
    "are not supported for %s in /v1/chat/completions. To use function tools, use "
    "/v1/responses or set reasoning_effort to 'none'.\", 'type': "
    "'invalid_request_error', 'param': 'reasoning_effort', 'code': None}}"
)


def test_gpt_5_6_with_tools_is_sent_effort_none_unless_the_caller_chose_one():
    provider, client = _openai()
    for model in ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        provider.complete(model=model, messages=[], tools=READ)
    provider.complete(model="gpt-5.6-sol", messages=[], tools=READ, reasoning_effort="low")
    provider.complete(model="gpt-5.6-sol", messages=[])
    provider.complete(model="gpt-5.5", messages=[], tools=READ)
    sent = [r.get("reasoning_effort") for r in client.requests]
    assert sent == ["none", "none", "none", "low", None, None]


def test_a_model_not_yet_known_that_refuses_effort_is_asked_once_more_at_none():
    def refuse(request):
        if request.get("tools") and request.get("reasoning_effort") != "none":
            return _EFFORT_REFUSAL % request["model"]

    provider, client = _openai(_message(content="x"), pieces=[_piece(content="ok"), _piece(finish="stop")], refuse=refuse)
    assert provider.complete(model="gpt-5.7-sol", messages=[], tools=READ).text == "x"
    assert "reasoning_effort" not in client.requests[0] and client.requests[1]["reasoning_effort"] == "none"
    # A stream is retried the same way.
    assert list(provider.stream(model="gpt-5.7-sol", messages=[], tools=READ))[-1].turn.text == "ok"
    assert len(client.requests) == 4


def test_a_refused_max_tokens_is_sent_again_as_max_completion_tokens():
    """Reasoning models want `max_completion_tokens` and compatible servers know only `max_tokens`,
    so the swap waits for the refusal (owner-hit 2026-07-20: titling silently did nothing)."""
    def refuse(request):
        if "max_tokens" in request:
            return "Error code: 400 - Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens' instead."

    provider, client = _openai(_message(content="Jira vs Linear"), refuse=refuse)
    assert provider.complete(model="gpt-5.6-sol", messages=[], max_tokens=64).text == "Jira vs Linear"
    first, second = client.requests
    assert first["max_tokens"] == 64 and "max_tokens" not in second and second["max_completion_tokens"] == 64


def test_a_ceiling_above_the_models_limit_is_dropped_and_the_request_sent_again():
    provider, client = _openai(_message(content="ok"), refuse=lambda r: "Error code: 400 - max_tokens must be at most 8193 for this model" if "max_tokens" in r else None)
    assert provider.complete(model="tiny-model", messages=[]).text == "ok"
    assert len(client.requests) == 2 and "max_tokens" not in client.requests[1]


def test_any_other_400_is_raised_without_a_second_try():
    provider, client = _openai(refuse=lambda r: "Error code: 400 - context_length_exceeded")
    with pytest.raises(RuntimeError):
        provider.complete(model="gpt-5.5", messages=[], tools=READ)
    assert len(client.requests) == 1


# -- streaming -----------------------------------------------------------------------------------


def _streamed(*pieces, model="gpt-5.5"):
    return list(OpenAIProvider(client=_Chat(pieces=pieces)).stream(model=model, messages=[]))


def test_a_stream_yields_its_text_then_the_turn():
    chunks = _streamed(_piece(content="Hel"), _piece(content="lo"), _piece(finish="stop"))
    assert [c.text_delta for c in chunks if c.text_delta] == ["Hel", "lo"]
    assert (chunks[-1].turn.text, chunks[-1].turn.finish_reason) == ("Hello", "stop")


def test_a_streamed_calls_name_and_arguments_are_joined_from_its_pieces():
    turn = _streamed(
        _piece(call=_function("call_1", "read_file", '{"pa', index=0)),
        _piece(call=_function(None, None, 'th": "a.py"}', index=0)),
        _piece(finish="tool_calls"),
    )[-1].turn
    assert turn.tool_calls[0] == ToolCall(id="call_1", name="read_file", arguments={"path": "a.py"})


def test_streamed_reasoning_content_is_reasoning_and_never_the_answer():
    def thinking(text):
        return _piece(reasoning_content=text)

    chunks = _streamed(thinking("hmm "), thinking("okay."), _piece(content="Answer", reasoning_content=None), _piece(finish="stop", reasoning_content=None), model="deepseek-v4-pro")
    assert [c.reasoning_delta for c in chunks if c.reasoning_delta] == ["hmm ", "okay."]
    assert (chunks[-1].turn.text, chunks[-1].turn.reasoning) == ("Answer", "hmm okay.")


# -- vendors on the OpenAI wire ------------------------------------------------------------------

ENDPOINTS = {
    "zai": "https://api.z.ai/api/paas/v4",
    # Zhipu's subscription Coding Plan, apart from pay-as-you-go `zai` (models.dev `zhipuai-coding-plan`).
    "zhipuai-coding-plan": "https://open.bigmodel.cn/api/coding/paas/v4",
    # Its international sibling (models.dev `zai-coding-plan`).
    "zai-coding-plan": "https://api.z.ai/api/coding/paas/v4",
    "deepseek": "https://api.deepseek.com",
    "kimi": "https://api.moonshot.ai/v1",
    "minimax": "https://api.minimax.io/v1",
    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "xai": "https://api.x.ai/v1",
    "mistral": "https://api.mistral.ai/v1",
    # The OpenCode Go gateway's provider-level endpoint on models.dev.
    "opencode-go": "https://opencode.ai/inference/go/openai/v1",
}


def _endpoint_field(descriptor):
    return next(field for field in descriptor.fields if field.key == "base_url")


@pytest.mark.parametrize(("name", "endpoint"), list(ENDPOINTS.items()))
def test_each_compatible_vendor_comes_with_its_endpoint_filled_in_and_editable(name, endpoint):
    descriptor = get_descriptor(name)
    assert descriptor is not None and descriptor.needs_key and descriptor.env_key
    field = _endpoint_field(descriptor)
    assert field.default == endpoint and not field.required  # blank means the default
    assert "OpenAI-compatible" in descriptor.blurb


def test_a_vendor_client_uses_its_endpoint_unless_the_profile_names_another(monkeypatch):
    zai = build_provider_client("zai", {"api_key": "zk"}, None)
    assert (zai._base_url, zai._api_key) == (ENDPOINTS["zai"], "zk")
    moved = "https://open.bigmodel.cn/api/paas/v4"
    assert build_provider_client("zai", {"api_key": "zk", "base_url": moved}, None)._base_url == moved
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    deepseek = build_provider_client("deepseek", {}, None)
    assert (deepseek._api_key, deepseek._base_url) == ("ds-key", ENDPOINTS["deepseek"])
    assert isinstance(build_provider_client("deepseek", {"api_key": "ds-key"}, None), OpenAIProvider)


@pytest.mark.parametrize(("name", "env", "said"), [("kimi", "MOONSHOT_API_KEY", "Kimi"), ("ark", "ARK_API_KEY", "BytePlus Ark")])
def test_the_openai_key_is_never_sent_to_another_vendor(monkeypatch, name, env, said):
    """A missing vendor key fails, by the vendor's name, before anything is sent."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-real")
    monkeypatch.delenv(env, raising=False)
    with pytest.raises(RuntimeError, match=said):
        build_provider_client(name, {}, None)


ARK = {
    "ark": ("https://ark.ap-southeast.bytepluses.com/api/v3", "ARK_API_KEY", False),
    "ark-agent-plan-cn": ("https://ark.cn-beijing.volces.com/api/plan/v3", "ARK_AGENT_PLAN_CN_API_KEY", True),
}


def test_the_two_ark_endpoints_are_separate_responses_api_providers(monkeypatch):
    from harness.llm.providers.matrix import models_for_provider
    from harness.llm.providers.responses import OpenAIResponsesProvider

    for name, (endpoint, env, _) in ARK.items():
        descriptor = get_descriptor(name)
        assert descriptor is not None and descriptor.needs_key and descriptor.env_key == env
        assert "Responses API" in descriptor.blurb
        assert _endpoint_field(descriptor).default == endpoint and not _endpoint_field(descriptor).required
    monkeypatch.setenv("ARK_AGENT_PLAN_CN_API_KEY", "plan-key")
    built = {"ark": build_provider_client("ark", {"api_key": "bp-key"}, None), "ark-agent-plan-cn": build_provider_client("ark-agent-plan-cn", {}, None)}
    for name, provider in built.items():
        endpoint, _, summary = ARK[name]
        assert isinstance(provider, OpenAIResponsesProvider)
        assert provider._base_url == endpoint and provider._reasoning_summary is summary
    assert (built["ark"]._api_key, built["ark-agent-plan-cn"]._api_key) == ("bp-key", "plan-key")
    # Each offers exactly its curated models.
    assert models_for_provider("ark") == ["dola-seed-evolving-latest-version", "dola-seed-2-1-turbo-260628"]
    assert models_for_provider("ark-agent-plan-cn") == ["doubao-seed-evolving", "doubao-seed-2.1-turbo"]


@pytest.mark.parametrize(
    "model",
    ["ark:dola-seed-evolving-latest-version", "ark:dola-seed-2-1-turbo-260628", "ark-agent-plan-cn:doubao-seed-evolving", "ark-agent-plan-cn:doubao-seed-2.1-turbo"],
)
def test_ark_models_route_to_their_provider_and_work_as_agents_without_images(model):
    prefix, bare = model.split(":", 1)
    assert ProviderRouter.__new__(ProviderRouter)._provider_name(model) == prefix and ProviderRouter._bare(model) == bare
    caps = capabilities_for(model)
    assert caps.tools and caps.parallel_tool_calls and caps.streaming and not caps.vision


@pytest.mark.parametrize(
    "model",
    ["zai:glm-5.2", "deepseek:deepseek-v4-flash", "kimi:kimi-k2.6", "minimax:MiniMax-M2.5", "qwen:qwen3-max", "xai:grok-4.3", "mistral:mistral-large-latest"],
)
def test_vendor_models_route_to_their_vendor_and_call_tools(model):
    prefix, bare = model.split(":", 1)
    assert ProviderRouter.__new__(ProviderRouter)._provider_name(model) == prefix and ProviderRouter._bare(model) == bare
    caps = capabilities_for(model)
    assert caps.tools and caps.streaming


# -- the curated matrix --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "together:zai-org/GLM-5.2",
        "together:meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "fireworks:accounts/fireworks/models/kimi-k2p6",
        "openrouter:z-ai/glm-5.2",
        "openrouter:meta-llama/llama-4-maverick",
    ],
)
def test_a_resellers_id_is_answered_by_the_matrix_with_tools_on(model):
    """A reseller's path-shaped id defeats any reading of the name; the matrix answers it exactly."""
    caps = capabilities_for(model)
    assert caps.tools and caps.parallel_tool_calls and caps.streaming


def test_the_matrix_is_small_labelled_and_tool_capable_and_an_unlisted_model_is_cautious():
    from harness.llm.providers.matrix import MATRIX, model_labels

    labels = model_labels()
    assert labels["together:zai-org/GLM-5.2"] == "GLM-5.2 · via Together" and labels["zai:glm-5.2"] == "GLM-5.2 · Z AI"
    # Agent-capable current models only (owner, 2026-07-04). It grew with the Zhipu and Z.AI Coding
    # Plans (2026-09-18) and the Claude subscription's four rows (2026-10-15).
    assert len(MATRIX) < 85 and all(entry.caps.tools for entry in MATRIX.values())
    unlisted = capabilities_for("together:some-org/Brand-New-Model")
    assert unlisted.tools and not unlisted.parallel_tool_calls  # usable, no parallel calls assumed


@pytest.mark.parametrize("name", ["together", "fireworks", "openrouter"])
def test_each_reseller_has_curated_models_for_the_settings_page(name):
    from harness.llm.providers.matrix import models_for_provider

    descriptor = get_descriptor(name)
    assert descriptor is not None and descriptor.needs_key and models_for_provider(name)
    assert _endpoint_field(descriptor).default.startswith("https://")


# -- OpenCode Go ---------------------------------------------------------------------------------


def test_a_session_header_client_routes_by_affinity_with_a_stable_fallback():
    """The OpenCode Go gateway 400s without `x-opencode-session`. A per-request affinity
    (the session id) routes one conversation to one backend; background passes that carry
    no affinity still send a non-empty, stable value so they never get the 400."""
    from harness.llm.providers.base import CACHE_AFFINITY

    client = _Chat(reply=_message(content="ok"))
    provider = OpenAIProvider(client=client, session_header="x-opencode-session")
    provider.complete(model="deepseek-v4.1-flash", messages=[{"role": "user", "content": "hi"}], **{CACHE_AFFINITY: "sess-42"})
    provider.complete(model="deepseek-v4.1-flash", messages=[])
    provider.complete(model="deepseek-v4.1-flash", messages=[])
    sent = [r["extra_headers"]["x-opencode-session"] for r in client.requests]
    assert sent[0] == "sess-42"
    assert sent[1] and sent[1] == sent[2]  # non-empty and stable


def test_a_plain_client_sends_no_extra_headers():
    """Other providers must not start shipping a gateway header they never needed."""
    provider, client = _openai(_message(content="ok"))
    provider.complete(model="gpt-5.5", messages=[])
    assert "extra_headers" not in client.requests[0]


def test_opencode_go_descriptor_builds_a_multi_wire_client():
    from harness.llm.providers.opencode_go import OpenCodeGoProvider

    p = build_provider_client("opencode-go", {"api_key": "ok"}, None)
    assert isinstance(p, OpenCodeGoProvider)
    assert p._base_url == "https://opencode.ai/inference/go/openai/v1"
    # The header the gateway 400s without rides every wire, valued per session.
    for model in ("deepseek-v4.1-flash", "qwen3.8-flash", "grok-4.7", "gemini-4-argon-contributor"):
        assert p._client_for(model)._session_header == "x-opencode-session"


class _GoCatalog:
    """The rows models.dev published for the gateway on 2026-10-21 (wire and endpoint)."""

    ROWS = {
        "opencode-go:minimax-m3": {"wire": "messages", "api": "https://opencode.ai/inference/go/anthropic/v1"},
        "opencode-go:grok-4.7": {"wire": "responses", "api": "https://opencode.ai/inference/go/openai/v1"},
        "opencode-go:gemini-4-argon-contributor": {"wire": "gemini", "api": "https://opencode.ai/inference/go/google/v1beta"},
        "opencode-go:deepseek-v4.1-flash": {"api": "https://opencode.ai/inference/go/openai/v1"},
        # Invented: the next time the gateway moves a model, the catalogue says where.
        "opencode-go:kimi-k3": {"api": "https://opencode.ai/inference/go/moonshot/v1"},
    }

    def get(self, model):
        return self.ROWS.get(model)


def test_opencode_go_routes_each_model_to_its_own_wire_and_endpoint(monkeypatch):
    """The gateway names a transport per model in `provider.npm` and an endpoint in
    `provider.api`; so does this client, from the catalogue's projected `wire` and `api`. A base
    saved before the gateway moved (`/zen/go/v1`, 2026-10-20) is the gateway's own host, so it
    follows the catalogue too instead of a path the website no longer forwards."""
    from harness.llm.providers import catalog as cat
    from harness.llm.providers.claude import AnthropicProvider
    from harness.llm.providers.gemini import GeminiProvider
    from harness.llm.providers.opencode_go import OpenCodeGoProvider
    from harness.llm.providers.responses import OpenAIResponsesProvider

    monkeypatch.setattr(cat, "models_dev", lambda: _GoCatalog())
    for saved in ("https://opencode.ai/zen/go/v1", "https://opencode.ai/inference/go/openai/v1"):
        p = OpenCodeGoProvider(api_key="k", base_url=saved)
        messages, responses = p._client_for("minimax-m3"), p._client_for("grok-4.7")
        gemini, chat = p._client_for("gemini-4-argon-contributor"), p._client_for("deepseek-v4.1-flash")
        assert isinstance(messages, AnthropicProvider)
        assert isinstance(responses, OpenAIResponsesProvider)
        assert isinstance(gemini, GeminiProvider)
        assert isinstance(chat, OpenAIProvider)
        # Anthropic's SDK appends /v1/messages, so its root drops the endpoint's /v1.
        assert messages._base_url == "https://opencode.ai/inference/go/anthropic"
        assert responses._base_url == "https://opencode.ai/inference/go/openai/v1"
        assert gemini._base_url == "https://opencode.ai/inference/go/google/v1beta"
        assert chat._base_url == "https://opencode.ai/inference/go/openai/v1"
        # A model the catalogue has no endpoint for gets its wire's documented root; one
        # transport per endpoint, so a conversation's models share a client.
        assert p._client_for("glm-5.3") is chat
        assert p._client_for("kimi-k3")._base_url == "https://opencode.ai/inference/go/moonshot/v1"


def test_a_proxy_in_front_of_opencode_go_keeps_its_own_base(monkeypatch):
    """A base on another host is the person's own proxy: the catalogue's opencode.ai endpoints
    would go around it, so every wire uses the base as given, in the original single-root shape."""
    from harness.llm.providers import catalog as cat
    from harness.llm.providers.opencode_go import OpenCodeGoProvider, _gemini_root, _messages_root

    monkeypatch.setattr(cat, "models_dev", lambda: _GoCatalog())
    p = OpenCodeGoProvider(api_key="k", base_url="https://proxy.example/go/v1/")
    assert p._client_for("deepseek-v4.1-flash")._base_url == "https://proxy.example/go/v1"
    assert p._client_for("grok-4.7")._base_url == "https://proxy.example/go/v1"
    assert p._client_for("minimax-m3")._base_url == "https://proxy.example/go"
    assert p._client_for("gemini-4-argon-contributor")._base_url == "https://proxy.example/go/v1"
    assert _messages_root("https://opencode.ai/zen/go/v1") == "https://opencode.ai/zen/go"
    assert _gemini_root("https://opencode.ai/zen/go/v1") == "https://opencode.ai/inference/go/v1"


class _Sent(Exception):
    """Raised by a fake SDK once it has the request: what was sent is all a test reads."""


class _Capture:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise _Sent()

    def generate_content(self, **kwargs):
        return self.create(**kwargs)


def test_every_opencode_go_wire_sends_the_conversation_as_its_routing_session():
    """The gateway routes (and caches) by `x-opencode-session`, one per conversation. Until
    2026-10-21 only the chat wire sent the session id; the Messages, Responses and Gemini wires
    sent one fixed value for every conversation, so the gateway saw them as one. Each wire now
    sends the call's session, and a stable stand-in when a call has none."""
    from harness.llm.providers.base import CACHE_AFFINITY
    from harness.llm.providers.claude import AnthropicProvider
    from harness.llm.providers.gemini import GeminiProvider
    from harness.llm.providers.responses import OpenAIResponsesProvider

    def sent_sessions(provider, capture, read, model):
        for affinity in ("sess-1", "sess-2", None, None):
            settings = {CACHE_AFFINITY: affinity} if affinity else {}
            with pytest.raises(_Sent):
                provider.complete(model=model, messages=[{"role": "user", "content": "hi"}], **settings)
        return [read(call) for call in capture.calls]

    header = "x-opencode-session"
    anthropic = _Capture()
    claude = AnthropicProvider(client=SimpleNamespace(messages=anthropic, beta=SimpleNamespace(messages=anthropic)), session_header=header)
    responses = _Capture()
    openai_responses = OpenAIResponsesProvider(client=SimpleNamespace(responses=responses), session_header=header)
    gemini = _Capture()
    google = GeminiProvider(client=SimpleNamespace(models=gemini), session_header=header)

    for provider, capture, read, model in (
        (claude, anthropic, lambda call: call["extra_headers"][header], "minimax-m3"),
        (openai_responses, responses, lambda call: call["extra_headers"][header], "grok-4.7"),
        (google, gemini, lambda call: call["config"]["http_options"]["headers"][header], "gemini-4-argon-contributor"),
    ):
        first, second, standin, again = sent_sessions(provider, capture, read, model)
        assert (first, second) == ("sess-1", "sess-2"), provider
        assert standin and standin == again, provider

    # Built without one (every other route), no wire starts sending a gateway header.
    plain = _Capture()
    with pytest.raises(_Sent):
        AnthropicProvider(client=SimpleNamespace(messages=plain, beta=SimpleNamespace(messages=plain))).complete(
            model="claude-x", messages=[{"role": "user", "content": "hi"}], **{CACHE_AFFINITY: "s"}
        )
    assert "extra_headers" not in plain.calls[0]


def test_sdk_retries_are_disabled_so_the_engine_owns_visible_attempts(monkeypatch):
    """One visible attempt per stream: hidden SDK retries
    multiplied the engine's budget and their failures never reached the log."""
    import anthropic
    import openai

    from harness.llm.providers.timeouts import STREAM_IDLE_SECONDS

    captured: list[dict] = []

    class _Recorder:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(openai, "OpenAI", _Recorder)
    monkeypatch.setattr(anthropic, "Anthropic", _Recorder)
    for name in ("deepseek", "anthropic"):
        build_provider_client(name, {"api_key": "k"}, None)._ensure_client()  # forces the lazy SDK construction
    assert captured, "both SDK clients were built"
    assert all(entry.get("max_retries") == 0 for entry in captured), captured
    assert all(entry["timeout"].read == STREAM_IDLE_SECONDS for entry in captured)
