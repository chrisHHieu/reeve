"""What a `web_search` may be narrowed by, and how a result is judged against it.

`allowed_domains` / `blocked_domains` (a search tool and Anthropic's server tool take the
same pair), `recency` (how old a hit may be) and `depth` (how hard the provider should look,
a `type` field). Each provider applies what it can natively; **every result is checked here
afterwards** regardless, because a provider that cannot filter (Exa's public MCP, DuckDuckGo for
dates) would otherwise make the argument mean "if it feels like it". A hit whose publication date
the provider did not report is kept: unknown is not old.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Optional
from urllib.parse import urlsplit

if TYPE_CHECKING:  # providers import this module, so the dependency points one way at runtime
    from .providers import SearchResult

RECENCY_DAYS = {"day": 1, "week": 7, "month": 31, "year": 366}
DEPTHS = ("fast", "auto", "deep")
#: A domain filter that asks for more than this is not a filter, it is an index.
MAX_DOMAINS = 20
#: When a filter is on, ask the provider for more than will be shown: most of what it returns
#: for a narrow filter is removed again here.
OVERFETCH = 3
MAX_FETCH = 30


def _host(value: str) -> str:
    """A bare lower-case host from a domain or a URL, without a leading `www.`."""
    text = str(value or "").strip().lower()
    if "://" in text:
        text = urlsplit(text).hostname or ""
    text = text.split("/", 1)[0].split(":", 1)[0].strip(".")
    return text[4:] if text.startswith("www.") else text


def normalise_domains(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    seen: list[str] = []
    for value in values:
        host = _host(value) if isinstance(value, str) else ""
        if host and host not in seen:
            seen.append(host)
    return tuple(seen[:MAX_DOMAINS])


def host_matches(url: str, domains: Iterable[str]) -> bool:
    """Whether the URL's host is one of `domains` or a subdomain of one."""
    host = _host(url)
    return bool(host) and any(host == d or host.endswith("." + d) for d in domains)


@dataclass(frozen=True)
class SearchOptions:
    allowed_domains: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()
    recency: str = ""
    depth: str = ""

    @classmethod
    def from_arguments(
        cls, allowed: Any = None, blocked: Any = None, recency: Any = None, depth: Any = None
    ) -> "SearchOptions":
        recency_ = str(recency or "").strip().lower()
        depth_ = str(depth or "").strip().lower()
        return cls(
            allowed_domains=normalise_domains(allowed),
            blocked_domains=normalise_domains(blocked),
            recency=recency_ if recency_ in RECENCY_DAYS else "",
            depth=depth_ if depth_ in DEPTHS else "",
        )

    @property
    def narrows(self) -> bool:
        """Whether results will be removed after the provider answers."""
        return bool(self.allowed_domains or self.blocked_domains or self.recency)

    @property
    def active(self) -> bool:
        return self.narrows or bool(self.depth)

    def cutoff(self, now: Optional[datetime] = None) -> Optional[datetime]:
        days = RECENCY_DAYS.get(self.recency)
        if not days:
            return None
        return (now or datetime.now(timezone.utc)) - timedelta(days=days)


def _published(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def apply(
    results: list[SearchResult], options: SearchOptions, now: Optional[datetime] = None
) -> tuple[list[SearchResult], int]:
    """`(kept, removed)`: results inside the allowed domains, outside the blocked ones, and no
    older than the recency window (undated results are kept)."""
    if not options.narrows:
        return results, 0
    cutoff = options.cutoff(now)
    kept: list[SearchResult] = []
    for row in results:
        if options.allowed_domains and not host_matches(row.url, options.allowed_domains):
            continue
        if options.blocked_domains and host_matches(row.url, options.blocked_domains):
            continue
        if cutoff is not None:
            when = _published(row.published_at)
            if when is not None and when < cutoff:
                continue
        kept.append(row)
    return kept, len(results) - len(kept)


def provider_count(wanted: int, options: SearchOptions) -> int:
    """How many results to ask the provider for, given how many will be shown."""
    if not options.narrows:
        return wanted
    return min(max(wanted * OVERFETCH, wanted + 10), MAX_FETCH)


def ddg_query(query: str, options: SearchOptions) -> str:
    """DuckDuckGo has no domain parameter; its operators are the way: `site:a OR site:b`, `-site:c`."""
    parts = [query]
    if options.allowed_domains:
        sites = " OR ".join(f"site:{d}" for d in options.allowed_domains[:5])
        parts.append(f"({sites})" if len(options.allowed_domains) > 1 else sites)
    parts.extend(f"-site:{d}" for d in options.blocked_domains[:5])
    return " ".join(parts)


def ddg_timelimit(options: SearchOptions) -> Optional[str]:
    return {"day": "d", "week": "w", "month": "m", "year": "y"}.get(options.recency)
