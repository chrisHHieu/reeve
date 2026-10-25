"""AWS Bedrock: which client serves a model id, the Converse mapping (boto3 answers plain dicts),
how credentials are chosen, and how the settings form and its check see the provider.

Nothing reaches AWS: boto3's session and clients are replaced with recorders.
"""

from __future__ import annotations

import os
from typing import Optional

import pytest

from harness.llm.providers import capabilities_for
from harness.llm.providers.base import AssistantTurn, ProviderClient, StreamChunk
from harness.llm.providers.bedrock import BedrockProvider
from harness.llm.providers.bedrock.converse import ConverseClient
from harness.llm.providers.bedrock.convert import convert_messages, convert_tools
from harness.llm.providers.bedrock.credentials import session_kwargs
from harness.llm.providers.registry import build_provider_client, descriptor_configured, get_descriptor, verify_provider_key

ASK = [{"role": "user", "content": "x"}]


@pytest.fixture(autouse=True)
def _no_bearer_in_the_environment(monkeypatch):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)


# -- history into Converse -----------------------------------------------------------------------


def test_the_system_prompt_is_separate_and_parallel_results_share_one_user_message():
    system, messages = convert_messages([
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "checking", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "ls", "arguments": '{"path": "."}'}},
            {"id": "t2", "type": "function", "function": {"name": "pwd", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": "a.txt"},
        {"role": "tool", "tool_call_id": "t2", "content": "/repo"},
    ])
    assert system == [{"text": "be terse"}]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    said, first_call = messages[1]["content"][:2]
    assert said == {"text": "checking"}
    assert first_call["toolUse"] == {"toolUseId": "t1", "name": "ls", "input": {"path": "."}}
    results = [block["toolResult"] for block in messages[2]["content"]]
    assert [r["toolUseId"] for r in results] == ["t1", "t2"] and results[0]["content"] == [{"text": "a.txt"}]


def test_a_history_that_starts_with_the_assistant_gets_a_user_turn_first():
    _, messages = convert_messages([{"role": "assistant", "content": "hello"}])
    assert messages[0] == {"role": "user", "content": [{"text": "(continued)"}]}


