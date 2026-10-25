"""A search result's snippet, as a reader (and the model) should get it.

Exa's highlights are page text, which is their value — but they arrive as the page's own
markdown: several passages joined by a `...` line, each with its headings (`## …`), table rules
(`| --- |`), link syntax and image tags, and often the page title again as the first line.
Measured on real responses, 2026-10-05: one highlight ran 2.6–4k characters, and the first 900
the product kept were a heading, a site banner and half a table. A card showed one result per
screen; the model paid for the pipes.

`clean_snippet` keeps the words and drops the furniture: headings lose their `#`, a table row
becomes its cells joined by ` — ` and its rule line goes, `[text](url)` becomes `text`, emphasis
markers and image tags go, the line that repeats the title goes, passages are joined by ` … `,
and the cut lands on a sentence (else a word) with `…` to say it is a cut. Nothing is invented
and nothing is reordered.
"""

from __future__ import annotations

import re

#: A line that is only a passage break (`...` / `…`), which is how Exa joins highlights.
_PASSAGE_BREAK = re.compile(r"\n\s*(?:\.\.\.|…)\s*\n")
_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)")
_EMPHASIS = re.compile(r"(\*\*|__|`)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
#: A blockquote marker, at the start of a line only: `x > 5` in a sentence is content.
_QUOTE = re.compile(r"^(?:>\s*)+")
_SPACE = re.compile(r"\s+")


def same_title(a: str, b: str) -> bool:
    """The line is the title again (Exa often opens a highlight with it)."""
    fold = lambda s: _SPACE.sub(" ", s).strip(" .:|-—").casefold()  # noqa: E731
    left, right = fold(a), fold(b)
    # A page title is often the heading plus the site: "Heading | Site", "Heading - Site".
    return bool(left) and bool(right) and (
        left == right or any(right.startswith(left + sep) for sep in (" |", " -", " —", " ·"))
    )


def _line(raw: str, title: str) -> str:
    line = raw.strip()
    if not line or _RULE.match(line) or _TABLE_RULE.match(line):
        return ""
    line = _IMAGE.sub("", line)
    line = _LINK.sub(r"\1", line)
    if line.startswith("|") or line.endswith("|"):
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        line = " — ".join(cell for cell in cells if cell)
    line = _HEADING.sub("", line)
    line = _LIST.sub("", line)
    line = _QUOTE.sub("", line)
    line = _EMPHASIS.sub("", line).replace("¶", "")  # a docs page's heading anchor
    line = _SPACE.sub(" ", line).strip()
    if not line or (title and same_title(line, title)):
        return ""
    return line


def _passage(raw: str, title: str) -> str:
    kept: list[str] = []
    for raw_line in raw.splitlines():
        line = _line(raw_line, title)
        if line and (not kept or kept[-1] != line):
            kept.append(line)
    # Lines of prose end in punctuation; a heading or a table cell does not, and reading them
    # run together ("Usage Context compaction shortens…") is worse than a separator.
    out = ""
    for line in kept:
        if out:
            out += " " if out[-1] in ".!?:;…)\"'”" else ". "
        out += line
    return out


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    sentence = max(head.rfind(". "), head.rfind("! "), head.rfind("? "), head.rfind(" … "))
    if sentence >= limit // 2:
        return head[: sentence + 1].rstrip(" …") + " …"
    space = head.rfind(" ")
    return (head[:space] if space >= limit // 2 else head).rstrip(" ,;:—") + "…"


def clean_snippet(text: str, *, title: str = "", limit: int = 900) -> str:
    """Readable prose from a provider's raw snippet, at most `limit` characters."""
    if not text:
        return ""
    passages = [_passage(part, title) for part in _PASSAGE_BREAK.split(str(text))]
    joined = " … ".join(part for part in passages if part)
    return _cut(joined, max(1, int(limit)))
