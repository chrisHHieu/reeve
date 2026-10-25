"""`web_search` can be narrowed by domain, recency and depth.

What has to hold:

  - the arguments mean what they say even where the provider cannot apply them: every result is
    checked after it comes back, and the provider is asked for more than will be shown when a filter
    will remove some;
  - a domain filter matches the domain and its subdomains, never a look-alike (`evilpython.org`);
  - an undated result is kept by a recency filter: unknown is not old;
  - a provider written before options existed still works; a search that nobody narrowed is called
    exactly as it always was;
  - "everything was removed by your filter" is an answer, not a failed search;
  - what each provider can do natively is sent to it (Exa's REST fields, DuckDuckGo's operators).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from harness.integrations.web import providers as providers_mod
from harness.integrations.web.providers import DuckDuckGoProvider, ExaProvider, FallbackProvider, SearchResult, WebSearchProvider
from harness.integrations.web.search_filters import SearchOptions, apply, ddg_query, ddg_timelimit, host_matches, normalise_domains, provider_count
from harness.integrations.web.tool import _SCHEMA, _web_search_render, make_web_search_tool

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _row(url, published=None):
    return SearchResult(title=url, url=url, snippet="s", published_at=published)


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# -- domains ------------------------------------------------------------------------------------


def test_a_domain_is_normalised_from_whatever_the_model_wrote():
    assert normalise_domains(["https://www.Docs.Python.org/3/", "python.org", "PYTHON.ORG", "", 5, "a.com:8080/x"]) == (
        "docs.python.org", "python.org", "a.com",
    )
    assert normalise_domains("python.org") == () and normalise_domains(None) == ()
    assert len(normalise_domains([f"d{i}.com" for i in range(50)])) == 20


def test_a_domain_matches_itself_and_its_subdomains_and_nothing_that_merely_ends_like_it():
    assert host_matches("https://docs.python.org/3", ["python.org"])
    assert host_matches("https://python.org/", ["python.org"])
    assert host_matches("https://www.python.org/", ["python.org"])
    assert not host_matches("https://evilpython.org/", ["python.org"])
    assert not host_matches("https://python.org.evil.net/", ["python.org"])
    assert not host_matches("not a url", ["python.org"])


def test_allowed_and_blocked_domains_filter_the_results():
    rows = [_row("https://docs.python.org/a"), _row("https://pypi.org/b"), _row("https://blog.example.com/c")]
    kept, removed = apply(rows, SearchOptions(allowed_domains=("python.org", "pypi.org")))
    assert [r.url for r in kept] == ["https://docs.python.org/a", "https://pypi.org/b"] and removed == 1
    kept, removed = apply(rows, SearchOptions(blocked_domains=("pypi.org",)))
    assert [r.url for r in kept] == ["https://docs.python.org/a", "https://blog.example.com/c"] and removed == 1


# -- recency ------------------------------------------------------------------------------------


def test_recency_drops_what_is_known_to_be_old_and_keeps_what_has_no_date():
    rows = [_row("https://a.com/new", _iso(3)), _row("https://a.com/old", _iso(400)), _row("https://a.com/undated"), _row("https://a.com/odd", "last Tuesday")]
    kept, removed = apply(rows, SearchOptions(recency="month"), now=NOW)
    assert [r.url for r in kept] == ["https://a.com/new", "https://a.com/undated", "https://a.com/odd"] and removed == 1


def test_the_windows():
    options = {name: SearchOptions.from_arguments(recency=name) for name in ("day", "week", "month", "year")}
    assert all(o.cutoff(NOW) is not None for o in options.values())
    assert (NOW - options["week"].cutoff(NOW)).days == 7
    assert SearchOptions.from_arguments(recency="decade").recency == ""  # not a window: ignored, not an error


def test_nothing_narrowed_means_nothing_removed_and_the_provider_is_asked_for_what_is_shown():
    plain = SearchOptions.from_arguments(depth="deep")
    assert not plain.narrows and plain.active and apply([_row("https://a.com/")], plain) == ([_row("https://a.com/")], 0)
    assert provider_count(5, plain) == 5
    narrowed = SearchOptions(allowed_domains=("a.com",))
    assert provider_count(5, narrowed) == 15 and provider_count(10, narrowed) == 30  # most of it is removed again


# -- the tool ------------------------------------------------------------------------------------


class _Ignores(WebSearchProvider):
    """A provider that cannot filter: it returns the same mix whatever it is asked."""

    name = "ignores"

    def __init__(self, rows):
        self.rows, self.asked = rows, []

    def search(self, query, max_results=5, options=None):
        self.asked.append((max_results, options))
        return list(self.rows)


class _Legacy(WebSearchProvider):
    """Written before options existed: its `search` takes no `options`."""

    name = "legacy"

    def __init__(self, rows):
        self.rows, self.asked = rows, []

    def search(self, query, max_results=5):
        self.asked.append(max_results)
        return list(self.rows)


MIXED = [_row("https://docs.python.org/a"), _row("https://spam.example.com/b"), _row("https://pypi.org/c")]


def test_the_filters_hold_even_when_the_provider_ignores_them():
    provider = _Ignores(MIXED)
    out = make_web_search_tool(provider=provider)(query="q", allowed_domains=["python.org"], max_results=5)
    assert [r["url"] for r in out["results"]] == ["https://docs.python.org/a"]
    assert out["filtered_out"] == 2
    assert provider.asked[0][0] == 15 and provider.asked[0][1].allowed_domains == ("python.org",)  # asked for more than shown


def test_a_legacy_provider_is_asked_plainly_and_still_filtered():
    provider = _Legacy(MIXED)
    out = make_web_search_tool(provider=provider)(query="q", blocked_domains=["spam.example.com"])
    assert [r["url"] for r in out["results"]] == ["https://docs.python.org/a", "https://pypi.org/c"]


def test_an_unnarrowed_search_is_called_exactly_as_before():
    provider = _Legacy(MIXED)
    out = make_web_search_tool(provider=provider)(query="q", max_results=3)
    assert provider.asked == [3] and "filtered_out" not in out and len(out["results"]) == 3


def test_when_the_filter_removes_everything_that_is_the_answer():
    out = make_web_search_tool(provider=_Ignores(MIXED))(query="q", allowed_domains=["nowhere.example"])
    assert "error" not in out and out["results"] == [] and out["filtered_out"] == 3
    text = _web_search_render({}, out)
    assert "No results: 3 found, and all were removed by your allowed_domains" in text and "Loosen one" in text


def test_the_render_counts_what_the_filters_removed():
    out = make_web_search_tool(provider=_Ignores(MIXED))(query="q", blocked_domains=["spam.example.com"])
    assert "1 more removed by your domain / recency filters" in _web_search_render({}, out)


def test_a_provider_that_returned_nothing_is_still_a_failed_search():
    out = make_web_search_tool(provider=_Ignores([]))(query="q", allowed_domains=["python.org"])
    assert "error" in out


def test_the_arguments_are_in_the_schema_with_the_date_and_sources_guidance():
    function = _SCHEMA["function"]
    properties = function["parameters"]["properties"]
    for name in ("allowed_domains", "blocked_domains", "recency", "depth"):
        assert name in properties
    assert properties["recency"]["enum"] == ["day", "week", "month", "year"]
    assert properties["depth"]["enum"] == ["fast", "auto", "deep"]
    text = function["description"]
    assert "Today's date is in the Environment block" in text and "never an earlier one" in text
    assert '"Sources:" list' in text


# -- what each provider does with them ---------------------------------------------------------------


def test_duckduckgo_gets_operators_and_a_time_limit():
    options = SearchOptions(allowed_domains=("python.org", "pypi.org"), blocked_domains=("spam.com",), recency="week")
    assert ddg_query("retry", options) == "retry (site:python.org OR site:pypi.org) -site:spam.com"
    assert ddg_query("retry", SearchOptions(allowed_domains=("python.org",))) == "retry site:python.org"
    assert ddg_query("retry", SearchOptions()) == "retry"
    assert ddg_timelimit(options) == "w" and ddg_timelimit(SearchOptions()) is None


def test_duckduckgo_search_passes_them_to_the_library(monkeypatch):
    seen = {}

    class FakeDDGS:
        def text(self, query, max_results=5, timelimit=None):
            seen.update(query=query, max_results=max_results, timelimit=timelimit)
            return [{"title": "t", "href": "https://python.org/", "body": "b"}]

    import sys, types

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    DuckDuckGoProvider().search("retry", 7, SearchOptions(allowed_domains=("python.org",), recency="month"))
    assert seen == {"query": "retry site:python.org", "max_results": 7, "timelimit": "m"}
    DuckDuckGoProvider().search("retry", 7)
    assert seen["timelimit"] is None and seen["query"] == "retry"


def test_exa_rest_gets_domains_a_start_date_and_a_depth(monkeypatch):
    sent = {}

    class Reply:
        status_code = 200

        def json(self):
            return {"results": [{"title": "t", "url": "https://python.org/", "highlights": ["h"], "publishedDate": _iso(1)}]}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.update(kw["json"]) or Reply())
    ExaProvider("key").search("q", 9, SearchOptions(allowed_domains=("python.org",), blocked_domains=("x.com",), recency="week", depth="deep"))
    assert sent["includeDomains"] == ["python.org"] and sent["excludeDomains"] == ["x.com"]
    assert sent["type"] == "deep" and sent["startPublishedDate"].endswith("Z") and sent["numResults"] == 9
    sent.clear()
    ExaProvider("key").search("q", 5)
    assert sent["type"] == "auto" and "includeDomains" not in sent and "startPublishedDate" not in sent


def test_exa_mcp_gets_only_what_its_schema_takes_and_leaves_filtering_to_the_tool(monkeypatch):
    sent = {}

    class Reply:
        status_code = 200
        text = 'data: {"result": {"content": [{"type": "text", "text": "Title: T\\nURL: https://python.org/\\nHighlights:\\nh"}]}}'

    import httpx

    monkeypatch.setattr(httpx, "post", lambda url, **kw: sent.update(kw["json"]["params"]["arguments"]) or Reply())
    ExaProvider().search("q", 5, SearchOptions(depth="fast", allowed_domains=("python.org",)))
    # The public tool's schema (read 2026-10-19) has no `type`, `livecrawl` or domain filter.
    assert sent == {"query": "q", "numResults": 5}


def test_the_chain_passes_options_on_only_when_there_are_some():
    class Spy(WebSearchProvider):
        name = "spy"

        def __init__(self, fail=False):
            self.calls, self.fail = [], fail

        def search(self, query, max_results=5, options=None):
            self.calls.append(options)
            if self.fail:
                raise RuntimeError("down")
            return [_row("https://a.com/")]

    # Nothing narrowed: a provider that takes no `options` is called exactly as it always was.
    legacy = FallbackProvider(_Legacy([]), _Legacy([_row("https://a.com/")]))
    assert [r.url for r in legacy.search("q", 5)] == ["https://a.com/"]

    primary, fallback = Spy(fail=True), Spy()
    chain = FallbackProvider(primary, fallback)
    options = SearchOptions(recency="day")
    chain.search("q", 5, options)
    assert primary.calls[-1] is options and fallback.calls[-1] is options and chain.fell_back