def test_tools_become_tool_specs_and_a_tool_without_parameters_gets_an_empty_object():
    config = convert_tools([
        {"type": "function", "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "noargs"}},
    ])
    spec, bare = (tool["toolSpec"] for tool in config["tools"])
    assert (spec["name"], spec["description"]) == ("read_file", "Read a file")
    assert spec["inputSchema"]["json"]["properties"]["path"] == {"type": "string"}
    assert bare["inputSchema"]["json"] == {"type": "object", "properties": {}}  # Converse needs one
    assert convert_tools(None) is None and convert_tools([]) is None


# -- the Converse client -------------------------------------------------------------------------


class _Runtime:
    """boto3's `bedrock-runtime`: answers `converse` with `reply` and `converse_stream` with
    `events`, keeping each request."""

    def __init__(self, reply: Optional[dict] = None, events: Optional[list] = None):
        self.reply, self.events = reply, events or []
        self.requests: list[tuple[str, dict]] = []

    def converse(self, **request):
        self.requests.append(("converse", request))
        return self.reply

    def converse_stream(self, **request):
        self.requests.append(("converse_stream", request))
        return {"stream": iter(self.events)}


def _answer(*blocks, stop="end_turn"):
    return {"output": {"message": {"content": list(blocks)}}, "stopReason": stop}


def test_a_text_reply_and_the_request_it_came_from():
    runtime = _Runtime(_answer({"text": "hello there"}))
    turn = ConverseClient(client=runtime).complete(
        model="meta.llama4-maverick-17b-instruct-v1:0",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        temperature=0.5, frequency_penalty=0.2,
    )
    assert (turn.text, turn.finish_reason) == ("hello there", "stop")
    method, sent = runtime.requests[0]
    assert method == "converse" and sent["modelId"] == "meta.llama4-maverick-17b-instruct-v1:0"
    # The cache point is its own block after the prompt it closes. It is sent to every model: one
    # that has no prompt caching is retried once without it, since no allowlist can be checked
    # without an AWS account.
    assert sent["system"] == [{"text": "sys"}, {"cachePoint": {"type": "default"}}]
    settings = sent["inferenceConfig"]
    assert settings["temperature"] == 0.5 and settings["maxTokens"] > 0
    assert "frequency_penalty" not in settings and "toolConfig" not in sent


def test_reasoning_text_and_a_tool_call_in_one_reply():
    runtime = _Runtime(_answer(
        {"reasoningContent": {"reasoningText": {"text": "hmm"}}},
        {"text": "let me check"},
        {"toolUse": {"toolUseId": "call-1", "name": "ls", "input": {"path": "."}}},
        stop="tool_use",
    ))
    turn = ConverseClient(client=runtime).complete(
        model="amazon.nova-2-pro-v1:0", messages=[{"role": "user", "content": "list files"}],
        tools=[{"type": "function", "function": {"name": "ls", "parameters": {}}}],
    )
    assert (turn.finish_reason, turn.reasoning, turn.text) == ("tool_calls", "hmm", "let me check")
    [call] = turn.tool_calls
    assert (call.id, call.name, call.arguments) == ("call-1", "ls", {"path": "."})
    assert runtime.requests[0][1]["toolConfig"]["tools"][0]["toolSpec"]["name"] == "ls"


def test_a_stream_yields_text_and_joins_a_calls_input_from_its_pieces():
    def delta(index, **piece):
        return {"contentBlockDelta": {"delta": piece, "contentBlockIndex": index}}

    runtime = _Runtime(events=[
        {"messageStart": {"role": "assistant"}},
        delta(0, text="hel"),
        delta(0, text="lo"),
        {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "c1", "name": "ls"}}, "contentBlockIndex": 1}},
        delta(1, toolUse={"input": '{"path"'}),
        delta(1, toolUse={"input": ': "."}'}),
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_use"}},
    ])
    chunks = list(ConverseClient(client=runtime).stream(model="mistral.mistral-large-3-v1:0", messages=[{"role": "user", "content": "go"}]))
    assert [c.text_delta for c in chunks if c.text_delta] == ["hel", "lo"]
    turn = chunks[-1].turn
    assert (turn.text, turn.finish_reason) == ("hello", "tool_calls")
    [call] = turn.tool_calls
    assert (call.id, call.name, call.arguments) == ("c1", "ls", {"path": "."})


def test_missing_aws_credentials_point_at_settings():
    from botocore.exceptions import NoCredentialsError

    class _NoCredentials:
        def converse(self, **request):
            raise NoCredentialsError()

    with pytest.raises(RuntimeError, match="Settings"):
        ConverseClient(client=_NoCredentials()).complete(model="m", messages=ASK)


# -- credentials ---------------------------------------------------------------------------------


def test_keys_win_over_a_profile_and_nothing_given_is_the_ambient_chain():
    assert session_kwargs("work", "AKIA1", "secret", "token") == {
        "aws_access_key_id": "AKIA1", "aws_secret_access_key": "secret", "aws_session_token": "token",
    }
    assert session_kwargs("work", None, None, None) == {"profile_name": "work"}
    assert session_kwargs(None, None, None, None) == {}


def test_a_bedrock_api_key_is_published_as_the_bearer_token_boto3_reads(monkeypatch):
    import boto3

    class _Session:
        def __init__(self, **kwargs):
            pass

        def client(self, service, **kwargs):
            return object()

    monkeypatch.setattr(boto3.session, "Session", _Session)
    ConverseClient(region="us-east-1", bedrock_api_key="ABSKtest")._ensure_client()
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == "ABSKtest"


# -- which client serves a model -----------------------------------------------------------------


