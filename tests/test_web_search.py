"""Web search (`integrations/web`): the tool, the chain of providers behind it, and which one the
settings resolve to.

Nothing is reached: providers are fakes. The Exa MCP parser runs on output captured from the real
public endpoint (2026-09-18), and the chain falls back without a socket being opened.
"""

from __future__ import annotations

from harness.integrations.web import SearchResult, build_provider, make_web_search_tool, parse_exa_mcp_text, provider_names, resolve_provider
from harness.integrations.web.providers import DuckDuckGoProvider, ExaProvider, FallbackProvider, WebSearchProvider
from harness.safety.secrets import SecretStore


class FakeProvider(WebSearchProvider):
    """Gives back as many made-up results as were asked for, and keeps each query."""

    name = "fake"

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def search(self, query, max_results=5):
        self.calls.append((query, max_results))
        return [SearchResult(title=f"r{n}", url=f"https://x/{n}", snippet="s") for n in range(max_results)]


def test_a_search_gives_back_its_results_and_who_gave_them():
    fake = FakeProvider()
    search = make_web_search_tool(provider=fake)
    found = search(query="anthropic", max_results=3)
    assert found["provider"] == "fake" and [r["title"] for r in found["results"]] == ["r0", "r1", "r2"]
    assert fake.calls == [("anthropic", 3)]
    assert search.__aisuite_tool_metadata__.category == "web"  # what the registry needs
    assert search.__tool_schema__["function"]["name"] == "web_search"


def test_the_number_of_results_asked_for_is_kept_between_one_and_ten():
    fake = FakeProvider()
    for asked in (99, 0):
        make_web_search_tool(provider=fake)(query="q", max_results=asked)
    assert [count for _, count in fake.calls] == [10, 1]  # between 1 and 10


def test_a_failing_provider_comes_back_as_an_error_that_names_it():
    class Down(WebSearchProvider):
        name = "boom"

        def search(self, query, max_results=5):
            raise RuntimeError("network down")

    failed = make_web_search_tool(provider=Down())(query="q")
    assert "web search failed" in failed["error"] and failed["provider"] == "boom"


def test_a_provider_is_built_by_name_and_an_unusable_one_is_duckduckgo():
    assert set(provider_names()) == {"duckduckgo", "exa", "tavily"}
    assert isinstance(build_provider("duckduckgo"), DuckDuckGoProvider)
    exa = build_provider("exa", "exa-k")
    assert isinstance(exa, ExaProvider) and exa.api_key == "exa-k"
    for unusable in ("unknown-thing", "tavily"):  # a name it does not know, and Tavily with no key
        assert isinstance(build_provider(unusable), DuckDuckGoProvider)


# -- Exa: the MCP parser and the API shape ---------------------------------------

#: Captured verbatim from `mcp.exa.ai` (trimmed to two results) — the format the parser
#: must keep working against: blocks split on a `---` rule, `N/A` dates dropped.
EXA_SAMPLE = """Title: Coroutines and tasks
URL: https://docs.python.org/3/library/asyncio-task.html
Published: N/A
Author: N/A
Highlights:
The asyncio components
...
## Timeouts¶
...
asyncio.timeout(delay) limits waiting.

---

Title: The Right Way to Set Timeouts
URL: https://runebook.dev/en/docs/python
Published: 2026-03-02
Author: someone
Highlights:
wait_for vs asyncio.timeout
"""


def test_parse_exa_mcp_text_keeps_highlights_and_dates():
    results = parse_exa_mcp_text(EXA_SAMPLE, max_results=5)
    assert [r.title for r in results] == [
        "Coroutines and tasks",
        "The Right Way to Set Timeouts",
    ]
    assert results[0].url == "https://docs.python.org/3/library/asyncio-task.html"
    # The words of every passage, in order, without the page's markdown: the heading keeps
    # its text and loses `##` and its anchor, and the `...` breaks become one ` … `.
    assert results[0].snippet == (
        "The asyncio components … Timeouts … asyncio.timeout(delay) limits waiting."
    )
    assert results[0].published_at is None  # N/A is "no date", not a date
    assert results[0].author is None
    assert results[1].published_at == "2026-03-02"
    assert results[1].author == "someone"


def test_parse_exa_mcp_text_caps_results_and_ignores_noise():
    assert len(parse_exa_mcp_text(EXA_SAMPLE, max_results=1)) == 1
    # A rate-limit sentence (the free tier's 200 answer) parses to nothing — the chain
    # treats that as failure and falls through, never as "no results".
    assert parse_exa_mcp_text("You've hit Exa's free MCP rate limit.", max_results=5) == []


