"""The HTML nesting-depth guard `web_fetch` runs before it converts a page.

Split out of `extract.py` (size ceiling). Conversion walks the DOM synchronously on the event loop,
and unclosed-tag nesting makes the tree (and the walk over it) super-linear, so a hostile page is
refused by one cheap LEXICAL pass before any parser sees it. The pass over-counts malformed input
(the safe direction: the answer gates a synchronous walk) rather than hiding it.
"""

from __future__ import annotations

#: Nesting-depth ceiling above which HTML skips conversion. Conversion runs synchronously
#: on the event loop, and unclosed-tag nesting makes the DOM tree (and the walk over it)
#: super-linear. Real pages nest a few dozen
#: levels; 512 is far above content and far below weaponizable. An invariant, not a tunable.
_MAX_CONVERSION_DEPTH = 512


_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}

#: Elements whose contents HTML parses as text until their matching end tag.
_RAW_TEXT_TAGS = {"script", "style", "noscript"}


def _is_tag_boundary(char: str) -> bool:
    return not char or char in ">/" or char.isspace()


def _find_raw_text_end(lower_html: str, name: str, start: int) -> int:
    """The matching `</name…>` offset, without interpreting markup-like body text."""
    prefix = f"</{name}"
    candidate = lower_html.find(prefix, start)
    while candidate != -1 and not _is_tag_boundary(
        lower_html[candidate + len(prefix) : candidate + len(prefix) + 1]
    ):
        candidate = lower_html.find(prefix, candidate + len(prefix))
    return candidate


def _exceeds_conversion_depth(html: str) -> bool:
    """Whether the HTML's element nesting crosses `_MAX_CONVERSION_DEPTH`.

    One conservative lexical pass: comments are skipped, raw-text
    bodies are stepped over, quoted `>` characters do not end a tag, and a closing tag
    only pops the element it matches. Malformed input therefore OVER-counts nesting rather
    than hiding it — the safe direction, since the answer gates a synchronous DOM walk.
    """
    lower = html.lower()
    open_stack: list[str] = []
    offset = 0
    length = len(html)
    while offset < length:
        start = html.find("<", offset)
        if start == -1:
            break
        if html.startswith("<!--", start):
            end = html.find("-->", start + 4)
            offset = length if end == -1 else end + 3
            continue
        cursor = start + 1
        closing = html[cursor : cursor + 1] == "/"
        if closing:
            cursor += 1
        name_start = cursor
        while cursor < length and (html[cursor].isalnum() or html[cursor] == "-"):
            cursor += 1
        if cursor == name_start or not html[name_start].isalpha():
            offset = start + 1
            continue
        name = lower[name_start:cursor]
        quote = ""
        while cursor < length:
            char = html[cursor]
            cursor += 1
            if quote:
                if char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char == ">":
                break
        if cursor > length or html[cursor - 1] != ">":
            break
        if closing:
            if open_stack and open_stack[-1] == name:
                open_stack.pop()
        else:
            last = cursor - 2
            while last >= 0 and html[last].isspace():
                last -= 1
            if name not in _VOID_TAGS and not (last >= 0 and html[last] == "/"):
                if len(open_stack) >= _MAX_CONVERSION_DEPTH:
                    return True
                open_stack.append(name)
                if name in _RAW_TEXT_TAGS:
                    end = _find_raw_text_end(lower, name, cursor)
                    if end == -1:
                        break
                    offset = end
                    continue
        offset = cursor
    return False
