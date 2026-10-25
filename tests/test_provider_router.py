"""Many providers behind one client: which provider a model id reaches, what the OpenAI-compatible
client is built with (Ollama included), tool calls a local model writes as text, and how the
manager keeps the providers' settings and the model list.

No SDK call leaves the test; a provider is a recorder or an OpenAI client over a canned reply.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from harness.llm.providers import AssistantTurn, ModelCapabilities, OpenAIProvider, ProviderClient, ProviderRouter, StreamChunk, capabilities_for
from harness.llm.providers.chat.salvage import salvage_tool_calls_from_text as salvage
from harness.llm.providers.registry import build_provider_client, normalize_ollama_url


def _built_with(monkeypatch) -> dict:
    """`openai.OpenAI`, recording what it was built with."""
    built: dict = {}

    class _OpenAI:
        def __init__(self, **kwargs):
            built.update(kwargs)

    monkeypatch.setattr("openai.OpenAI", _OpenAI)
    return built


def _chat(content=None, tool_calls=None):
    """An OpenAI-compatible client answering one message."""
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls), finish_reason="stop")])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **request: reply)))


def _manager(tmp_path, monkeypatch, *, clear_keys=()):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    for name in clear_keys:
        monkeypatch.delenv(name, raising=False)
    from harness.interface.server.manager import SessionManager

    return SessionManager(data_dir=tmp_path)


def _providers(manager) -> dict:
    return {row["name"]: row for row in manager.get_providers()}


# -- the OpenAI-compatible client ----------------------------------------------------------------


def test_the_client_takes_the_endpoint_given_and_leaves_retries_to_the_engine(monkeypatch):
    from harness.llm.providers.timeouts import STREAM_IDLE_SECONDS

    built = _built_with(monkeypatch)
    OpenAIProvider(api_key="ollama", base_url="http://localhost:11434/v1")._ensure_client()
    assert (built["api_key"], built["base_url"]) == ("ollama", "http://localhost:11434/v1")
    assert built["max_retries"] == 0  # one visible attempt each
    assert built["timeout"].read == STREAM_IDLE_SECONDS  # a stalled stream fails as a retryable error


def test_without_an_endpoint_the_client_gets_none(monkeypatch):
    built = _built_with(monkeypatch)
    OpenAIProvider(api_key="sk-x")._ensure_client()
    assert "base_url" not in built


@pytest.mark.parametrize(
    ("typed", "used"),
    [(None, "http://localhost:11434/v1"), ("  ", "http://localhost:11434/v1"), ("http://localhost:11434", "http://localhost:11434/v1"), ("http://h:1/v1/", "http://h:1/v1")],
)
def test_an_ollama_address_is_completed_to_its_v1_endpoint(typed, used):
    assert normalize_ollama_url(typed) == used


def test_ollama_is_reached_at_its_address_with_a_placeholder_key(monkeypatch):
    built = _built_with(monkeypatch)
    build_provider_client("ollama", {"base_url": "http://box:11434"}, secrets=None)._ensure_client()  # type: ignore[attr-defined]
    assert built["base_url"] == "http://box:11434/v1" and built["api_key"] == "ollama"  # Ollama ignores the key


def test_the_native_providers_find_their_key_at_the_first_call_and_openai_picks_its_api_by_endpoint(monkeypatch):
    from harness.llm.providers import AnthropicProvider, GeminiProvider, OpenAIResponsesProvider

    anthropic = build_provider_client("anthropic", {"api_key": "sk-ant-x"}, None)
    assert isinstance(anthropic, AnthropicProvider) and anthropic._api_key == "sk-ant-x"
    gemini = build_provider_client("gemini", {"api_key": "AIza-x"}, None)
    assert isinstance(gemini, GeminiProvider) and gemini._api_key == "AIza-x"
    for name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="Anthropic"):
        build_provider_client("anthropic", {}, None)._ensure_client()
    with pytest.raises(RuntimeError, match="Gemini"):
        build_provider_client("gemini", {}, None)._ensure_client()
    # A custom endpoint (Azure, OpenRouter, vLLM) keeps Chat Completions; none means the Responses API.
    custom = build_provider_client("openai", {"base_url": "https://my.azure.example/openai/v1"}, None)
    assert isinstance(custom, OpenAIProvider) and custom._base_url == "https://my.azure.example/openai/v1"
    assert isinstance(build_provider_client("openai", {}, None), OpenAIResponsesProvider)


# -- the router ----------------------------------------------------------------------------------


class _Named(ProviderClient):
    def __init__(self, name: str):
        self.name, self.models = name, []

    def complete(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        return AssistantTurn(text=self.name)

    def stream(self, *, model, messages, tools=None, **settings):
        self.models.append(model)
        yield StreamChunk(turn=AssistantTurn(text=self.name))

    def capabilities(self, model):
        return ModelCapabilities()


def _builds(monkeypatch) -> list[_Named]:
    """Every client the router builds, each new, so a rebuild can be seen."""
    built: list[_Named] = []
    monkeypatch.setattr("harness.llm.providers.router.build_provider_client", lambda name, profile, secrets: built.append(_Named(name)) or built[-1])
    return built


def test_a_prefixed_id_reaches_its_provider_without_the_prefix_and_a_bare_one_reaches_openai(monkeypatch):
    built = _builds(monkeypatch)
    router = ProviderRouter(secrets=None)
    assert router.complete(model="ollama:llama3.3", messages=[]).text == "ollama"
    router.complete(model="gpt-5.5", messages=[])
    assert [(client.name, client.models) for client in built] == [("ollama", ["llama3.3"]), ("openai", ["gpt-5.5"])]


def test_a_providers_client_is_built_once_and_again_after_it_is_invalidated(monkeypatch):
    built = _builds(monkeypatch)
    router = ProviderRouter(secrets=None)
    first = router._client_for("ollama:a")
    assert router._client_for("ollama:b") is first and len(built) == 1
    router.invalidate("ollama")
    assert router._client_for("ollama:c") is not first and len(built) == 2


def test_only_a_known_providers_prefix_is_split_off():
    router = ProviderRouter(secrets=None)
    assert router._bare("ollama:qwen2.5-coder:32b") == "qwen2.5-coder:32b"
    assert router._bare("gpt-5.5") == "gpt-5.5"
    # A version tag is not a provider: split, OpenAI would be asked for "32b".
    assert router._bare("qwen2.5-coder:32b") == "qwen2.5-coder:32b"
    assert router._provider_name("qwen2.5-coder:32b") == "openai"


def test_ollama_models_take_tools_one_call_at_a_time_and_no_images():
    for caps in (ProviderRouter(secrets=None).capabilities("ollama:qwen2.5-coder"), capabilities_for("ollama:qwen2.5-coder")):
        assert caps.tools is True and caps.parallel_tool_calls is False
    assert capabilities_for("ollama:qwen2.5-coder").vision is False


def test_the_native_providers_take_parallel_calls_images_and_stream():
    for model in ("anthropic:claude-sonnet-4-6", "gemini:gemini-2.5-flash"):
        caps = capabilities_for(model)
        assert caps.tools and caps.vision and caps.streaming and caps.parallel_tool_calls is True


def test_each_call_reports_the_provider_it_used_and_a_broken_report_costs_nothing():
    seen: list[str] = []
    router = ProviderRouter(on_use=seen.append)
    router._clients["openai"] = OpenAIProvider(client=_chat(content="hi"))
    router._clients["zai"] = OpenAIProvider(client=_chat(content="hi"))
    router.complete(model="gpt-5.5", messages=[])
    router.complete(model="zai:glm-5.2", messages=[])
    assert seen == ["openai", "zai"]

    def down(name):
        raise RuntimeError("telemetry down")

    broken = ProviderRouter(on_use=down)
    broken._clients["openai"] = OpenAIProvider(client=_chat(content="ok"))
    assert broken.complete(model="gpt-5.5", messages=[]).text == "ok"


# -- tool calls written as text ------------------------------------------------------------------

_OFFERED = [
    {"type": "function", "function": {"name": "todo_write", "parameters": {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}}},
    {"type": "function", "function": {"name": "list_files", "parameters": {"type": "object", "properties": {"recursive": {"type": "boolean"}}}}},
]


@pytest.mark.parametrize(
    ("text", "calls"),
    [
        ('{"name": "get_weather", "arguments": {"city": "Paris"}}', [("get_weather", {"city": "Paris"})]),
        ('<tool_call>{"name": "a", "arguments": {"x": 1}}</tool_call>', [("a", {"x": 1})]),
        ('[{"name": "a", "arguments": {}}, {"name": "b", "arguments": {"y": 2}}]', [("a", {}), ("b", {"y": 2})]),
        ('{"name": "a", "arguments": "{\\"k\\": 1}"}', [("a", {"k": 1})]),  # arguments as a JSON string
    ],
)
def test_a_call_a_model_wrote_as_json_is_read_back_as_a_call(text, calls):
    assert [(c.name, c.arguments) for c in salvage(text)] == calls


@pytest.mark.parametrize("text", ['{"city": "Paris", "temp": 18}', "The weather in Paris is sunny.", ""])
def test_json_that_is_not_a_call_and_prose_stay_text(text):
    assert salvage(text) == []


def test_a_call_after_prose_is_found_by_the_offered_tools_name():
    calls = salvage('It seems the workspace is empty. {"name": "list_files", "arguments": {"recursive": true}}', _OFFERED)
    assert [(c.name, c.arguments) for c in calls] == [("list_files", {"recursive": True})]


def test_a_tool_name_followed_by_its_arguments_is_a_call():
    # From a real session: `todo_write [ {…}, {…} ]`, the bare array being the tool's one parameter.
    items = [{"content": "Understand requirements", "status": "in_progress"}, {"content": "Plan", "status": "pending"}]
    import json

    [call] = salvage(f"todo_write {json.dumps(items)}", _OFFERED)
    assert (call.name, call.arguments) == ("todo_write", {"items": items})
    [call] = salvage('list_files {"recursive": false}', _OFFERED)
    assert (call.name, call.arguments) == ("list_files", {"recursive": False})


def test_a_call_to_a_tool_nobody_offered_is_left_as_text():
    assert salvage('{"name": "rm_rf", "arguments": {"path": "/"}}', _OFFERED) == []


def test_nested_braces_inside_a_tag_are_one_call():
    [call] = salvage('<tool_call>{"name": "todo_write", "arguments": {"items": [{"content": "a", "status": "pending"}]}}</tool_call>', _OFFERED)
    assert (call.name, call.arguments) == ("todo_write", {"items": [{"content": "a", "status": "pending"}]})


def test_text_is_read_as_a_call_only_when_tools_were_offered():
    written = '{"name": "get_weather", "arguments": {"city": "Paris"}}'
    turn = OpenAIProvider(client=_chat(content=written)).complete(model="ollama:x", messages=[], tools=[{"type": "function", "function": {"name": "get_weather"}}])
    assert turn.has_tool_calls and turn.tool_calls[0].name == "get_weather" and turn.text is None
    plain = OpenAIProvider(client=_chat(content=written)).complete(model="ollama:x", messages=[])
    assert not plain.has_tool_calls and plain.text == written


# -- the manager's providers ---------------------------------------------------------------------


def test_the_manager_saves_a_providers_settings_and_never_lists_its_key(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    assert isinstance(manager.provider, ProviderRouter)
    assert manager.set_provider("ollama", {"base_url": "http://localhost:9999"})["ok"] is True
    rows = _providers(manager)
    assert rows["ollama"]["configured"] is True  # keyless, so usable
    assert rows["ollama"]["values"]["base_url"] == "http://localhost:9999"
    assert rows["openai"]["needs_key"] is True and "api_key" not in rows["openai"].get("values", {})
    assert manager.set_provider("nope", {})["ok"] is False


def test_a_key_sets_up_a_provider_and_adds_its_first_model(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, clear_keys=("ANTHROPIC_API_KEY", "GEMINI_API_KEY"))
    rows = _providers(manager)
    assert rows["anthropic"]["configured"] is False and rows["gemini"]["needs_key"] is True
    assert "claude-sonnet-4-6" in rows["anthropic"]["suggested_models"]
    assert "gemini-2.5-flash" in rows["gemini"]["suggested_models"]
    assert manager.set_provider("anthropic", {"api_key": "sk-ant-test"})["ok"] is True
    rows = _providers(manager)
    assert rows["anthropic"]["configured"] is True and "api_key" not in rows["anthropic"].get("values", {})
    assert "anthropic:claude-fable-5" in manager.get_settings()["models"]
    # A key in the environment alone sets a provider up.
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-env")
    assert _providers(manager)["gemini"]["configured"] is True


def test_suggested_models_are_named_without_the_provider_prefix(tmp_path, monkeypatch):
    rows = _providers(_manager(tmp_path, monkeypatch))
    assert "gpt-5.5" in rows["openai"]["suggested_models"]
    ollama = rows["ollama"]["suggested_models"]
    assert isinstance(ollama, list) and all(not name.startswith("ollama:") for name in ollama)


def test_the_first_provider_given_a_key_takes_the_default_and_the_next_does_not_steal_it(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, clear_keys=("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"))
    assert manager.model == "gpt-5.6-sol"  # the built-in default, OpenAI not set up
    manager.set_provider("anthropic", {"api_key": "sk-ant-x"})
    assert manager.model == "anthropic:claude-fable-5"
    manager.set_provider("gemini", {"api_key": "AIza-x"})
    assert manager.model == "anthropic:claude-fable-5"


def test_setting_up_ollama_adds_its_first_known_model_and_nothing_when_none_is_known(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "_suggested_models", lambda name: ["qwen3-coder:30b"] if name == "ollama" else [])
    assert manager.set_provider("ollama", {"base_url": "http://localhost:11434"}) == {"ok": True, "provider": "ollama"}
    assert "ollama:qwen3-coder:30b" in manager.get_settings()["models"]
    assert manager.model == "ollama:qwen3-coder:30b"  # the old default's provider was not set up

    other = _manager(tmp_path / "other", monkeypatch)
    monkeypatch.setattr(other, "_suggested_models", lambda name: [])
    other.set_provider("ollama", {"base_url": "http://localhost:11434"})
    assert "ollama:qwen3-coder:30b" not in other.get_settings()["models"] and other.model == "gpt-5.6-sol"


def test_the_model_list_is_the_set_up_providers_models_plus_what_was_added(tmp_path, monkeypatch):
    """No seed list: the curated matrix of providers that hold a key, plus ids the person added."""
    from harness.interface.server.manager import SessionManager
    from harness.llm.providers.registry import provider_descriptors

    keys = [d.env_key for d in provider_descriptors() if d.env_key]  # a dev shell's keys stay out
    manager = _manager(tmp_path, monkeypatch, clear_keys=keys)
    # Whether Ollama answers is a probe of a local server (test_settings.py covers it); pinned, or
    # this would pass only where Ollama happens to run.
    monkeypatch.setattr(SessionManager, "_ollama_alive", lambda self: True)

    def models():
        return manager.get_settings()["models"]

    assert models() == [manager.model]  # nothing but the default
    manager.set_provider("anthropic", {"api_key": "sk-ant-test"})
    assert "anthropic:claude-opus-4-8" in models() and "gpt-4o" not in models()

    added = manager.add_model("ollama:qwen2.5-coder:32b")
    assert added["ok"] and "ollama:qwen2.5-coder:32b" in added["models"]
    count = len(models())
    manager.add_model("ollama:qwen2.5-coder:32b")
    assert len(models()) == count

    # Removing a curated model hides it, across a restart, until it is added back.
    assert "anthropic:claude-haiku-4-5" not in manager.remove_model("anthropic:claude-haiku-4-5")["models"]
    assert "anthropic:claude-haiku-4-5" not in SessionManager(data_dir=tmp_path).get_settings()["models"]
    manager.add_model("anthropic:claude-haiku-4-5")
    assert "anthropic:claude-haiku-4-5" in models()

    manager.remove_model("ollama:qwen2.5-coder:32b")
    assert "ollama:qwen2.5-coder:32b" not in models()
    manager.remove_model(manager.model)
    assert manager.model in models()  # the default stays selectable
    assert manager.add_model("  ")["ok"] is False


def test_the_settings_pane_sees_when_a_key_was_set_and_last_used(tmp_path, monkeypatch):
    from harness.interface.server.manager import SessionManager

    manager = _manager(tmp_path, monkeypatch)
    today = date.today().isoformat()
    manager.set_provider("deepseek", {"api_key": "ds-key"})
    row = _providers(manager)["deepseek"]
    assert (row["configured"], row["key_set_at"], row["last_used_at"]) == (True, today, None)
    # Saving only the endpoint leaves the key's date alone.
    manager.set_provider("deepseek", {"base_url": "https://api.deepseek.com/v1"})
    assert _providers(manager)["deepseek"]["key_set_at"] == today

    manager._note_provider_use("deepseek")
    first = manager._prefs["provider_last_used"]["deepseek"]
    manager._note_provider_use("deepseek")  # inside the 60 s window: not written again
    assert manager._prefs["provider_last_used"]["deepseek"] == first
    assert _providers(manager)["deepseek"]["last_used_at"] == first
    assert _providers(SessionManager(data_dir=tmp_path))["deepseek"]["last_used_at"] == first
