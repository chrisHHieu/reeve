"""Thinking-mode endpoints get their `reasoning_content` back (providers/reasoning.py).

Measured on `zai-coding-plan:glm-5.3-flash` (2026-09-23): a tool-call turn in thinking
mode 400s with "The `reasoning_content` in the thinking mode must be passed back to the
API." The engine used to strip `reasoning` from every outbound message, so the
requirement could never be met. The rule is re-decided per request — a mid-session
model switch must not carry one route's echo into another's strict wire.
"""

from __future__ import annotations

from harness.loop import TurnEngine
from harness.safety.permissions import PermissionEngine
from harness.llm.providers import ModelCapabilities, ProviderClient
from harness.llm.providers.reasoning import REASONING_PLACEHOLDER, reasoning_echo_field
from harness.tools import ToolRegistry


class _Echoing(ProviderClient):
    """A provider with the passback rule under test; no request is made."""

    def __init__(self, field=None):
        self.field = field

    def reasoning_passback(self, model):
        return self.field

    def stream(self, **kwargs):
        raise AssertionError("no request is made here")

    def complete(self, **kwargs):
        raise AssertionError("no request is made here")

    def capabilities(self, model):
        return ModelCapabilities()


def _engine(tmp_path, field, messages):
    return TurnEngine(
        provider=_Echoing(field),
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="zai-coding-plan:glm-5.3-flash",
        messages=messages,
    )


# -- the family table ---------------------------------------------------------------


def test_thinking_mode_families_require_passback():
    for model in ("glm-5.3-flash", "deepseek-v4.1-flash", "kimi-k2-thinking", "mimo-7b"):
        assert reasoning_echo_field(model) == "reasoning_content", model


def test_an_aggregator_is_excluded_even_for_a_family_model():
    """OpenRouter carries `reasoning_details`; a hand-rolled `reasoning_content` is the
    one thing it rejects."""
    assert reasoning_echo_field("deepseek-v4.1-flash", "https://openrouter.ai/api/v1") is None


def test_strict_families_stay_stripped():
    for model in ("gpt-5.6-sol", "claude-sonnet-4-6", "gemini-3-pro", "llama3.3"):
        assert reasoning_echo_field(model) is None, model


# -- the outbound projection --------------------------------------------------------


def _assistant(tmp_path, field, **extra):
    message = {"role": "assistant", "content": "an answer", **extra}
    engine = _engine(tmp_path, field, [message])
    return engine._outbound_messages()[0], engine


def test_reasoning_goes_back_under_the_wire_spelling(tmp_path):
    out, engine = _assistant(
        tmp_path,
        "reasoning_content",
        reasoning="the chain of thought",
        tool_calls=[{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}],
    )

    assert out["reasoning_content"] == "the chain of thought"
    assert "reasoning" not in out
    # History is untouched: the transcript keeps the canonical sidecar.
    assert engine.messages[0]["reasoning"] == "the chain of thought"


def test_a_tool_call_turn_without_reasoning_gets_the_non_empty_placeholder(tmp_path):
    """An empty string is its own 400 on the non-official DeepSeek-compatible relays."""
    out, _ = _assistant(
        tmp_path,
        "reasoning_content",
        tool_calls=[{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}],
    )

    assert out["reasoning_content"] == REASONING_PLACEHOLDER


def test_a_plain_turn_is_not_padded(tmp_path):
    out, _ = _assistant(tmp_path, "reasoning_content")

    assert "reasoning_content" not in out


def test_a_strict_route_still_strips_reasoning(tmp_path):
    out, _ = _assistant(
        tmp_path,
        None,
        reasoning="the chain of thought",
        tool_calls=[{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}],
    )

    assert "reasoning" not in out
    assert "reasoning_content" not in out


# -- the real client's rule ---------------------------------------------------------


def test_the_openai_client_reads_its_endpoint_host():
    from harness.llm.providers.chat import OpenAIProvider

    zai = OpenAIProvider(base_url="https://api.z.ai/api/coding/paas/v4")
    openrouter = OpenAIProvider(base_url="https://openrouter.ai/api/v1")

    assert zai.reasoning_passback("glm-5.3-flash") == "reasoning_content"
    assert openrouter.reasoning_passback("glm-5.3-flash") is None
    assert zai.reasoning_passback("gpt-5.6-sol") is None


# -- through the router the engine actually holds ---------------------------------------------


def test_the_router_and_the_gateway_answer_for_the_route_they_send_to(tmp_path):
    """The engine asks its provider, and in the product that is the router. Until 2026-10-10 the
    router (and OpenCode Go's wrapper) answered the base's None, so no thinking-mode route was ever
    sent its reasoning back: DeepSeek through OpenCode Go refused 7 requests that day for it."""
    from harness.llm.providers import OpenAIProvider, ProviderRouter
    from harness.llm.providers.opencode_go import OpenCodeGoProvider

    router = ProviderRouter(secrets=None)
    router._clients["opencode-go"] = OpenCodeGoProvider(api_key="test", base_url="https://gateway.test/v1")
    router._clients["openai"] = OpenAIProvider(api_key="test")
    assert router.reasoning_passback("opencode-go:deepseek-v4.1-flash") == "reasoning_content"
    assert router.reasoning_passback("openai:gpt-5.5") is None

    engine = TurnEngine(
        provider=router,
        registry=ToolRegistry(),
        permissions=PermissionEngine(workspace_root=tmp_path),
        model="opencode-go:deepseek-v4.1-flash",
        messages=[
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "reasoning": "first read it",
             "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "text"},
        ],
    )
    sent = [m for m in engine._outbound_messages() if m["role"] == "assistant"]
    assert sent[0]["reasoning_content"] == "first read it"
