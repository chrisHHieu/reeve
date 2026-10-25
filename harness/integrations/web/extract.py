"""HTML → the page's readable text, for `web_fetch`.

Split out of `fetch.py` (size ceiling) together with the one thing that is new: **which part of
the page is the page**. A fetched documentation page used to come back as its menu, its sidebar,
its cookie banner, the article, and its footer links, in document order; the model read all of
it, and every later step carried it. The page is parsed ONCE into three views and one is chosen:

  main   what sits inside `<main>`, `<article>` or `role=main`, minus the boilerplate below
  clean  the whole page minus the boilerplate: `<nav>`, `<footer>`, `<aside>`, the ARIA landmarks `navigation` / `banner` / `contentinfo` /
         `complementary` / `search`, and a short list of exact class/id names (cookie banners,
         newsletter boxes, breadcrumbs, sidebars)
  full   the whole page minus only what is never text (scripts, styles, hidden subtrees)

`main` is chosen only when it holds a real share of the page's text, `clean` only when dropping
the boilerplate did not remove most of the page; otherwise `full`. The fallback is the point: a
site that keeps its content in an `<aside>`, or marks a tiny teaser as the `<article>`, reads as
it always did rather than as a stub. The result says which view it is and how much it left out.

Elements are tracked on a stack, not a depth counter: a subtree that skips (a hidden `<ul>` whose
`<li>` were never closed) ends when its OWN end tag arrives, instead of swallowing the rest of the
document.

A page that is a JavaScript shell (almost no text, scripts and a mount point) is named as one, so
the model reaches for the browser tools instead of concluding the page is empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Optional

import html2text

from .html_depth import (  # noqa: F401 — `_VOID_TAGS` is used here; the rest is re-exported
    _MAX_CONVERSION_DEPTH,
    _RAW_TEXT_TAGS,
    _VOID_TAGS,
    _exceeds_conversion_depth,
)

#: Pre-parse ceiling. A 10 MB page is HTML nobody reads to the end, and converting it is
#: CPU time on the event loop; the output cap is applied after conversion, so this is
#: what keeps a hostile page from being expensive before the first character is useful.
_MAX_HTML_CHARS = 2_000_000

#: Shown where a page's converted text would be when the DOM is nested past what the converter
#: can walk safely. The same wording shape as other omission markers.
_HTML_OMITTED = "[HTML content omitted: nested too deeply to convert safely]"

#: Subtrees that are never page text: active content and document furniture.
_SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "head",
    "template",
    "iframe",
    "object",
    "embed",
}
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)", re.I)
# -- what is boilerplate ---------------------------------------------------------------

_BOILERPLATE_TAGS = {"nav", "footer", "aside"}
_BOILERPLATE_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search"}
#: EXACT class/id values, never fragments: `with-sidebar` is a layout wrapper around the content,
#: and dropping it would drop the page. Only on elements that cannot be one (below).
_BOILERPLATE_NAMES = {
    "cookie",
    "cookies",
    "cookie-banner",
    "cookie-consent",
    "cookie-notice",
    "gdpr",
    "newsletter",
    "newsletter-signup",
    "breadcrumb",
    "breadcrumbs",
    "sidebar",
    "skip-link",
    "skiplink",
}
#: Structural elements a class name must never remove (`<body class="sidebar">`).
_NEVER_BY_NAME = {"html", "body", "main", "article"}
_ROOT_IDS = {"root", "app", "__next", "__nuxt", "___gatsby", "svelte"}

#: A view smaller than this is not trusted to be "the page"; and the share of the wider view it
#: has to hold. Tuned conservatively: a wrong fallback costs tokens, a wrong cut costs the answer.
_MIN_VIEW_CHARS = 200
_MIN_MAIN_SHARE = 0.20
_MIN_CLEAN_SHARE = 0.35
#: Under this much visible text, with scripts or a mount point present, the page is a JS shell.
_SHELL_TEXT_CHARS = 300


# -- the parse -------------------------------------------------------------------------


@dataclass
class _View:
    name: str
    out: list[str] = field(default_factory=list)
    #: Visible text characters the view holds (what the choice is judged on).
    chars: int = 0
    #: The stack position at which this view began skipping a subtree, None when it is not.
    skip_from: Optional[int] = None


class _PageParser(HTMLParser):
    """The page, three ways at once (see the module docstring)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.views = {name: _View(name) for name in ("full", "clean", "main")}
        self.stack: list[str] = []
        #: The stack position of the outermost main/article/role=main element we are inside.
        self.region_from: Optional[int] = None
        self.scripts = 0
        self.root_hint = False
        self._noscript = 0
        self.noscript_text = ""
        #: The document's `<title>` (the first one, not an SVG's), for the card that shows the
        #: fetch; the model's text does not carry it.
        self.title = ""
        self._in_title = False

    # -- classification

    @staticmethod
    def _attrs(attrs: Any) -> dict[str, str]:
        return {str(k).lower(): (v or "") for k, v in attrs}

    @staticmethod
    def _hidden(tag: str, d: dict[str, str]) -> bool:
        if tag in _SKIP_TAGS:
            return True
        if "hidden" in d or d.get("aria-hidden", "").strip().lower() == "true":
            return True
        style = d.get("style", "")
        return bool(style and _HIDDEN_STYLE.search(style))

    def _boilerplate(self, tag: str, d: dict[str, str]) -> bool:
        if tag in _BOILERPLATE_TAGS:
            return True
        if d.get("role", "").strip().lower() in _BOILERPLATE_ROLES:
            return True
        if tag in _NEVER_BY_NAME:
            return False
        names = {d.get("id", "").strip().lower(), *d.get("class", "").lower().split()}
        return bool(names & _BOILERPLATE_NAMES)

    # -- emitting

    def _live(self, view: _View) -> bool:
        """Whether this view takes output now: not inside one of its skipped subtrees, and for
        `main`, inside a content region."""
        if view.skip_from is not None:
            return False
        return view.name != "main" or self.region_from is not None

    def _start(self, tag: str, attrs: Any, *, void: bool) -> None:
        d = self._attrs(attrs)
        if tag == "title" and not void and not self.title and "svg" not in self.stack:
            self._in_title = True
        if tag == "script":
            self.scripts += 1
        if d.get("id", "").strip().lower() in _ROOT_IDS:
            self.root_hint = True
        hidden = self._hidden(tag, d)
        boilerplate = not hidden and self._boilerplate(tag, d)
        position = len(self.stack)
        if (
            self.region_from is None
            and not hidden
            and (tag in ("main", "article") or d.get("role", "").strip().lower() == "main")
            and not void
        ):
            self.region_from = position  # before emitting: the region's own start tag is in `main`
        text = self.get_starttag_text() or f"<{tag}>"
        for view in self.views.values():
            if view.skip_from is not None:
                continue
            if hidden or (boilerplate and view.name != "full"):
                if not void:
                    view.skip_from = position
                continue
            if view.name == "main" and self.region_from is None:
                continue
            view.out.append(text)
        if not void:
            self.stack.append(tag)
            if tag == "noscript":
                self._noscript += 1

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        self._start(tag, attrs, void=tag in _VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        self._start(tag, attrs, void=True)

    def handle_endtag(self, tag: str) -> None:
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i] == tag), None)
        if index is None:
            return  # an end tag with nothing open: there is nothing to close
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join(self.title.split())[:300]
        in_region = self.region_from is not None
        text = f"</{tag}>"
        for view in self.views.values():
            skipping = view.skip_from is not None and view.skip_from <= index
            if skipping:
                continue
            if view.name == "main" and not in_region:
                continue
            view.out.append(text)
        for popped in self.stack[index:]:
            if popped == "noscript":
                self._noscript -= 1
        del self.stack[index:]
        for view in self.views.values():
            if view.skip_from is not None and view.skip_from >= index:
                view.skip_from = None
        if self.region_from is not None and self.region_from >= index:
            self.region_from = None

    def handle_data(self, data: str) -> None:
        if self._noscript and len(self.noscript_text) < 400:
            self.noscript_text += data
        if self._in_title and len(self.title) < 1000:
            self.title += data
        visible = len(data.strip())
        for view in self.views.values():
            if self._live(view):
                view.out.append(data)
                view.chars += visible


