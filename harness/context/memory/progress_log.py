"""What a new session is shown of the progress log.

The log is loaded into every new session of its project, and the store shows what models write in
it: a diary. The two live logs (2026-10-14) were 5,649 and 4,352 characters, about 60% of each the
detail of finished work (dated update sections, "done" lists), with one checklist item between them.
Every request of every session in the project carried it, whether the session resumed that work or
not.

A short log is still shown whole. A longer one is shown as what a resume starts from, its headings
and its open items, with the file named to read before resuming. Both are lines the harness can tell
apart in any language (`#`, `- [ ]`), so the cut never depends on how a section is titled.
"""

from __future__ import annotations

import re

#: A log this long or shorter is shown whole: its outline and the line naming the file would cost
#: about as much as the log.
INLINE_CHARS = 2_000

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_OPEN_ITEM = re.compile(r"^\s*[-*+]\s+\[ \]\s+\S")


def outline(text: str) -> str:
    """The log's headings and open items, in the order the log has them."""
    return "\n".join(
        line.rstrip() for line in text.splitlines() if _HEADING.match(line) or _OPEN_ITEM.match(line)
    )


def shown(text: str, *, limit: int) -> tuple[str, bool]:
    """What a new session is shown of `text`, and whether that is the whole log. `limit` bounds
    either one, for a file written past the gate (by hand)."""
    if len(text) <= INLINE_CHARS:
        return text, True
    body = outline(text)
    if len(body) > limit:
        body = body[:limit].rstrip() + "\n… (outline cut at its load limit)"
    return body, False


__all__ = ["INLINE_CHARS", "outline", "shown"]
