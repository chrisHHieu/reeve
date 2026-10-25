"""The text of an HTML page, without its scripts and styles."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

_SKIPPED = {"script", "style", "noscript", "svg", "head"}


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _SKIPPED:
            self._hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text and not self._hidden:
            self.parts.append(text)


def html_to_text(html: str) -> str:
    """One text block per line, blank runs collapsed. Malformed HTML gives what was read so far."""
    collector = _TextCollector()
    try:
        collector.feed(html)
    except Exception:
        pass
    return re.sub(r"\n{3,}", "\n\n", "\n".join(collector.parts))
