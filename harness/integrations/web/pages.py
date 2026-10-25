"""A fetched page is a document the session holds.

`web_fetch` used to treat every call as a new download and every long page as something to cut
once: the head and the tail were returned, the middle went to a file, and nothing said how to
read it. Real session, 2026-10-05: an agent asked for the same paper with `max_chars` 100,000
and then 200,000, got the same 48,000 characters twice (and two identical files), concluded the
tool had a hard cap, and wrote in its memory not to trust an answered fetch. For a paper the
middle is the method.

What the tools this was measured against do: the reference MCP fetch server reads on by
`start_index`; OpenAI's web tool has `open_page` and `find_in_page`; a fetched
URL for fifteen minutes; the Anthropic API returns an answer's citations with their character
positions.

So a page is fetched once and then READ: a window from a position, the places a phrase occurs,
or an answer whose quotes are checked against the text. Everything here is pure except the
store, which is a small lock-guarded map.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

#: How long a fetched page answers for its URL. Fifteen minutes.
PAGE_TTL_SECONDS = 15 * 60
#: Pages held per session, and the characters they may hold together. Past either the least
#: recently read page goes; it is fetched again if asked for.
PAGE_KEEP = 16
PAGE_KEEP_CHARS = 12_000_000
#: A window ends at a line break when one falls in its last fifth, so the next one starts on
#: a line.
_LINE_BREAK_SHARE = 0.8
#: `find`: how many places are shown, and how much text around each.
FIND_SHOWN = 12
FIND_CONTEXT_CHARS = 240
#: Evidence: how many quotes an answer may rest on, and the shortest one worth checking.
EVIDENCE_MAX = 6
EVIDENCE_MIN_CHARS = 12
EVIDENCE_QUOTE_CHARS = 320
EVIDENCE_MARK = "EVIDENCE:"


@dataclass
class Page:
    """One fetched page, as text in one format."""

    url: str
    status_code: int
    content_type: str
    text: str
    fetched_at: float = field(default_factory=time.time)
    #: Where the whole text is on disk once something needed it there.
    saved: Optional[str] = None
    #: What the fetch reports beside the text (scope, boilerplate, https, a UA retry…).
    about: dict[str, Any] = field(default_factory=dict)


class PageStore:
    """The pages one session has fetched, by `(url, format)`, for `PAGE_TTL_SECONDS`."""

    def __init__(self, *, ttl: float = PAGE_TTL_SECONDS, clock: Callable[[], float] = time.time) -> None:
        self._ttl = ttl
        self._clock = clock
        self._pages: dict[tuple[str, str], Page] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(url: str, fmt: str) -> tuple[str, str]:
        # A fragment names a place in a page, not another page.
        return (url.split("#", 1)[0].strip(), fmt)

    def get(self, url: str, fmt: str) -> Optional[Page]:
        key = self.key(url, fmt)
        with self._lock:
            page = self._pages.get(key)
            if page is None:
                return None
            if self._clock() - page.fetched_at > self._ttl:
                del self._pages[key]
                return None
            self._pages[key] = self._pages.pop(key)  # most recently read goes last
            return page

    def put(self, url: str, fmt: str, page: Page) -> Page:
        page.fetched_at = self._clock()  # the store's clock, which is the one `get` reads
        with self._lock:
            self._pages.pop(self.key(url, fmt), None)
            self._pages[self.key(url, fmt)] = page
            while len(self._pages) > PAGE_KEEP or (
                len(self._pages) > 1 and sum(len(p.text) for p in self._pages.values()) > PAGE_KEEP_CHARS
            ):
                del self._pages[next(iter(self._pages))]
        return page

    def age(self, page: Page) -> float:
        return max(0.0, self._clock() - page.fetched_at)


# -- a window from a position --------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    text: str
    start: int
    end: int
    total: int

    @property
    def more(self) -> bool:
        return self.end < self.total


def window(text: str, start: int, size: int, max_lines: Optional[int] = None) -> Window:
    """`text[start : start + size]`, ended on a line break where one is near, and held to
    `max_lines`. Continuous: what a reader gets is the page in order, never its two ends."""
    total = len(text)
    start = max(0, min(int(start), total))
    end = min(total, start + max(1, int(size)))
    if end < total:
        cut = text.rfind("\n", start + int((end - start) * _LINE_BREAK_SHARE), end)
        if cut > start:
            end = cut + 1
    if max_lines is not None:
        at = start
        for _ in range(max(1, max_lines)):
            cut = text.find("\n", at, end)
            if cut < 0:
                at = end
                break
            at = cut + 1
        end = min(end, at)
    return Window(text=text[start:end], start=start, end=end, total=total)


# -- the places a phrase occurs ------------------------------------------------------------


@dataclass(frozen=True)
class Match:
    at: int
    text: str


_QUOTES = "\"“”„«»"
_APOSTROPHES = "'’‘`"
_DASHES = "-‐‑‒–—"


def _loose(phrase: str) -> Optional[re.Pattern[str]]:
    """`phrase` as a pattern that survives what copying does to text: any run of whitespace
    for a space, and any quote or dash of the same family for the one typed."""
    words = phrase.split()
    if not words:
        return None
    parts = []
    for word in words:
        out = []
        for char in word:
            if char in _QUOTES:
                out.append(f"[{re.escape(_QUOTES)}]")
            elif char in _APOSTROPHES:
                out.append(f"[{re.escape(_APOSTROPHES)}]")
            elif char in _DASHES:
                out.append(f"[{re.escape(_DASHES)}]")
            else:
                out.append(re.escape(char))
        parts.append("".join(out))
    return re.compile(r"\s+".join(parts), re.IGNORECASE)


def find(text: str, phrase: str, shown: int = FIND_SHOWN, context: int = FIND_CONTEXT_CHARS) -> tuple[list[Match], int]:
    """Where `phrase` occurs in `text` (plain text, case-insensitive): the first `shown`
    places with the text around each, and how many there are in all."""
    pattern = _loose(phrase)
    if pattern is None:
        return [], 0
    matches: list[Match] = []
    count = 0
    for found in pattern.finditer(text):
        count += 1
        if len(matches) >= shown:
            continue
        lo = max(0, found.start() - context)
        hi = min(len(text), found.end() + context)
        # Whole lines where the line is near: an excerpt that starts mid-word reads as noise.
        line = text.rfind("\n", lo, found.start())
        lo = line + 1 if line >= 0 else lo
        line = text.find("\n", found.end(), hi)
        hi = line if line >= 0 else hi
        matches.append(Match(at=found.start(), text=text[lo:hi].strip()))
    return matches, count


# -- an answer's quotes, checked against the page ------------------------------------------


@dataclass(frozen=True)
class Quote:
    text: str
    #: Where it is in the page, or None when the page does not hold it.
    at: Optional[int]


_MARK = re.compile(r"^[#*_\s>-]*EVIDENCE\s*:?[*_\s]*(.*)$", re.IGNORECASE)
_LEAD = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_ELLIPSIS = re.compile(r"\s*(?:\.{3,}|…|\[\s*(?:\.{3}|…)\s*\])\s*")


def split_evidence(answer: str) -> tuple[str, list[str]]:
    """`(the answer without its evidence section, the quotes it listed)`.

    The section is the LAST line that starts with `EVIDENCE:` and everything after it, one
    quote a line. An answer with no such section comes back whole, with no quotes."""
    lines = answer.splitlines()
    mark = next((i for i in range(len(lines) - 1, -1, -1) if _MARK.match(lines[i])), None)
    if mark is None:
        return answer.strip(), []
    first = _MARK.match(lines[mark]).group(1)  # type: ignore[union-attr]
    quotes: list[str] = []
    for raw in [first, *lines[mark + 1:]]:
        quote = _LEAD.sub("", raw).strip().strip(_QUOTES + _APOSTROPHES).strip()
        if len(quote) < EVIDENCE_MIN_CHARS or quote.lower() in ("none", "none."):
            continue
        quotes.append(quote[:EVIDENCE_QUOTE_CHARS])
        if len(quotes) >= EVIDENCE_MAX:
            break
    return "\n".join(lines[:mark]).strip(), quotes


def locate(text: str, quote: str) -> Optional[int]:
    """Where `quote` is in `text`, or None. A quote the reader shortened with an ellipsis is
    found when every piece of it is there, in order and close together."""
    pieces = [piece for piece in _ELLIPSIS.split(quote) if len(piece.strip()) >= 4]
    if not pieces:
        return None
    first: Optional[int] = None
    at = 0
    for piece in pieces:
        pattern = _loose(piece)
        found = pattern.search(text, at) if pattern is not None else None
        if found is None or (first is not None and found.start() - at > 4_000):
            return None
        first = found.start() if first is None else first
        at = found.end()
    return first


def check_quotes(text: str, quotes: list[str]) -> list[Quote]:
    return [Quote(text=quote, at=locate(text, quote)) for quote in quotes]


__all__ = [
    "EVIDENCE_MARK",
    "EVIDENCE_MAX",
    "FIND_SHOWN",
    "Match",
    "PAGE_TTL_SECONDS",
    "Page",
    "PageStore",
    "Quote",
    "Window",
    "check_quotes",
    "find",
    "locate",
    "split_evidence",
    "window",
]