@dataclass
class Page:
    """The chosen view of a page, before conversion to Markdown or text."""

    html: str
    scope: str  # "main" | "clean" | "full"
    #: Visible text characters the chosen view left out of the whole page.
    omitted_chars: int = 0
    #: The page is a JavaScript shell: almost no text, and scripts or a mount point.
    js_required: bool = False
    chars: int = 0
    title: str = ""


def _parse(html: str, scope: str) -> Page:
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — a parser surprise must not cost the fetch
        return Page(html=html, scope="full")
    full, clean, main = (parser.views[n].chars for n in ("full", "clean", "main"))
    chosen = "full"
    if scope == "main" and full >= _MIN_VIEW_CHARS:
        if main >= max(_MIN_VIEW_CHARS, _MIN_MAIN_SHARE * clean):
            chosen = "main"
        elif clean >= max(_MIN_VIEW_CHARS, _MIN_CLEAN_SHARE * full):
            chosen = "clean"
    elif scope == "clean" and clean >= max(_MIN_VIEW_CHARS, _MIN_CLEAN_SHARE * full):
        chosen = "clean"
    view = parser.views[chosen]
    shell = full < _SHELL_TEXT_CHARS and (
        parser.scripts >= 2 or parser.root_hint or "javascript" in parser.noscript_text.lower()
    )
    return Page(
        html="".join(view.out),
        scope=chosen,
        omitted_chars=max(0, full - view.chars),
        js_required=shell,
        chars=view.chars,
        title=parser.title if not parser._in_title else " ".join(parser.title.split())[:300],
    )


