"""Session cache affinity (providers/base.py `CACHE_AFFINITY`).

The context freeze keeps the request prefix byte-stable; `prompt_cache_key` is the other
half — OpenAI routes requests with the same key to the same backend, so the cache that
prefix earned actually gets read. The engine injects the session id once per session;
only the stock OpenAI providers translate it, and no provider ever forwards the private
key under its own name (compat endpoints reject unknown fields).
"""

from __future__ import annotations

from types import SimpleNamespace

from harness.llm.providers.base import CACHE_AFFINITY


class _Stub:
    def complete(self, **kwargs):  # pragma: no cover - never called here
        raise NotImplementedError

    def capabilities(self, model):
        from harness.llm.providers import ModelCapabilities

        return ModelCapabilities()


def _engine(tmp_path, *, session_id=None):
    from harness.compose import build_engine
    from harness.modes.agents import reeve_agent

    return build_engine(
        agent=reeve_agent(),
        workspace=tmp_path,
        provider=_Stub(),
        session_id=session_id,
    )


def test_build_engine_carries_the_session_key(tmp_path):
    engine = _engine(tmp_path, session_id="sess_abc123")
    try:
        assert engine.model_settings[CACHE_AFFINITY] == "sess_abc123"
    finally:
        engine.executor.close()


def test_without_a_session_id_no_key_is_injected(tmp_path):
    engine = _engine(tmp_path)
    try:
        assert CACHE_AFFINITY not in engine.model_settings
    finally:
        engine.executor.close()


def test_child_engines_drop_the_parents_affinity(tmp_path):
    """A child has its own instructions and toolset, so the parent's prefix key is
    meaningless to it — and a child is short-lived, with no prefix worth pinning."""
    from harness.tools.task_agents import AgentInfo
    from harness.tools.task_runtime import build_child_engine

    engine = build_child_engine(
        workspace=tmp_path,
        provider=_Stub(),
        model="gpt-5.5",
        agent=AgentInfo(name="general"),
        model_settings={CACHE_AFFINITY: "sess_parent", "temperature": 0.2},
    )
    try:
        assert CACHE_AFFINITY not in engine.model_settings
        assert engine.model_settings["temperature"] == 0.2  # real settings survive
    finally:
        engine.executor.close()


# -- the OpenAI translation ---------------------------------------------------------


class _FakeCompletions:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.chat = SimpleNamespace(completions=_FakeCompletions(response))


def _response():
    message = SimpleNamespace(content="ok", tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice])


def _chat_provider(**kwargs):
    from harness.llm.providers import OpenAIProvider

    client = _FakeClient(_response())
    return OpenAIProvider(client=client, **kwargs), client


def test_chat_completions_translates_the_key_to_prompt_cache_key():
    provider, client = _chat_provider()
    provider.complete(
        model="gpt-5.5",
        messages=[{"role": "user", "content": "hi"}],
        **{CACHE_AFFINITY: "sess_abc123"},
    )
    sent = client.chat.completions.calls[0]
    assert sent["prompt_cache_key"] == "sess_abc123"
    # The private key must not leak under its own name.
    assert CACHE_AFFINITY not in sent


def test_a_compat_endpoint_gets_no_unknown_field():
    """Ollama and other `base_url` backends have never heard of `prompt_cache_key`;
    some reject unknown parameters, so the key is dropped entirely."""
    provider, client = _chat_provider(base_url="http://localhost:11434/v1")
    provider.complete(
        model="llama3.3",
        messages=[{"role": "user", "content": "hi"}],
        **{CACHE_AFFINITY: "sess_abc123"},
    )
    sent = client.chat.completions.calls[0]
    assert "prompt_cache_key" not in sent
    assert CACHE_AFFINITY not in sent


def _responses_provider(**kwargs):
    from harness.llm.providers.responses import OpenAIResponsesProvider

    return OpenAIResponsesProvider(client=object(), **kwargs)


def test_responses_api_also_translates_the_key():
    provider = _responses_provider()
    kwargs = provider._request_kwargs(
        model="gpt-5.6-sol",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        settings={CACHE_AFFINITY: "sess_abc123"},
    )
    assert kwargs["prompt_cache_key"] == "sess_abc123"
    assert CACHE_AFFINITY not in kwargs


def test_responses_with_base_url_gets_no_unknown_field():
    provider = _responses_provider(base_url="http://gateway.local/v1")
    kwargs = provider._request_kwargs(
        model="gpt-5.6-sol",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        settings={CACHE_AFFINITY: "sess_abc123"},
    )
    assert "prompt_cache_key" not in kwargs
    assert CACHE_AFFINITY not in kwargs
