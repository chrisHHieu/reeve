"""Settings ▸ Web search, the backend (`manager/web_search_settings.py`).

What must hold: switching the provider keeps a saved key (it used to be thrown away); a key is
removed only when removal is asked for; the screen is told where the key a search will use
comes from; a test search says who answered and, when the chain fell through, why — and no
answer, field or error ever carries the key.
"""

from __future__ import annotations

import pytest

from harness.interface.server import SessionManager
from harness.integrations.web.providers import FallbackProvider, SearchResult, WebSearchProvider

KEY = "test-key-0123456789"


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    return SessionManager(data_dir=tmp_path / "data")


def test_a_fresh_install_runs_the_chain_with_no_key(manager):
    got = manager.get_web_search()
    assert got["mode"] == "auto"
    assert got["destination"] == "exa → duckduckgo"
    assert got["has_key"] is False and got["key_source"] is None


def test_switching_the_provider_keeps_the_key(manager):
    assert manager.set_web_search("auto", KEY)["ok"]
    saved = manager.get_web_search()
    assert saved["has_key"] and saved["key_source"] == "settings" and saved["key_set_at"]
    # DuckDuckGo only, then back: the key is still there to be used.
    manager.set_web_search("duckduckgo")
    assert manager.get_web_search()["mode"] == "duckduckgo"
    assert manager.get_web_search()["has_key"] is True
    manager.set_web_search("auto")
    assert manager.get_web_search()["has_key"] is True
    assert manager.secrets.get("web_search:default")["api_key"] == KEY


def test_a_key_goes_only_when_removal_is_asked_for(manager):
    manager.set_web_search("auto", KEY)
    manager.set_web_search("auto", "")  # an empty field is not "remove"
    assert manager.get_web_search()["has_key"] is True
    manager.set_web_search("auto", remove_key=True)
    got = manager.get_web_search()
    assert got["has_key"] is False and got["key_set_at"] is None and got["key_source"] is None


def test_the_screen_is_told_where_the_key_comes_from(manager, monkeypatch):
    manager.secrets.put("exa:default", {"api_key": "from-the-connector-card"})
    assert manager.get_web_search()["key_source"] == "connector"
    monkeypatch.setenv("EXA_API_KEY", "from-the-environment")
    assert manager.get_web_search()["key_source"] == "environment"
    manager.set_web_search("auto", KEY)
    # The order `_provider_key` reads them in: the one saved here wins.
    assert manager.get_web_search()["key_source"] == "settings"


class _Answers(WebSearchProvider):
    def __init__(self, name: str, results=None, error: str = "") -> None:
        self.name = name
        self._results = results or []
        self._error = error

    def search(self, query, max_results=5, options=None):
        if self._error:
            raise RuntimeError(self._error)
        return self._results


def _chain(primary: WebSearchProvider, fallback: WebSearchProvider):
    return lambda secrets=None: FallbackProvider(primary, fallback)


def test_a_test_search_says_who_answered(manager, monkeypatch):
    hit = SearchResult(title="A page", url="https://a.example/", snippet="s")
    monkeypatch.setattr(
        "harness.integrations.web.tool.resolve_provider",
        _chain(_Answers("exa", [hit, hit]), _Answers("duckduckgo", [hit])),
    )
    got = manager.test_web_search("widgets")
    assert got["ok"] is True and got["provider"] == "exa" and got["results"] == 2
    assert got["first"] == {"title": "A page", "url": "https://a.example/"}
    assert "fallback_from" not in got and got["query"] == "widgets"


def test_a_rejected_key_shows_as_a_fallback_and_never_shows_the_key(manager, monkeypatch):
    manager.set_web_search("auto", KEY)
    hit = SearchResult(title="T", url="https://b.example/", snippet="s")
    monkeypatch.setattr(
        "harness.integrations.web.tool.resolve_provider",
        _chain(
            _Answers("exa", error=f"exa api returned HTTP 401 for key {KEY}"),
            _Answers("duckduckgo", [hit]),
        ),
    )
    got = manager.test_web_search("")
    assert got["ok"] is True and got["provider"] == "duckduckgo"
    assert got["fallback_from"] == "exa" and "HTTP 401" in got["fallback_reason"]
    assert KEY not in str(got)
    assert got["query"]  # an empty query runs the default one


def test_a_search_that_fails_outright_is_a_sentence_without_the_key(manager, monkeypatch):
    manager.set_web_search("auto", KEY)

    def broken(secrets=None):
        raise RuntimeError(f"bad config {KEY}")

    monkeypatch.setattr("harness.integrations.web.tool.resolve_provider", broken)
    got = manager.test_web_search("q")
    assert got["ok"] is False and got["error"].startswith("The search failed")
    assert KEY not in str(got)


def test_the_routes_serve_the_screen(manager, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server.app import create_app

    client = TestClient(create_app(manager))
    assert client.post("/v1/web-search", json={"provider": "auto", "api_key": KEY}).json()["ok"]
    assert client.post("/v1/web-search", json={"provider": "duckduckgo"}).json()["ok"]
    got = client.get("/v1/web-search")
    assert got.json()["mode"] == "duckduckgo" and got.json()["has_key"] is True
    assert KEY not in got.text
    assert client.post("/v1/web-search", json={"provider": "auto", "remove_key": True}).json()["ok"]
    assert client.get("/v1/web-search").json()["has_key"] is False

    hit = SearchResult(title="T", url="https://c.example/", snippet="s")
    monkeypatch.setattr(
        "harness.integrations.web.tool.resolve_provider", _chain(_Answers("exa", [hit]), _Answers("duckduckgo"))
    )
    tested = client.post("/v1/web-search/test", json={"query": "q"}).json()
    assert tested["ok"] is True and tested["provider"] == "exa"
