"""Tavily as a `web_search` engine: its REST API, with the key saved in Settings.

Not its MCP server: the hosted `tavily_search` takes no option for publication dates, and the
REST answer carries one on each result (measured 2026-10-11: 5 of 5, where Exa had 3 of 5).
"""

from __future__ import annotations

from typing import Any, Optional

from .providers import _EXA_SNIPPET_CHARS, PAGE_CHARS, SearchResult, WebSearchProvider
from .search_filters import SearchOptions
from .snippets import clean_snippet

_TAVILY_API = "https://api.tavily.com/search"
_TAVILY_TIMEOUT = 15.0


def _iso_date(value: Any) -> Optional[str]:
    """A provider's date as ISO 8601, which the recency filter and the cards read. Tavily
    reports RFC 2822 (`Mon, 05 Oct 2026 02:00:00 GMT`); an ISO date passes through."""
    text = str(value or "").strip()
    if not text:
        return None
    from datetime import datetime
    from email.utils import parsedate_to_datetime

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).isoformat()
    except (TypeError, ValueError):
        return None


def check_key(api_key: str) -> Optional[str]:
    """Why Tavily refuses `api_key`, or None when it accepts it (or cannot be asked).

    The usage endpoint answers 401 for a bad key and costs no credit, so a key is checked when it
    is saved rather than on the first search, where a refusal only shows as a fallback.
    """
    import httpx

    try:
        resp = httpx.get(
            "https://api.tavily.com/usage",
            headers={"Authorization": f"Bearer {str(api_key or '').strip()}"},
            timeout=10.0,
        )
    except httpx.HTTPError:
        return None  # offline is not a refusal: the key is kept, and a test search will say
    if resp.status_code in (401, 403):
        return "Tavily did not accept this key."
    return None


class TavilyProvider(WebSearchProvider):
    """Tavily over its REST API, with the key saved in Settings ▸ Web search.

    Measured 2026-10-11 against Exa on one query: as relevant, a date on 5 of 5 results (Exa
    3 of 5), longer excerpts that carry more page furniture. A result's `content` is up to
    three chunks joined by ` [...] `; they become passages for `clean_snippet`. Raises on any
    failure so the chain falls through to DuckDuckGo.
    """

    name = "tavily"

    def __init__(self, api_key: str) -> None:
        self.api_key = str(api_key or "").strip()

    def search(
        self, query: str, max_results: int = 5, options: Optional[SearchOptions] = None
    ) -> list[SearchResult]:
        import httpx

        options = options or SearchOptions()
        depth = {"deep": "advanced", "fast": "fast"}.get(options.depth or "", "basic")
        body: dict[str, Any] = {
            "query": query,
            "max_results": max(1, min(int(max_results), 20)),
            "search_depth": depth,
            "include_published_date": True,
            "include_favicon": True,
        }
        if options.allowed_domains:
            body["include_domains"] = list(options.allowed_domains)
        if options.blocked_domains:
            body["exclude_domains"] = list(options.blocked_domains)
        cutoff = options.cutoff()
        if cutoff is not None:
            body["start_date"] = cutoff.strftime("%Y-%m-%d")
        resp = httpx.post(
            _TAVILY_API,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=_TAVILY_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"tavily api returned HTTP {resp.status_code}")
        rows = (resp.json() or {}).get("results") or []
        results: list[SearchResult] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("url"):
                continue
            name = str(row.get("title") or "")
            content = str(row.get("content") or "").replace(" [...] ", "\n...\n")
            results.append(
                SearchResult(
                    title=name,
                    url=str(row["url"]),
                    snippet=clean_snippet(content, title=name, limit=_EXA_SNIPPET_CHARS),
                    published_at=_iso_date(row.get("published_date")),
                    favicon=str(row.get("favicon") or "") or None,
                    page=clean_snippet(content, title=name, limit=PAGE_CHARS) if content.strip() else "",
                )
            )
        return results