# -- the chain ---------------------------------------------------------------------

def test_chain_uses_the_primary_when_it_answers():
    primary, fallback = FakeProvider(), FakeProvider()
    primary.name, fallback.name = "exa", "duckduckgo"
    chain = FallbackProvider(primary, fallback)
    out = make_web_search_tool(provider=chain)(query="q", max_results=2)
    assert out["provider"] == "exa"
    assert "fell_back" not in out and "fallback_from" not in out
    assert fallback.calls == []


def test_chain_falls_back_on_a_primary_failure_and_names_it():
    class RateLimited(WebSearchProvider):
        name = "exa"

        def search(self, query, max_results=5):
            raise RuntimeError("exa mcp: rate limited")

    fallback = FakeProvider()
    fallback.name = "duckduckgo"
    chain = FallbackProvider(RateLimited(), fallback)
    out = make_web_search_tool(provider=chain)(query="q", max_results=2)
    assert out["provider"] == "duckduckgo"
    assert out["fallback_from"] == "exa"
    assert "rate limited" in out["fallback_reason"]
    assert len(out["results"]) == 2


def test_chain_falls_back_when_the_primary_is_empty():
    class Empty(WebSearchProvider):
        name = "exa"

        def search(self, query, max_results=5):
            return []

    fallback = FakeProvider()
    fallback.name = "duckduckgo"
    chain = FallbackProvider(Empty(), fallback)
    out = make_web_search_tool(provider=chain)(query="q", max_results=1)
    assert out["provider"] == "duckduckgo" and out["fallback_reason"] == "no results"


def test_chain_reports_both_failures():
    class Bad(WebSearchProvider):
        def __init__(self, name):
            self.name = name

        def search(self, query, max_results=5):
            raise RuntimeError(f"{self.name} down")

    chain = FallbackProvider(Bad("exa"), Bad("duckduckgo"))
    out = make_web_search_tool(provider=chain)(query="q")
    assert "duckduckgo down" in out["error"]
    assert out["provider"] == "duckduckgo" and out["fallback_from"] == "exa"


# -- multi-query -------------------------------------------------

def test_multi_query_merges_round_robin_and_dedupes():
    class TwoQueries(WebSearchProvider):
        name = "fake"

        def search(self, query, max_results=5):
            if query == "a":
                return [
                    SearchResult("A1", "https://x/1", "s"),
                    SearchResult("A2", "https://x/2", "s"),
                ]
            return [
                SearchResult("B1", "https://x/2", "s"),  # same URL as A2 → dropped
                SearchResult("B2", "https://x/3", "s"),
            ]

    out = make_web_search_tool(provider=TwoQueries())(queries=["a", "b"], max_results=5)
    # Best-of-each first: A1 (rank 0 of a), B1 dropped as a dupe, then A2, B2.
    assert [r["url"] for r in out["results"]] == [
        "https://x/1",
        "https://x/2",
        "https://x/3",
    ]
    assert out["queries"] == ["a", "b"]


def test_multi_query_caps_at_four_and_keeps_single_query_compat():
    fake = FakeProvider()
    make_web_search_tool(provider=fake)(queries=["a", "b", "c", "d", "e", "f"])
    assert len(fake.calls) == 4  # capped

    single = FakeProvider()
    out = make_web_search_tool(provider=single)(query="only")
    assert single.calls == [("only", 5)]
    assert "queries" not in out  # the single-query payload is unchanged


def test_multi_query_survives_one_failure():
    class Flaky(WebSearchProvider):
        name = "flaky"

        def search(self, query, max_results=5):
            if query == "bad":
                raise RuntimeError("boom")
            return [SearchResult("ok", "https://x/ok", "s")]

    out = make_web_search_tool(provider=Flaky())(queries=["good", "bad"], max_results=5)
    assert [r["url"] for r in out["results"]] == ["https://x/ok"]
    assert out["errors"] and "bad" in out["errors"][0]


def test_search_without_any_query_is_an_error():
    out = make_web_search_tool(provider=FakeProvider())()
    assert "needs a query" in out["error"]
    assert make_web_search_tool(provider=FakeProvider())(queries=[" ", ""])["error"]


