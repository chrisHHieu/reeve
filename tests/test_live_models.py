"""Live provider model listing (`harness/llm/providers/live_models.py`).

Every case fakes `httpx.get`; no network. The contract under test: the right URL/headers
per provider shape, the parsed bare-id list, a short cache with `refresh`, and failures
returned as `{ok: False, error}` (the manager turns those into the curated fallback).
"""

from __future__ import annotations

import httpx
import pytest

from harness.llm.providers import live_models


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clean_cache():
    live_models.clear_cache()
    yield
    live_models.clear_cache()


def _fake_get(monkeypatch, payload, status=200):
    calls: list[dict] = []

    def _get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "headers": headers or {}, "params": params or {}})
        return _Resp(payload, status)

    monkeypatch.setattr(httpx, "get", _get)
    return calls


def test_an_openai_compatible_provider_lists_and_sorts_its_models(monkeypatch):
    calls = _fake_get(
        monkeypatch,
        {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}, {"id": "deepseek-chat"}]},
    )
    out = live_models.list_provider_models("deepseek", api_key="sk-x")

    assert out["ok"] is True and out["source"] == "live"
    assert out["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert calls[0]["url"] == "https://api.deepseek.com/models"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-x"


def test_a_custom_endpoint_wins_and_the_cache_serves_the_second_call(monkeypatch):
    calls = _fake_get(monkeypatch, {"data": [{"id": "m1"}]})
    first = live_models.list_provider_models(
        "deepseek", api_key="sk-x", base_url="https://proxy.local/v1"
    )
    second = live_models.list_provider_models(
        "deepseek", api_key="sk-x", base_url="https://proxy.local/v1"
    )

    assert first["models"] == second["models"] == ["m1"]
    assert calls[0]["url"] == "https://proxy.local/v1/models"
    assert len(calls) == 1, "the second read must be served from the cache"

    live_models.list_provider_models(
        "deepseek", api_key="sk-x", base_url="https://proxy.local/v1", refresh=True
    )
    assert len(calls) == 2, "refresh must bypass the cache"

    # A different key is a different cache entry.
    live_models.list_provider_models("deepseek", api_key="sk-y", base_url="https://proxy.local/v1")
    assert len(calls) == 3


def test_anthropic_uses_its_own_headers(monkeypatch):
    calls = _fake_get(monkeypatch, {"data": [{"id": "claude-opus-5"}]})
    out = live_models.list_provider_models("anthropic", api_key="sk-a")

    assert out["models"] == ["claude-opus-5"]
    assert calls[0]["url"] == "https://api.anthropic.com/v1/models"
    assert calls[0]["headers"]["x-api-key"] == "sk-a"
    assert calls[0]["headers"]["anthropic-version"] == "2023-06-01"


def test_gemini_lists_generative_models_only(monkeypatch):
    calls = _fake_get(
        monkeypatch,
        {
            "models": [
                {
                    "name": "models/gemini-3.1-pro",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                },
                {"name": "models/gemini-2.5-flash"},
            ]
        },
    )
    out = live_models.list_provider_models("gemini", api_key="gm-x")

    assert out["models"] == ["gemini-2.5-flash", "gemini-3.1-pro"]
    assert calls[0]["url"] == "https://generativelanguage.googleapis.com/v1beta/models"
    assert calls[0]["params"] == {"key": "gm-x"}


def test_ollama_lists_its_native_tags_without_a_key(monkeypatch):
    calls = _fake_get(monkeypatch, {"models": [{"name": "llama3:8b"}, {"name": "qwen3:4b"}]})
    out = live_models.list_provider_models("ollama", base_url="http://127.0.0.1:11434")

    assert out["models"] == ["llama3:8b", "qwen3:4b"]
    assert calls[0]["url"] == "http://127.0.0.1:11434/api/tags"


def test_a_public_provider_lists_without_a_key(monkeypatch):
    calls = _fake_get(monkeypatch, {"data": [{"id": "openai/gpt-5.6-terra"}]})
    out = live_models.list_provider_models("openrouter")

    assert out["ok"] is True and out["models"] == ["openai/gpt-5.6-terra"]
    assert "Authorization" not in calls[0]["headers"]


def test_failures_are_values_not_exceptions(monkeypatch):
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    out = live_models.list_provider_models("deepseek", api_key="sk-x")
    assert out["ok"] is False and out["models"] == [] and "boom" not in out["error"]
    assert "OSError" in out["error"]

    _fake_get(monkeypatch, {"error": "nope"}, status=500)
    out = live_models.list_provider_models("deepseek", api_key="sk-x", refresh=True)
    assert out["ok"] is False and "HTTP 500" in out["error"]


def test_a_keyless_required_provider_refuses_without_calling_out(monkeypatch):
    calls = _fake_get(monkeypatch, {"data": [{"id": "x"}]})
    out = live_models.list_provider_models("deepseek")
    assert out["ok"] is False and "API key" in out["error"]
    assert calls == []


def test_ark_and_codex_report_why_they_cannot_list(monkeypatch):
    out = live_models.list_provider_models("ark-agent-plan-cn", api_key="k")
    assert out["ok"] is False and "no model list endpoint" in out["error"]

    from harness.orchestration import subagent_external

    monkeypatch.setattr(subagent_external, "_codex_cli_models", lambda: None)
    out = live_models.list_provider_models("openai-codex")
    assert out["ok"] is False and "Codex CLI catalog" in out["error"]


def test_the_manager_falls_back_to_the_curated_list(tmp_path, monkeypatch):
    from harness.interface.server.manager import SessionManager

    class _Provider:
        def complete(self, **kwargs):  # pragma: no cover - never called here
            raise AssertionError

        def capabilities(self, model):
            from harness.llm.providers import ModelCapabilities

            return ModelCapabilities()

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        live_models,
        "list_provider_models",
        lambda name, **kwargs: {"ok": False, "models": [], "error": "offline"},
    )
    mgr = SessionManager(workspace=tmp_path, provider=_Provider())

    out = mgr.provider_models("deepseek")
    assert out["ok"] is False and out["source"] == "curated" and out["error"] == "offline"
    assert out["models"] == mgr._suggested_models("deepseek")

    monkeypatch.setattr(
        live_models,
        "list_provider_models",
        lambda name, **kwargs: {"ok": True, "models": ["m-live"], "source": "live"},
    )
    out = mgr.provider_models("deepseek")
    assert out == {"ok": True, "models": ["m-live"], "source": "live"}
