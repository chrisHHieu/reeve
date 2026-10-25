"""Which sources an answer cites that it only ever saw as a search-result snippet.

`web_search` returns a title, a URL and a few hundred characters of each page. That finds a
source; it does not read one. Checked against the papers themselves, a research answer in the
store described a withdrawn paper, and a figure on no page, from snippets alone; counted over
the store, 31 of the 40 answers that cited a searched URL cited one nothing had opened.

What the harness can know of it, without judging what the answer says:

- a SEARCHED source is a URL a `web_search` result of this session listed;
- an OPENED one is a URL any other call named in its arguments (`web_fetch`, a browser, a
  shell `curl`), the person's own message named, or a `task` report named. A report is
  trusted the way the parent trusts it: the harness cannot see what a child read. A search
  result given with the text its engine read from the page (`READ_MARK`, 2026-10-19) is opened
  too: the model was given the page, not a description of it;
- a source is the same source without its scheme, `www.`, fragment or trailing slash, and an
  arXiv link is its paper id, whichever of `abs`, `html` or `pdf` (and version) was linked.

The finish gate's own note names the unread sources; it is not the person naming them, so a
message attached by the harness opens nothing.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from harness.session.turns import ATTACHED_BY

SEARCH_TOOL = "web_search"
REPORT_TOOL = "task"
#: The line `web_search` writes above a result's page text (`integrations/web/search_present.py`).
READ_MARK = "Read from the page:"
_RESULT = re.compile(r"(?m)^\d+\. ")

_URL = re.compile(r"https?://[^\s<>()\[\]{}\"'`|\\]+")
_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", re.IGNORECASE)
_TRAILING = ".,;:!?*_"


def _clean(url: str) -> str:
    return url.rstrip(_TRAILING)


def source_key(url: str) -> str:
    """One name for one source, whichever way it was linked."""
    url = _clean(url)
    paper = _ARXIV.search(url)
    if paper:
        return f"arxiv:{paper.group(1)}"
    bare = re.sub(r"^https?://(?:www\.)?", "", url, flags=re.IGNORECASE).split("#", 1)[0]
    host, _, path = bare.partition("/")
    return f"{host.lower()}/{path}".rstrip("/")


def _urls(text: str) -> list[str]:
    return [_clean(url) for url in _URL.findall(text or "")]


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        )
    return ""


def _arguments(call: dict[str, Any]) -> tuple[str, str]:
    function = call.get("function") if isinstance(call.get("function"), dict) else call
    raw = function.get("arguments")
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    return str(function.get("name") or ""), text or ""


class SourceLedger:
    """What a session searched and what it opened, fed one message at a time."""

    def __init__(self) -> None:
        self.searched: set[str] = set()
        self.opened: set[str] = set()
        self._calls: dict[str, str] = {}

    def add(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                name, arguments = _arguments(call)
                self._calls[str(call.get("id") or "")] = name
                if name != SEARCH_TOOL:
                    self.opened.update(source_key(url) for url in _urls(arguments))
        elif role == "user" and ATTACHED_BY not in message:
            self.opened.update(source_key(url) for url in _urls(_text(message.get("content"))))
        elif role == "tool":
            name = self._calls.get(str(message.get("tool_call_id") or ""))
            found = {source_key(url) for url in _urls(_text(message.get("content")))}
            if name == SEARCH_TOOL:
                self.searched |= found
                self.opened |= _read_results(_text(message.get("content")))
            elif name == REPORT_TOOL:
                self.opened |= found

    def cited(self, answer: str) -> list[str]:
        """The searched sources `answer` links, once each, as it wrote them."""
        out: dict[str, str] = {}
        for url in _urls(answer):
            key = source_key(url)
            if key in self.searched and key not in out:
                out[key] = url
        return list(out.values())

    def unread(self, answer: str) -> list[str]:
        """The searched sources `answer` links that nothing opened."""
        return [url for url in self.cited(answer) if source_key(url) not in self.opened]


def _read_results(text: str) -> set[str]:
    """The sources a search result gave as read pages: each result is `N. title`, its URL on the
    next line, then `READ_MARK` and the page or a snippet line. Only the result's own URL counts,
    never one inside its page text."""
    read: set[str] = set()
    for block in _RESULT.split(text or "")[1:]:
        lines = block.splitlines()
        if len(lines) < 3 or lines[2].strip() != READ_MARK:
            continue
        own = _urls(lines[1])
        if own:
            read.add(source_key(own[0]))
    return read


def unread_sources(messages: Iterable[Any], answer: Optional[str]) -> list[str]:
    """The sources `answer` cites that this session saw only as search snippets."""
    if not answer or "http" not in answer:
        return []
    ledger = SourceLedger()
    for message in messages:
        ledger.add(message)
    return ledger.unread(answer)