def test_render_states_the_merge_and_the_failed_queries():
    from harness.integrations.web.tool import WEB_SEARCH_CONTRACT

    text = WEB_SEARCH_CONTRACT.render(
        {},
        {
            "provider": "exa",
            "queries": ["a", "b"],
            "errors": ["b: boom"],
            "results": [{"title": "T", "url": "https://x/1", "snippet": "S"}],
        },
    )
    assert "2 queries merged" in text and "failed queries: b: boom" in text


def test_render_carries_the_trust_notice_and_the_fallback_note():
    from harness.integrations.web.tool import WEB_SEARCH_CONTRACT

    text = WEB_SEARCH_CONTRACT.render(
        {"query": "q"},
        {
            "provider": "duckduckgo",
            "fallback_from": "exa",
            "fallback_reason": "rate limited",
            "results": [
                {"title": "T", "url": "https://x/1", "snippet": "S", "published_at": "2026-01-02"}
            ],
        },
    )
    assert text.startswith("External web content follows")
    assert "https://x/1 (2026-01-02)" in text
    assert "exa unavailable (rate limited) — answered by duckduckgo" in text


# -- resolution ----------------------------------------------------------------------

def _store(tmp_path) -> SecretStore:
    return SecretStore(tmp_path / "secrets.json")


def test_resolve_default_is_the_shipped_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    p = resolve_provider(_store(tmp_path))
    assert isinstance(p, FallbackProvider)
    assert isinstance(p.primary, ExaProvider) and p.primary.api_key is None
    assert isinstance(p.fallback, DuckDuckGoProvider)


def test_resolve_honours_the_env_key(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("EXA_API_KEY", "exa-env")
    p = resolve_provider(_store(tmp_path))
    assert isinstance(p.primary, ExaProvider) and p.primary.api_key == "exa-env"


def test_resolve_pins_duckduckgo(tmp_path, monkeypatch):
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    store = _store(tmp_path)
    store.put("web_search:default", {"provider": "duckduckgo"})
    assert isinstance(resolve_provider(store), DuckDuckGoProvider)


def test_a_removed_provider_name_falls_back_to_the_chain(tmp_path, monkeypatch):
    """A profile naming Tavily with no Tavily key must not break search: it runs the shipped
    chain instead."""
    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    store = _store(tmp_path)
    store.put("web_search:default", {"provider": "tavily", "api_key": "tvly-old"})
    p = resolve_provider(store)
    assert isinstance(p, FallbackProvider) and isinstance(p.primary, ExaProvider)


def test_the_name_shown_is_where_searches_go_now(tmp_path, monkeypatch):
    from harness.integrations.web.tool import provider_name

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path))
    store = _store(tmp_path)
    assert provider_name(store) == "exa → duckduckgo"
    store.put("web_search:default", {"provider": "duckduckgo"})
    assert provider_name(store) == "duckduckgo"


def test_the_settings_route_chooses_the_provider_and_never_shows_the_key(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from harness.interface.server.app import create_app
    from harness.interface.server.manager import SessionManager

    monkeypatch.setenv("REEVE_STATE_DIR", str(tmp_path / "state"))
    client = TestClient(create_app(SessionManager(data_dir=tmp_path / "data")))
    assert client.get("/v1/web-search").json()["provider"] == "exa → duckduckgo"
    assert client.post("/v1/web-search", json={"provider": "exa", "api_key": "sk-secret-xyz"}).json()["ok"] is True
    chosen = client.get("/v1/web-search")
    assert chosen.json()["provider"] == "exa" and chosen.json()["has_key"] is True
    assert "sk-secret-xyz" not in chosen.text  # the key is never given back
    assert client.post("/v1/web-search", json={"provider": "nope"}).json()["ok"] is False


def test_a_built_engine_has_web_search(tmp_path):
    from harness.compose import build_engine
    from harness.modes.agents import chat_agent

    engine = build_engine(agent=chat_agent(), provider=_Idle(), secrets=SecretStore(tmp_path / "s.json"))
    assert "web_search" in engine.registry.names()


class _Idle:
    """A model client that answers nothing, for an engine that is built and not run."""

    def complete(self, **_kw):
        from harness.llm.providers import AssistantTurn

        return AssistantTurn()

    def capabilities(self, _model):
        from harness.llm.providers.base import ModelCapabilities

        return ModelCapabilities()

    def stream(self, **_kw):
        from harness.llm.providers.base import StreamChunk

        yield StreamChunk(turn=self.complete())
