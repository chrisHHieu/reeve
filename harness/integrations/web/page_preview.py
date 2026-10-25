"""The start of a fetched page, as the fetch card shows it to a person.

The card used to print the first 800 characters of what the model read as one run of text: the
page's own Markdown with its furniture (`# Coroutines and tasks¶`, `[Lib/asyncio/coroutines.py]
(<https://…>)`, `* * *`) and its line breaks collapsed. Measured 2026-10-05 on docs.python.org,
Wikipedia and a blog: every preview opened on that furniture. The card now renders the preview
as Markdown, so this keeps the page's structure (headings, lists, tables, code, emphasis) and
takes out what a reader of the card should not get:

  · images: a rendered `![](src)` would load a third party's picture from the GUI, past the
    egress gate every fetch goes through (the reason the card's site mark is not a favicon);
  · link targets: a page's links are relative to the page (`</3.14/copyright.html>`) and open
    nothing from a card, so a link becomes its words;
  · a block that is nothing but links (a language list, a table of contents, "Edit links",
    "Subscribe"): navigation, which is what Wikipedia's main content opened on;
  · a docs page's heading anchors (`¶`), horizontal rules, and list items left empty;
  · a first heading that is the page's title again: the card already shows the title.

The cut falls between blocks, never inside one, so the preview never ends on half a table or an
open code fence; only a first block longer than the whole preview is cut inside, at a word (a
line, for code) with `…`. The model's text is not touched: this is the card's copy.

JSON is shown indented (`text_preview(..., json=True)`): an API answers on one line, and the
card showed a wall of it. The text is usually a window of a longer document, so this indents
what it has without parsing it whole.
"""

from __future__ import annotations

import re

from .snippets import same_title

PREVIEW_CHARS = 2000

_FENCE = re.compile(r"^\s*(```|~~~)")
_IMAGE = re.compile(r"!\[[^\]]*\]\((?:[^()]|\([^)]*\))*\)")
_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)")
_RULE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_EMPTY_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
#: A line that is only links (a list item or not): navigation, once every line of a block is.
_LINK_ONLY = re.compile(
    r"^\s*(?:(?:[-*+]|\d+[.)])\s+)?(?:\[[^\]]*\]\((?:[^()]|\([^)]*\))*\)[\s|·,]*)+$"
)


def _clean_line(line: str, *, links: bool) -> str:
    line = _IMAGE.sub("", line)
    if not links:
        line = _LINK.sub(r"\1", line)
    line = line.replace("¶", "").rstrip()
    if _RULE.match(line) or _EMPTY_ITEM.match(line):
        return ""
    return line


def _blocks(text: str, *, links: bool) -> list[str]:
    """The text as Markdown blocks (a blank line outside a fence ends one), cleaned; a block
    of nothing but links is left out unless links are kept."""
    blocks: list[str] = []
    current: list[str] = []
    nav = True  # every line of the current block so far is only links
    fence = ""

    def close() -> None:
        nonlocal current, nav
        if current and (links or not nav):
            blocks.append("\n".join(current))
        current, nav = [], True

    for raw in text.replace("\r\n", "\n").split("\n"):
        opener = _FENCE.match(raw)
        if fence:
            current.append(raw)
            if opener and opener.group(1) == fence:
                fence = ""
            continue
        if opener:
            fence = opener.group(1)
            current.append(raw)
            nav = False
            continue
        if not raw.strip():
            close()
            continue
        line = _clean_line(raw, links=links)
        if line:
            nav = nav and bool(_LINK_ONLY.match(_IMAGE.sub("", raw)))
            current.append(line)
    if fence:  # a fence the page never closed
        current.append(fence)
    close()
    return blocks


def _cut_block(block: str, limit: int) -> str:
    """The first `limit` characters of one block, cut where a reader can tell it was."""
    fence = _FENCE.match(block)
    if fence or block.startswith("    "):
        lines = block.split("\n")
        kept: list[str] = []
        for line in lines:
            if sum(len(k) + 1 for k in kept) + len(line) > limit and kept:
                break
            kept.append(line)
        if fence and len(kept) < len(lines):
            kept.append(fence.group(1))
        return "\n".join(kept)
    head = block[: limit - 1]
    space = head.rfind(" ")
    return (head[:space] if space >= limit // 2 else head).rstrip(" ,;:—") + "…"


def page_preview(
    text: str, *, title: str = "", limit: int = PREVIEW_CHARS, links: bool = False
) -> str:
    """The page's first blocks as Markdown a card can render, at most about `limit` characters.

    `links=True` keeps link targets (and link-only blocks): for prose a model wrote (an
    answer), whose links are whole addresses it chose; a page's own links are not.
    """
    blocks = _blocks(str(text or ""), links=links)
    heading = _HEADING.match(blocks[0]) if blocks and "\n" not in blocks[0] else None
    if heading and title and same_title(heading.group(1), title):
        blocks = blocks[1:]
    kept: list[str] = []
    used = 0
    for block in blocks:
        if used + len(block) > limit:
            if not kept:
                kept.append(_cut_block(block, limit))
            break
        kept.append(block)
        used += len(block) + 2
    return "\n\n".join(kept)


def _indent_json(text: str, limit: int) -> str:
    """JSON with one value per line, from the start of a document that may be cut anywhere:
    a scan that tracks strings and nesting, not a parse, so half a document indents too."""
    out: list[str] = []
    depth, size, quoted, escaped = 0, 0, False, False
    for char in text:
        if size > limit:
            break
        if quoted:
            piece = char
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted, piece = True, char
        elif char in "{[":
            depth += 1
            piece = char + "\n" + "  " * depth
        elif char in "}]":
            depth = max(0, depth - 1)
            piece = "\n" + "  " * depth + char
        elif char == ",":
            piece = ",\n" + "  " * depth
        elif char == ":":
            piece = ": "
        elif char in " \t\r\n":
            continue
        else:
            piece = char
        out.append(piece)
        size += len(piece)
    return "".join(out)


def text_preview(text: str, *, limit: int = PREVIEW_CHARS, json: bool = False) -> str:
    """The start of text that is not Markdown (plain text, JSON, XML), cut at a line. `json`
    indents a document that arrived on one line."""
    body = str(text or "").replace("\r\n", "\n").strip("\n")
    if json and body[:1] in ("{", "[") and "\n" not in body[:200]:
        body = _indent_json(body, limit + 1)
    if len(body) <= limit:
        return body
    head = body[:limit]
    line = head.rfind("\n")
    return (head[:line] if line >= limit // 2 else head).rstrip() + "\n…"


__all__ = ["PREVIEW_CHARS", "page_preview", "text_preview"]
