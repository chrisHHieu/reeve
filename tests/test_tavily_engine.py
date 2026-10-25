"""Tavily as a `web_search` engine, set up in Settings ▸ Web search like Exa.

What must hold: Tavily runs only on its own key (saved in the profile as `tavily_api_key`, or
`TAVILY_API_KEY`), never on the profile's `api_key`, which is Exa's; its answer becomes the same
results Exa's does, dates as ISO, chunks as passages, filters sent to Tavily; a failure falls
through to DuckDuckGo; a key Tavily refuses is not saved; choosing it needs a key; removing its
key sends searches back to Exa; no answer or error carries a key; it is no longer a connector;
and the review counts a session that searched through both `web_search` and a connector's tool.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from harness.integrations.connectors.descriptors import get_descriptor
from harness.integrations.web import (
    DuckDuckGoProvider,
    ExaProvider,
    FallbackProvider,
    TavilyProvider,
    provider_name,
    resolve_provider,
    search_engine,
)
from harness.integrations.web.search_filters import SearchOptions
from harness.interface.server.manager import SessionManager
from harness.safety.secrets import SecretStore

TAVILY_KEY = "tvly-test-0123456789"
EXA_KEY = "exa-test-0123456789"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    return SecretStore(tmp_path / "secrets.json")


class _Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


#: The shape Tavily's REST API answered with on 2026-10-11 (values invented, fields as seen).
TAVILY_BODY = {
    "query": "q",
    "answer": None,
    "images": [],
    "follow_up_questions": None,
    "results": [
        {
            "title": "Release notes",
            "url": "https://a.example/notes",
            "content": "## Release notes\nThe first chunk. [...] The second chunk.",
            "score": 0.9,
            "published_date": "Mon, 05 Oct 2026 02:00:00 GMT",
            "raw_content": None,
            "id": "1",
        },
        {"title": "No date", "url": "https://b.example/", "content": "Plain text.", "score": 0.5},
        {"title": "No url", "content": "dropped"},
    ],
}


def test_tavily_answers_become_the_same_results_exa_gives(monkeypatch):
    sent = {}

    def post(url, headers, json, timeout):
        sent.update(url=url, auth=headers["Authorization"], body=json)
        return _Resp(200, TAVILY_BODY)

    monkeypatch.setattr("httpx.post", post)
    options = SearchOptions(allowed_domains=("a.example",), recency="week", depth="deep")
    results = TavilyProvider(TAVILY_KEY).search("q", 5, options)

    assert sent["url"] == "https://api.tavily.com/search" and sent["auth"] == f"Bearer {TAVILY_KEY}"
    body = sent["body"]
    assert body["include_published_date"] is True and body["search_depth"] == "advanced"
    assert body["include_domains"] == ["a.example"] and "start_date" in body
    assert [r.url for r in results] == ["https://a.example/notes", "https://b.example/"]
    first = results[0]
    assert first.published_at == "2026-10-05T02:00:00+00:00"  # RFC 2822 in, ISO out
    assert first.snippet == "The first chunk. … The second chunk."  # heading = title, chunks = passages
    assert results[1].published_at is None


def test_a_refused_key_raises_so_the_chain_falls_through(monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp(401))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        TavilyProvider(TAVILY_KEY).search("q")


def test_tavily_runs_only_on_its_own_key(store):
    store.put("web_search:default", {"provider": "tavily", "api_key": EXA_KEY})
    # Chosen with no Tavily key: the Exa chain on Exa's key.
    p = resolve_provider(store)
    assert isinstance(p.primary, ExaProvider) and p.primary.api_key == EXA_KEY
    assert search_engine(store) == "exa" and provider_name(store) == "exa → duckduckgo"
    # With its key: Tavily first, DuckDuckGo behind it, on Tavily's key and never Exa's.
    store.put("web_search:default", {"provider": "tavily", "api_key": EXA_KEY, "tavily_api_key": TAVILY_KEY})
    p = resolve_provider(store)
    assert isinstance(p, FallbackProvider) and isinstance(p.fallback, DuckDuckGoProvider)
    assert isinstance(p.primary, TavilyProvider) and p.primary.api_key == TAVILY_KEY
    assert search_engine(store) == "tavily" and provider_name(store) == "tavily → duckduckgo"


def test_tavily_is_an_engine_and_no_longer_a_connector():
    assert get_descriptor("tavily") is None


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REEVE_HOME", str(tmp_path))
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    return SessionManager(workspace=str(tmp_path), data_dir=tmp_path / "data")


def _usage(status: int, seen: list):
    def get(url, headers, timeout):
        seen.append((url, headers["Authorization"]))
        return _Resp(status)

    return get


def test_a_tavily_key_is_saved_here_and_checked_when_saved(manager, monkeypatch):
    seen: list = []
    refused = manager.set_web_search("tavily")
    assert refused["ok"] is False and "Tavily API key" in refused["error"]

    monkeypatch.setattr("httpx.get", _usage(401, seen))
    bad = manager.set_web_search("tavily", tavily_key="tvly-wrong-000000")
    assert bad["ok"] is False and bad["error"] == "Tavily did not accept this key."
    assert manager.get_web_search()["tavily"]["has_key"] is False  # a refused key is not kept

    monkeypatch.setattr("httpx.get", _usage(200, seen))
    assert manager.set_web_search("tavily", tavily_key=TAVILY_KEY)["ok"]
    assert seen[-1] == ("https://api.tavily.com/usage", f"Bearer {TAVILY_KEY}")
    got = manager.get_web_search()
    assert got["mode"] == "tavily" and got["engine"] == "tavily"
    assert got["tavily"]["has_key"] is True and got["tavily"]["key_source"] == "settings"
    assert TAVILY_KEY not in json.dumps(got)


def test_switching_engines_keeps_both_keys_and_removing_tavilys_returns_to_exa(manager, monkeypatch):
    monkeypatch.setattr("httpx.get", _usage(200, []))
    assert manager.set_web_search("auto", EXA_KEY)["ok"]
    assert manager.set_web_search("tavily", tavily_key=TAVILY_KEY)["ok"]
    profile = manager.secrets.get("web_search:default")
    assert profile["api_key"] == EXA_KEY and profile["tavily_api_key"] == TAVILY_KEY

    assert manager.set_web_search("auto")["ok"]  # back to Exa: Tavily's key stays for later
    assert manager.get_web_search()["tavily"]["has_key"] is True
    assert manager.set_web_search("tavily")["ok"]  # and choosing it again needs no paste

    assert manager.set_web_search("tavily", remove_tavily_key=True)["ok"]
    got = manager.get_web_search()
    assert got["mode"] == "auto" and got["engine"] == "exa" and got["tavily"]["has_key"] is False
    assert manager.secrets.get("web_search:default")["api_key"] == EXA_KEY


def test_a_tavily_error_never_carries_its_key(manager, monkeypatch):
    monkeypatch.setattr("httpx.get", _usage(200, []))
    manager.set_web_search("tavily", tavily_key=TAVILY_KEY)
    assert "•••" in manager._without_key(f"tavily said no to {TAVILY_KEY}")


def _review():
    repo = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("harness_review", repo / "scripts" / "harness_review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_the_review_counts_a_session_that_searched_through_both_tools(tmp_path):
    review = _review()
    t = datetime(2026, 10, 11, tzinfo=timezone.utc).timestamp()

    def log(name, tools):
        path = tmp_path / f"{name}.events.jsonl"
        rows = [{"seq": i, "t": t, "type": "tool/result", "name": tool} for i, tool in enumerate(tools)]
        path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        return path

    logs = [
        log("both", ["web_search", "mcp__tavily__tavily_search"]),
        log("one", ["web_search", "web_search"]),
        log("other", ["mcp__firecrawl__firecrawl_scrape", "read_file"]),
    ]
    counts, refs = review.review(logs)
    assert counts["split-search"] == 1 and counts["searching sessions"] == 2
    assert refs["split-search"][0][0] == "both.events.jsonl"
