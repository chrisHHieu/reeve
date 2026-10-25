"""A search result's snippet, cleaned (`harness/integrations/web/snippets.py`).

The inputs here are shaped like what Exa's API returned on 2026-10-05 (several passages of a
page's own markdown joined by `...` lines, the title repeated as the first line, a table, link
syntax), with invented text. What must hold: the words survive in order, the furniture goes,
and a cut says it is a cut.
"""

from __future__ import annotations

from harness.integrations.web.providers import ExaProvider, SearchOptions
from harness.integrations.web.search_present import WEB_SEARCH_CONTRACT, _search_present_result
from harness.integrations.web.snippets import clean_snippet
from harness.tools.contract import safe_facts

PAGE = """Widget Guide | Example Docs

## Widget Guide

**Widgets** shorten long [runs](https://example.test/runs) of work.
![diagram](https://example.test/d.png)
...
| Step | Owner | Purpose |
| --- | --- | --- |
| Decide | `Hook` | Watches the budget |
...
- First point
- Second point
---
Done."""


def test_the_words_survive_and_the_markdown_goes():
    out = clean_snippet(PAGE, title="Widget Guide | Example Docs")
    assert out == (
        "Widgets shorten long runs of work. … Step — Owner — Purpose. Decide — Hook — "
        "Watches the budget … First point. Second point. Done."
    )


def test_the_title_is_not_said_twice():
    # Exa opens a highlight with the page's heading; the row already shows the title.
    assert clean_snippet("Widget Guide\nBody text.", title="Widget Guide - Example") == "Body text."
    assert clean_snippet("# Widget Guide\nBody text.", title="Widget Guide") == "Body text."
    # A line that merely starts the same way is content.
    assert clean_snippet("Widget Guides compared.", title="Widget Guide") == "Widget Guides compared."


def test_a_cut_lands_on_a_sentence_and_says_so():
    text = "First sentence is here. Second sentence is a little longer than the first. Third."
    out = clean_snippet(text, limit=40)
    assert out == "First sentence is here. …"
    # A sentence end that would throw away more than half the room is not worth it: a word.
    out = clean_snippet(text, limit=60)
    assert out == "First sentence is here. Second sentence is a little longer…"
    # No sentence in reach: a word, never half of one.
    out = clean_snippet("alpha beta gamma delta epsilon zeta eta theta iota kappa", limit=30)
    assert out.endswith("…") and len(out) <= 30 and " ".join(out[:-1].split()) in "alpha beta gamma delta epsilon zeta"
    assert clean_snippet("short", limit=30) == "short"
    assert clean_snippet("", limit=30) == ""


def test_the_exa_api_path_keeps_the_byline_and_cleans_the_snippet(monkeypatch):
    body = {
        "results": [
            {
                "title": "Widget Guide | Example Docs",
                "url": "https://example.test/widgets",
                "publishedDate": "2026-10-04T10:00:00.000Z",
                "author": "A. Writer",
                "highlights": [PAGE],
            },
            {"title": "No byline", "url": "https://example.test/b", "text": "## Plain\nText only."},
        ]
    }

    class _Resp:
        status_code = 200

        def json(self):
            return body

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    rows = ExaProvider("k")._search_api("widgets", 5, SearchOptions())
    assert rows[0].author == "A. Writer"
    assert rows[0].to_dict()["author"] == "A. Writer"
    assert rows[0].snippet.startswith("Widgets shorten long runs of work.")
    assert "|" not in rows[0].snippet and "##" not in rows[0].snippet
    assert rows[1].author is None and "author" not in rows[1].to_dict()
    assert rows[1].snippet == "Plain. Text only."


def test_the_card_says_what_a_list_of_links_cannot():
    value = {
        "provider": "duckduckgo",
        "results": [{"title": "T", "url": "https://example.test/", "snippet": "s", "author": "B"}],
        "fallback_from": "exa",
        "fallback_reason": "exa mcp: rate limited",
        "queries": ["one", "two"],
        "filtered_out": 2,
        "errors": ["two: timed out"],
    }
    card = _search_present_result({"queries": ["one", "two"]}, value).card
    assert card["provider"] == "duckduckgo"
    assert card["fallback_from"] == "exa" and card["fallback_reason"] == "exa mcp: rate limited"
    assert card["queries"] == ["one", "two"]
    assert card["filtered_out"] == 2 and card["failed_queries"] == 1
    assert card["sources"][0]["author"] == "B"
    # A search that went as planned carries none of it.
    plain = _search_present_result(
        {"query": "q"}, {"provider": "exa", "results": [{"title": "T", "url": "https://e.test/"}]}
    ).card
    assert not {"fallback_from", "queries", "filtered_out", "failed_queries"} & set(plain)


def test_the_log_records_which_service_answered():
    facts = safe_facts(
        WEB_SEARCH_CONTRACT,
        "web_search",
        {"query": "q"},
        {
            "provider": "duckduckgo",
            "results": [{"url": "https://e.test/"}],
            "fallback_from": "exa",
            "fallback_reason": "rate limited",
        },
    )
    assert facts == {
        "provider": "duckduckgo",
        "results": 1,
        "failed": False,
        "pages": 0,
        "fallback_from": "exa",
        "fallback_reason": "rate limited",
    }
    failed = safe_facts(
        WEB_SEARCH_CONTRACT, "web_search", {"query": "q"}, {"error": "web search failed", "provider": "exa"}
    )
    assert failed == {"provider": "exa", "results": 0, "failed": True}