class _Seen(ProviderClient):
    def __init__(self):
        self.models: list[str] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        return AssistantTurn(text="ok")

    def stream(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        yield StreamChunk(turn=AssistantTurn(text="ok"))

    def capabilities(self, model):
        return capabilities_for(model)


def test_claude_ids_go_to_the_claude_client_and_everything_else_to_converse():
    claude, converse = _Seen(), _Seen()
    provider = BedrockProvider(region="us-east-1", claude_client=claude, converse_client=converse)
    for model in ("claude/anthropic.claude-sonnet-4-6-v1:0", "other/amazon.nova-2-pro-v1:0", "meta.llama4-maverick-17b-instruct-v1:0"):
        provider.complete(model=model, messages=ASK)
    assert claude.models == ["anthropic.claude-sonnet-4-6-v1:0"]
    # A raw Bedrock id without a family still works: Converse serves every model.
    assert converse.models == ["amazon.nova-2-pro-v1:0", "meta.llama4-maverick-17b-instruct-v1:0"]


def test_the_claude_family_is_the_native_anthropic_provider_over_bedrock():
    from anthropic import AnthropicBedrock

    from harness.llm.providers import AnthropicProvider

    claude = BedrockProvider(region="us-east-1", profile_name="work")._family_client("claude")
    assert isinstance(claude, AnthropicProvider) and isinstance(claude._client, AnthropicBedrock)


def test_a_bedrock_api_key_is_used_without_the_sigv4_settings():
    """AnthropicBedrock raises outright when it is handed both."""
    client = BedrockProvider(region="us-east-1", bedrock_api_key="ABSKtest", profile_name="work")._family_client("claude")._client
    assert client.api_key == "ABSKtest" and client.aws_profile is None


def test_the_chosen_sign_in_method_drops_what_another_method_left_stored():
    stale = BedrockProvider(
        region="us-east-1", auth_method="profile", bedrock_api_key="ABSKstale", profile_name="work",
        access_key_id="AKIAstale", secret_access_key="stale",
    )
    assert stale._bedrock_api_key is None and stale._access_key_id is None
    client = stale._family_client("claude")._client
    assert client.api_key is None and client.aws_profile == "work"

    keyed = BedrockProvider(region="us-east-1", auth_method="api_key", bedrock_api_key="ABSKlive", profile_name="stale")
    assert keyed._profile_name is None and keyed._family_client("claude")._client.api_key == "ABSKlive"


def test_capabilities_come_from_the_matrix_and_an_unknown_id_gets_the_cautious_default():
    curated = capabilities_for("bedrock:claude/anthropic.claude-sonnet-4-6-v1:0")
    assert curated.vision and curated.pdf and curated.parallel_tool_calls
    assert capabilities_for("bedrock:other/amazon.nova-2-pro-v1:0").tools
    # No guessing from the id's shape: tools on, nothing riskier assumed.
    unknown_claude = capabilities_for("bedrock:claude/us.anthropic.claude-opus-4-8-v1:0")
    assert unknown_claude.tools and not unknown_claude.vision
    unknown_other = capabilities_for("bedrock:other/cohere.command-b-v1:0")
    assert unknown_other.tools and not unknown_other.parallel_tool_calls


def test_a_bedrock_versions_colon_does_not_split_the_provider_prefix():
    from harness.llm.providers.router import ProviderRouter

    model = "bedrock:claude/anthropic.claude-sonnet-4-6-v1:0"
    assert ProviderRouter.__new__(ProviderRouter)._provider_name(model) == "bedrock"
    assert ProviderRouter._bare(model) == "claude/anthropic.claude-sonnet-4-6-v1:0"


# -- the settings form ---------------------------------------------------------------------------


def test_the_form_asks_for_a_region_and_the_fields_of_one_sign_in_method():
    descriptor = get_descriptor("bedrock")
    assert descriptor is not None and descriptor.needs_key
    fields = {field.key: field for field in descriptor.fields}
    assert list(fields) == ["region", "auth_method", "bedrock_api_key", "aws_profile", "aws_access_key_id", "aws_secret_access_key", "aws_session_token"]
    assert [key for key, field in fields.items() if field.required] == ["region"]
    assert {key for key, field in fields.items() if field.secret} == {"bedrock_api_key", "aws_secret_access_key", "aws_session_token"}
    method = fields["auth_method"]
    assert method.default == "api_key" and [c["value"] for c in method.choices] == ["api_key", "profile", "iam"]
    assert fields["bedrock_api_key"].show_when == {"auth_method": "api_key"}
    assert fields["aws_profile"].show_when == {"auth_method": "profile"}
    assert fields["aws_secret_access_key"].show_when == {"auth_method": "iam"}
    assert fields["region"].show_when is None and fields["region"].to_dict()["choices"] == []

    built = build_provider_client("bedrock", {"region": "eu-west-1", "aws_profile": "work"}, None)
    assert isinstance(built, BedrockProvider) and (built._region, built._profile_name) == ("eu-west-1", "work")


def test_bedrock_is_set_up_once_it_has_a_region_and_a_key_provider_once_it_has_a_key(monkeypatch):
    bedrock = get_descriptor("bedrock")
    assert not descriptor_configured(bedrock, {}) and not descriptor_configured(bedrock, {"aws_profile": "work"})
    assert descriptor_configured(bedrock, {"region": "us-east-1"})

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    anthropic = get_descriptor("anthropic")
    assert not descriptor_configured(anthropic, {}) and descriptor_configured(anthropic, {"api_key": "sk-ant-x"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert descriptor_configured(anthropic, {})


# -- checking the settings -----------------------------------------------------------------------


def _aws(monkeypatch, error: Optional[Exception] = None) -> dict:
    """boto3's control-plane client, listing models or raising `error`; returns what it was built with."""
    import boto3

    built: dict = {}

    class _Control:
        def list_foundation_models(self):
            if error:
                raise error
            return {"modelSummaries": []}

    class _Session:
        def __init__(self, **kwargs):
            built["session"] = kwargs

        def client(self, service, **kwargs):
            built["service"], built["client"] = service, kwargs
            return _Control()

    monkeypatch.setattr(boto3.session, "Session", _Session)
    return built


def _check(**fields):
    return verify_provider_key("bedrock", fields={"region": "us-east-1", **fields})


def test_a_profile_is_checked_by_listing_models_in_the_region(monkeypatch):
    built = _aws(monkeypatch)
    assert _check(auth_method="profile", aws_profile="work") == {"ok": True}
    assert (built["service"], built["session"], built["client"]["region_name"]) == ("bedrock", {"profile_name": "work"}, "us-east-1")


def test_each_sign_in_method_names_the_field_it_is_missing(monkeypatch):
    missing_key = _check(auth_method="api_key")
    assert not missing_key["ok"] and "Bedrock API key" in missing_key["error"]
    missing_secret = _check(auth_method="iam", aws_access_key_id="AKIA")
    assert not missing_secret["ok"] and "secret access key" in missing_secret["error"]
    # A blank profile is the default credential chain, not a missing field.
    built = _aws(monkeypatch)
    assert _check(auth_method="profile") == {"ok": True} and built["session"] == {}


def test_a_check_uses_only_the_chosen_methods_fields(monkeypatch):
    built = _aws(monkeypatch)
    assert _check(auth_method="iam", aws_access_key_id="AKIA1", aws_secret_access_key="sec", aws_profile="stale") == {"ok": True}
    assert built["session"] == {"aws_access_key_id": "AKIA1", "aws_secret_access_key": "sec"}


def test_a_checked_api_key_rides_the_bearer_token_and_no_sigv4_session(monkeypatch):
    built = _aws(monkeypatch)
    assert _check(bedrock_api_key="ABSKtest") == {"ok": True}
    assert built["session"] == {} and os.environ["AWS_BEARER_TOKEN_BEDROCK"] == "ABSKtest"


def _client_error(code, cls=None):
    from botocore.exceptions import ClientError

    return (cls or ClientError)({"Error": {"Code": code, "Message": "no"}}, "ListFoundationModels")


@pytest.mark.parametrize(("code", "said"), [("AccessDeniedException", "Bedrock access"), ("UnrecognizedClientException", "rejected")])
def test_what_aws_refused_is_said_in_the_words_of_its_fix(monkeypatch, code, said):
    _aws(monkeypatch, _client_error(code))
    out = _check(auth_method="profile")
    assert not out["ok"] and said in out["error"]


def test_the_modelled_error_classes_boto3_raises_are_read_by_their_code(monkeypatch):
    """Live boto3 raises a subclass named for the code, not `ClientError`; read by class name, the
    specific advice was lost to the generic "Couldn't reach" (owner, 2026-08-17, a real key)."""
    from botocore.exceptions import ClientError

    for code, said in (("AccessDeniedException", "Bedrock access"), ("ExpiredTokenException", "expired")):
        _aws(monkeypatch, _client_error(code, type(code, (ClientError,), {})))
        out = _check(auth_method="profile")
        assert not out["ok"] and said in out["error"] and "Couldn't reach" not in out["error"]
