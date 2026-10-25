"""Reading the web: `web_search` (Exa then DuckDuckGo, or Tavily then DuckDuckGo) and `web_fetch`,
which reads a page as Markdown."""

from __future__ import annotations

from .ask import PageAsker
from .fetch import make_web_fetch_tool
from .providers import (
    EXTERNAL_CONTENT_NOTICE,
    DuckDuckGoProvider,
    ExaProvider,
    FallbackProvider,
    SearchResult,
    WebSearchProvider,
    build_provider,
    default_provider,
    parse_exa_mcp_text,
    provider_names,
)
from .tavily import TavilyProvider
from .tool import make_web_search_tool, provider_name, resolve_provider, search_engine, tavily_key

__all__ = [
    "DuckDuckGoProvider",
    "EXTERNAL_CONTENT_NOTICE",
    "ExaProvider",
    "FallbackProvider",
    "PageAsker",
    "SearchResult",
    "TavilyProvider",
    "WebSearchProvider",
    "build_provider",
    "default_provider",
    "make_web_fetch_tool",
    "make_web_search_tool",
    "parse_exa_mcp_text",
    "provider_name",
    "provider_names",
    "resolve_provider",
    "search_engine",
    "tavily_key",
]
