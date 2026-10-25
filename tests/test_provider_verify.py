"""Which provider a pasted key belongs to, and the Test button (`verify_provider_key`): one read-only
request to the endpoint the session would use, and its answer put in a sentence. `httpx.get` and
`httpx.post` are replaced, so nothing is reached.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from harness.llm.providers import detect_provider, verify_provider_key


@pytest.mark.parametrize(
    ("key", "provider"),
    [
        ("sk-ant-api03-abc", "anthropic"),
        ("sk-or-v1-abc", "openrouter"),
        ("AIzaSyAbc123", "gemini"),
        ("sk-proj-abc", "openai"),
        ("sk_live_abc", "openai"),
        ("", None),
        ("   ", None),
        ("nonsense", None),
    ],
)
def test_a_key_is_known_by_its_prefix(key, provider):
    assert detect_provider(key) == provider


class _Probe:
    """Stands in for one httpx verb: keeps the request it was sent and answers with a status."""

    def __init__(self, monkeypatch, verb="get", status=200, fails=None):
        self.sent: dict = {}
        self._status, self._fails = status, fails
        monkeypatch.setattr(f"httpx.{verb}", self)

    def __call__(self, url, **request):
        self.sent.update(url=url, **request)
        if self._fails:
            raise self._fails
        return SimpleNamespace(status_code=self._status)


# -- where the probe goes and what it carries ----------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "settings", "url"),
    [
        ("openai", {"api_key": "sk-x"}, "https://api.openai.com/v1/models"),
        ("openai", {"api_key": "sk-x", "base_url": "https://gw.example/openai/v1/"}, "https://gw.example/openai/v1/models"),  # the slash goes
        ("anthropic", {"api_key": "sk-ant-x"}, "https://api.anthropic.com/v1/models"),
        # a proxy user must not hear "Invalid API key" from a probe that went to api.anthropic.com
        ("anthropic", {"api_key": "sk-ant-x", "base_url": "https://proxy.lan/"}, "https://proxy.lan/v1/models"),
        ("gemini", {"api_key": "AIza-x", "base_url": "https://gw.lan"}, "https://gw.lan/v1beta/models"),
        ("gemini", {"api_key": "AIza-x", "base_url": "   "}, "https://generativelanguage.googleapis.com/v1beta/models"),  # blank is the default
        ("ollama", {"base_url": "http://localhost:11434"}, "http://localhost:11434/v1/models"),
    ],
)
def test_the_probe_asks_the_endpoint_the_session_would_use(monkeypatch, provider, settings, url):
    probe = _Probe(monkeypatch)
    verify_provider_key(provider, **settings)
    assert probe.sent["url"] == url


def test_each_provider_is_sent_its_own_kind_of_key(monkeypatch):
    probe = _Probe(monkeypatch)
    assert verify_provider_key("openai", api_key="sk-x") == {"ok": True}
    assert probe.sent["headers"]["Authorization"] == "Bearer sk-x"
    verify_provider_key("anthropic", api_key="sk-ant-x")
    assert probe.sent["headers"]["x-api-key"] == "sk-ant-x" and "anthropic-version" in probe.sent["headers"]
    verify_provider_key("gemini", api_key="AIza-x")
    assert probe.sent["params"]["key"] == "AIza-x"
    keyless = _Probe(monkeypatch)
    verify_provider_key("ollama", base_url="http://localhost:11434")
    assert "headers" not in keyless.sent


@pytest.mark.parametrize(
    ("provider", "base_url", "model"),
    [
        ("ark", "https://ark.ap-southeast.bytepluses.com/api/v3", "dola-seed-evolving-latest-version"),
        ("ark-agent-plan-cn", "https://ark.cn-beijing.volces.com/api/plan/v3", "doubao-seed-evolving"),
    ],
)
def test_ark_is_probed_with_a_one_token_response_that_is_not_kept(monkeypatch, provider, base_url, model):
    probe = _Probe(monkeypatch, verb="post")
    assert verify_provider_key(provider, api_key="ark-key") == {"ok": True}
    assert probe.sent["url"] == base_url + "/responses"
    assert probe.sent["headers"]["Authorization"] == "Bearer ark-key"
    assert probe.sent["json"] == {"model": model, "input": "Reply with OK.", "max_output_tokens": 1, "store": False}


def test_an_ark_profile_can_point_the_probe_elsewhere(monkeypatch):
    probe = _Probe(monkeypatch, verb="post")
    verify_provider_key("ark", api_key="ark-key", base_url="https://gateway.example/ark/v3/")
    assert probe.sent["url"] == "https://gateway.example/ark/v3/responses"


# -- what the answer says ------------------------------------------------------------------------


def test_a_refused_key_is_called_invalid(monkeypatch):
    _Probe(monkeypatch, status=401)
    assert verify_provider_key("openai", api_key="sk-bad") == {"ok": False, "error": "Invalid API key."}


def test_an_unreachable_endpoint_and_an_odd_status_are_said_as_such(monkeypatch):
    _Probe(monkeypatch, fails=ConnectionError("boom"))
    unreachable = verify_provider_key("openai", api_key="sk-x")
    assert unreachable["ok"] is False and "Couldn't reach" in unreachable["error"]
    _Probe(monkeypatch, status=500)
    odd = verify_provider_key("anthropic", api_key="sk-ant-x")
    assert odd["ok"] is False and "500" in odd["error"]


def test_a_malformed_endpoint_is_reported_and_nothing_is_sent(monkeypatch):
    # never raises, and never blames the network for a typo in the URL field
    probe = _Probe(monkeypatch)
    said = verify_provider_key("anthropic", api_key="sk-ant-x", base_url="proxy.lan")
    assert said["ok"] is False and "http(s)" in said["error"]
    assert probe.sent == {}
