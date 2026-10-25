"""What a person (and the model) reads of a `web_search` result (split from `tool.py`, size ceiling).

The card, the call summary, the text rendering the model reads, and the tool's contract. Pure
functions over the result dict `tool.py` builds.
"""

from __future__ import annotations

import json
from typing import Any

from harness.loop.sources import READ_MARK
from harness.tools import cards
from harness.tools.contract import NETWORK_CAPS, ToolContract, ToolPresentation, clip
from .providers import EXTERNAL_CONTENT_NOTICE
from .snippets import clean_snippet

#: What a result's text is: the page its engine read (`READ_MARK`, which `loop/sources.py` reads to
#: tell a source the session read from one it only found), or a snippet.
SNIPPET_MARK = "Search snippet, page not read:"
#: The page text one search may give the model in all, highest-ranked result first. Five results
#: of Exa's 2,000-5,200 characters would be ~20k; past this a result is given as its snippet.
PAGES_BUDGET = 12_000
#: Less page than this is not worth calling read: the result is given as its snippet.
_MIN_PAGE = 600

# -- what a person sees (tools/contract.py) -------------------------------------
# Results are already `{title, url, snippet}` objects, so the card is a projection rather
# than a parse. The CALL card stays generic, carrying only the query: there is nothing
# structured to show before the search runs, which is the rule for search tools.


def _search_present_call(arguments: dict[str, Any]) -> ToolPresentation:
    raw = arguments.get("queries")
    qs = [str(q).strip() for q in raw if str(q).strip()] if isinstance(raw, list) else []
    if len(qs) > 1:
        summary = f"Search the web ({len(qs)} queries)"
        detail = clip(" · ".join(qs))
    else:
        query = clip(qs[0] if qs else arguments.get("query"), 160)
        summary = f"Search the web for “{query}”" if query else "Search the web"
        detail = clip(query)
    return ToolPresentation(
        summary=summary,
        detail=detail,
        kind="search",
        card=cards.generic_call(summary, kind="search", raw_input=detail or None),
    )


def _search_present_result(
    arguments: dict[str, Any], value: Any, card_meta: Any = None
) -> ToolPresentation:
    if not isinstance(value, dict):
        return ToolPresentation(summary="Web search finished", kind="search")
    error = str(value.get("error") or "").strip()
    if error:
        return ToolPresentation(summary=clip(error), kind="search")
    found = value.get("results")
    rows = found if isinstance(found, list) else []
    sources: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        if not isinstance(url, str) or not url:
            # A result with no URL is not a citation, and a citation list is what this
            # card IS — a row that cannot be followed would be a link that goes nowhere.
            continue
        source: dict[str, Any] = {"url": url}
        for key in ("title", "snippet", "published_at", "author"):
            text = row.get(key)
            if isinstance(text, str) and text:
                source[key] = text
        if row.get("icon") is True:
            source["icon"] = True  # the site's mark is kept locally (`favicons.py`)
        sources.append(source)
    if not sources:
        # "No results" is an answer, and the card says so rather than rendering empty.
        return ToolPresentation(
            summary=f"No web results for “{clip(arguments.get('query'), 60)}”",
            kind="search",
        )
    provider = value.get("provider")
    detail = f"{len(sources)} result{'' if len(sources) == 1 else 's'}"
    if isinstance(provider, str) and provider:
        detail += f" via {provider}"
    query = arguments.get("query")
    raw_queries = value.get("queries")
    queries = [str(q) for q in raw_queries if str(q).strip()] if isinstance(raw_queries, list) else []
    removed = value.get("filtered_out")
    failed = value.get("errors")
    return ToolPresentation(
        summary=f"Found {len(sources)} web results",
        detail=detail,
        kind="search",
        card=cards.web_search(
            sources,
            provider=provider if isinstance(provider, str) and provider else None,
            query=query if isinstance(query, str) and query else None,
            # What the reader cannot see in a list of links: that the service they expect
            # did not answer, which searches were merged, and what the filters took away.
            fallback_from=str(value.get("fallback_from") or "") or None,
            fallback_reason=str(value.get("fallback_reason") or "") or None,
            queries=queries if len(queries) > 1 else None,
            filtered_out=removed if isinstance(removed, int) and removed > 0 else None,
            failed_queries=len(failed) if isinstance(failed, list) and failed else None,
        ),
    )