def _markdown(html: str) -> str:
    """Markdown, not plain text: headings, lists, tables and code fences survive, so the
    model reads the page's structure instead of one undifferentiated paragraph."""
    conv = html2text.HTML2Text()
    conv.body_width = 0  # no hard wrapping — the reader decides line length
    conv.ignore_images = False
    conv.unicode_snob = True
    conv.protect_links = True  # do not auto-escape URLs
    text = conv.handle(html)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# -- plain text (`format="text"`) ------------------------------------------------------

_TEXT_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
}


class _TextExtractor(HTMLParser):
    """Visible text with block breaks — the pre-Markdown extractor, kept for `text`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _TEXT_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _TEXT_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text + " ")


def _plain(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    joined = "".join(parser.parts)
    joined = re.sub(r"[ \t]+\n", "\n", joined)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


# -- the entry points -------------------------------------------------------------------


@dataclass
class Extracted:
    text: str
    scope: str = "full"
    omitted_chars: int = 0
    js_required: bool = False
    #: The document's `<title>`, "" when it has none.
    title: str = ""


def extract(html: str, fmt: str = "markdown", scope: str = "main") -> Extracted:
    """The page's text in `fmt` ("markdown" | "text") from the view `scope` asks for ("main" |
    "clean" | "full"; the view actually used is `.scope`, and may be wider)."""
    if len(html) > _MAX_HTML_CHARS:
        html = html[:_MAX_HTML_CHARS]
    if _exceeds_conversion_depth(html):
        return Extracted(text=_HTML_OMITTED)
    page = _parse(html, scope)
    text = _plain(page.html) if fmt == "text" else _markdown(page.html)
    return Extracted(
        text=text,
        scope=page.scope,
        omitted_chars=page.omitted_chars,
        js_required=page.js_required,
        title=page.title,
    )


def _html_to_markdown(html: str) -> str:
    """The whole page as Markdown (hidden subtrees dropped): `extract` with the widest view."""
    return extract(html, "markdown", "full").text


def _html_to_text(html: str) -> str:
    return extract(html, "text", "full").text


def _is_textual(mime: str) -> bool:
    """What this tool can read. Anything else (PDF, images, octet-stream) is refused with
    its type rather than decoded into mojibake — the old behaviour returned UTF-8 garbage
    that read like a broken page instead of a file this tool cannot open."""
    if not mime:
        return True  # a server that forgot the header still gets its text tried
    if mime.startswith("text/"):
        return True
    if mime in ("application/json", "application/xml"):
        return True
    return mime.endswith("+json") or mime.endswith("+xml")


def _is_html(mime: str) -> bool:
    return mime in ("text/html", "application/xhtml+xml")