def _search_facts(arguments: dict[str, Any], value: Any) -> dict[str, Any]:
    """What the log records about a search, as data: which service answered,
    whether the chain fell through and from what, and how many results came back. The rendered
    text says the same to the model; this is what a count reads."""
    if not isinstance(value, dict):
        return {}
    found = value.get("results")
    facts: dict[str, Any] = {
        "provider": str(value.get("provider") or "") or None,
        "results": len(found) if isinstance(found, list) else 0,
        "failed": bool(value.get("error")),
    }
    if isinstance(found, list):
        # How many results the model is given as read pages (`_web_search_render`).
        facts["pages"] = len(_read_rows(found))
    if value.get("fallback_from"):
        facts["fallback_from"] = str(value["fallback_from"])
        facts["fallback_reason"] = str(value.get("fallback_reason") or "")
    if isinstance(value.get("filtered_out"), int):
        facts["filtered_out"] = value["filtered_out"]
    return facts


#: Every search result is external content: every result is prefixed with the same kind of
#: notice, and it is the one line that tells the model a fetched page is data, not a brief.
_TRUST_NOTICE = EXTERNAL_CONTENT_NOTICE


def _web_search_render(arguments: dict[str, Any], value: Any) -> str:
    """Numbered results as text, replacing `{"provider", "results": [{title,url,snippet}]}`,
    which repeats three key names per result. Dates ride inline after the URL."""
    if not isinstance(value, dict):
        return json.dumps(value, default=str)
    error = str(value.get("error") or "").strip()
    if error:
        provider = str(value.get("provider") or "")
        note = f" (provider: {provider})" if provider else ""
        if value.get("fallback_from"):
            note += f" [{value.get('fallback_from')}: {value.get('fallback_reason')}]"
        return f"Error: {error}{note}"
    results = value.get("results")
    if not isinstance(results, list) or not results:
        gone = value.get("filtered_out")
        if isinstance(gone, int) and gone:
            return (
                f"No results: {gone} found, and all were removed by your allowed_domains / "
                "blocked_domains / recency. Loosen one and search again."
            )
        return "No results"
    rows = []
    pages = _read_rows(results)
    for index, result in enumerate(results, 1):
        if not isinstance(result, dict):
            continue
        head = f"{index}. {result.get('title')}\n   {result.get('url')}"
        published = result.get("published_at")
        if isinstance(published, str) and published:
            head += f" ({published})"
        page = pages.get(index - 1)
        if page:
            rows.append(f"{head}\n   {READ_MARK}\n" + _indent(page))
        else:
            rows.append(f"{head}\n   {SNIPPET_MARK} {result.get('snippet')}")
    body = "\n".join(rows)
    notes: list[str] = []
    queries = value.get("queries")
    if isinstance(queries, list) and len(queries) > 1:
        notes.append(f"{len(queries)} queries merged")
    if value.get("errors"):
        notes.append("failed queries: " + "; ".join(str(e) for e in value["errors"]))
    if isinstance(value.get("filtered_out"), int) and value["filtered_out"]:
        notes.append(f"{value['filtered_out']} more removed by your domain / recency filters")
    if value.get("fallback_from"):
        notes.append(
            f"{value['fallback_from']} unavailable ({value.get('fallback_reason')}) — "
            f"answered by {value.get('provider')}"
        )
    if notes:
        body += "\n\n[" + " · ".join(notes) + "]"
    return f"{_TRUST_NOTICE}\n\n{body}"


def _read_rows(results: list[Any]) -> dict[int, str]:
    """`{position: page text}` for the results the model is given as read pages: those whose engine
    read the page (`page`), highest-ranked first, while `PAGES_BUDGET` lasts. The rest are given as
    their snippet, and a source cited from one has not been read (`loop/sources.py`)."""
    left = PAGES_BUDGET
    out: dict[int, str] = {}
    for position, result in enumerate(results):
        page = result.get("page") if isinstance(result, dict) else None
        if not isinstance(page, str) or len(page.strip()) < _MIN_PAGE or left < _MIN_PAGE:
            continue
        text = page if len(page) <= left else clean_snippet(page, limit=left)
        out[position] = text
        left -= len(text)
    return out


def _indent(text: str) -> str:
    return "\n".join(f"   {line}" if line.strip() else "" for line in text.splitlines())


#: The search goes straight through the provider chain: Exa's 15 s (`_EXA_TIMEOUT`) plus
#: DuckDuckGo's 20 s is the worst case below this deadline, so the tool's 60 s holds even
#: when the primary hangs and the fallback answers.
WEB_SEARCH_CONTRACT = ToolContract(
    capabilities=NETWORK_CAPS,
    render=_web_search_render,
    present_call=_search_present_call,
    present_result=_search_present_result,
    facts=_search_facts,
    timeout_ms=60_000,
)
