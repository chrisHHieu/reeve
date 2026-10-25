"""What a design check reads in the page's source (amendment of 2026-10-19).

The browser measures what is drawn; some first-draft habits are only in the source, and a browser
cannot see them: an emoji standing in for an icon, a gradient nobody asked for, "Lorem ipsum", a
clickable <div>, a transition on `width`. This reads the HTML and its inline CSS as text and names
each one with the line it is on. It needs no browser, so it runs even where the machine has none.

Every rule is one of the craft rules `design_guide` gives (`guide.py`), so the model is never held
to a rule it was not told. Two kinds of finding:

- **fix**: the page breaks a rule outright; counted in the check's `issues`;
- **check**: the source looks like a habit that is wrong unless the person said otherwise (numbers
  that read like invented proof); said, never counted.

It is deliberately plain text matching, not a parser of CSS: cheap, deterministic, and each finding
carries the line so the model can look before it changes anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional

#: How many findings one rule reports before it counts the rest.
_PER_RULE = 3
_MAX_SOURCE = 2_000_000

_GRADIENT = re.compile(r"\b(?:repeating-)?(?:linear|radial|conic)-gradient\s*\(", re.I)
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF☀-⛿✀-➿\U0001F900-\U0001F9FF]"
)
#: A slot of a layout (`layouts.py`) left in the page: "[[the outcome for the reader]]".
_SLOT = re.compile(r"\[\[[^\]]{1,160}\]\]")
#: A display face nobody chose: the families every default reaches for (the guide's machine-made list).
_DEFAULT_DISPLAY = re.compile(r"^\s*[\"']?(inter|roboto|arial)\b", re.I)
_DISPLAY_SELECTOR = re.compile(r"(^|[\s,>+~])h[12]\b|\.display\b|\.hero\b", re.I)
_DISPLAY_FAMILY = re.compile(r"(?:--font-display|font-family)\s*:\s*([^;}]+)", re.I)
_FILLER = re.compile(r"\blorem ipsum\b|\bdolor sit amet\b|\byour (?:text|title|content) here\b|\bplaceholder text\b", re.I)
_PROOF = re.compile(
    r"\b\d[\d,.]*\s*(?:\+|%|x|k\+?|m\+?)\s+(?:of\s+)?(?:users|customers|teams|companies|businesses|developers|"
    r"downloads|reviews|uptime|faster|countries|members|clients)\b|\btrusted by\s+\d",
    re.I,
)
_VAGUE = {"ok", "submit", "click here", "click", "here", "go"}
_LAYOUT_MOTION = re.compile(
    r"\btransition(?:-property)?\s*:[^;}]*\b(all|width|height|max-height|min-height|top|left|right|bottom|margin|padding)\b",
    re.I,
)
_ANIMATES = re.compile(r"\b(?:transition|animation)\s*:", re.I)
_REDUCED = re.compile(r"prefers-reduced-motion", re.I)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIDE_STRIPE = re.compile(r"\bborder-(?:left|inline-start)\s*:\s*([3-9]|\d{2,})px\b", re.I)
_RADIUS = re.compile(r"\bborder-radius\s*:", re.I)
_UPPER = re.compile(r"\btext-transform\s*:\s*uppercase\b", re.I)
_TRACKING = re.compile(r"\bletter-spacing\s*:", re.I)

_STATES = re.compile(
    r"\bloading\b|\bskeleton\b|\bno [a-z ]{1,24} yet\b|\bnothing (?:here|yet)\b|\bempty\b|\btry again\b|\bretry\b|"
    r"\bcould(?:n't| not) load\b|\bsomething went wrong\b|\berror\b",
    re.I,
)

_HIDDEN_TEXT = {"script", "style", "noscript", "template", "svg"}
_ACTIONS = {"button", "a", "input", "select", "textarea", "summary", "label", "option", "details"}


@dataclass
class LintFinding:
    rule: str
    kind: str  # "fix" or "check"
    line: int
    message: str


class _Reader(HTMLParser):
    """Collects what the rules read: the CSS (style blocks and attributes, with their lines), the
    visible text with its line, clickable non-controls, and the words on buttons and links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.css: list[tuple[int, str]] = []
        self.text: list[tuple[int, str]] = []
        self.clickable: list[tuple[int, str]] = []
        self.labels: list[tuple[int, str, str]] = []
        #: Where the page shows a collection: a table, or a list of three or more items.
        self.collection = 0
        self._items: list[int] = []
        self._stack: list[str] = []
        self._in_style = False
        self._label: Optional[list] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        line = self.getpos()[0]
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag == "style":
            self._in_style = True
        if values.get("style"):
            self.css.append((line, values["style"]))
        if tag not in _ACTIONS and ("onclick" in values or values.get("role") == "button"):
            self.clickable.append((line, tag))
        if tag == "input" and values.get("type", "").lower() in ("submit", "button") and values.get("value"):
            self.labels.append((line, "input", values["value"]))
        if tag == "table" and not self.collection:
            self.collection = line
        if tag in ("ul", "ol"):
            self._items.append(0)
        if tag == "li" and self._items:
            self._items[-1] += 1
            if self._items[-1] == 3 and not self.collection:
                self.collection = line
        if tag in ("button", "a") and self._label is None:
            self._label = [line, tag, ""]
        if tag not in ("br", "img", "input", "meta", "link", "hr", "source", "wbr", "col", "area", "base"):
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("ul", "ol") and self._items:
            self._items.pop()
        if tag == "style":
            self._in_style = False
        if self._label is not None and tag == self._label[1]:
            self.labels.append((self._label[0], self._label[1], self._label[2]))
            self._label = None
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        line = self.getpos()[0]
        if self._in_style:
            self.css.append((line, data))
            return
        if any(tag in _HIDDEN_TEXT for tag in self._stack):
            return
        if data.strip():
            self.text.append((line, data))
            if self._label is not None:
                self._label[2] += data


def _css_lines(chunks: list[tuple[int, str]]):
    """Each CSS rule `(line, selector, body)`; an attribute's declarations are one rule."""
    for start, css in chunks:
        if "{" not in css:
            yield start, "style attribute", css
            continue
        for match in _RULE.finditer(css):
            # The line the selector itself is on, not the newline before it.
            begins = match.start() + len(match.group(1)) - len(match.group(1).lstrip())
            yield start + css.count("\n", 0, begins), match.group(1).strip()[:60], match.group(2)


def _snippet(text: str, at: int, width: int = 50) -> str:
    left = max(0, at - width // 2)
    return " ".join(text[left : left + width].split())


def lint_source(source: str, *, design_md_text: str = "", state_pages: bool = False) -> list[LintFinding]:
    """The findings in a page's source. `design_md_text` (lower case) lifts a rule DESIGN.md
    overrides: a gradient it names is the project's own. `state_pages`: the page's states are drawn
    as pages beside it (`name.empty.html`, …)."""
    reader = _Reader()
    try:
        reader.feed(source[:_MAX_SOURCE])
        reader.close()
    except Exception:  # a page the parser cannot finish is still checked by the browser
        return []
    found: dict[str, list[LintFinding]] = {}

    def add(rule: str, kind: str, line: int, message: str) -> None:
        found.setdefault(rule, []).append(LintFinding(rule, kind, line, message))

    rules = list(_css_lines(reader.css))
    if "gradient" not in design_md_text:
        for line, selector, body in rules:
            if _GRADIENT.search(body):
                add("gradient", "fix", line, f"a gradient on {selector}; DESIGN.md asks for none")
    for line, selector, body in rules:
        for match in _DISPLAY_FAMILY.finditer(body):
            is_display = match.group(0).lower().startswith("--font-display") or _DISPLAY_SELECTOR.search(selector)
            face = _DEFAULT_DISPLAY.match(match.group(1))
            if is_display and face and face.group(1).lower() not in design_md_text:
                add("default-display", "check", line, f"{face.group(1)} as the display face on {selector}; a face with character reads designed")
        moving = _LAYOUT_MOTION.search(body)
        if moving:
            add("layout-motion", "fix", line, f"{selector} animates `{moving.group(1)}`; move only opacity and transform")
        if _SIDE_STRIPE.search(body) and _RADIUS.search(body):
            add("side-stripe", "fix", line, f"{selector} is a rounded card with a thick stripe down one side")
        if _UPPER.search(body) and not _TRACKING.search(body):
            add("caps-tracking", "fix", line, f"{selector} is ALL CAPS with no letter-spacing (0.04–0.08em)")
    if any(_ANIMATES.search(body) for _, _, body in rules) and not any(_REDUCED.search(css) for _, css in reader.css):
        add("reduced-motion", "fix", rules[0][0] if rules else 1, "things move and nothing honours prefers-reduced-motion")
    for line, text in reader.text:
        emoji = _EMOJI.search(text)
        if emoji:
            add("emoji", "fix", line, f"an emoji in the page (“{_snippet(text, emoji.start(), 30)}”); icons are inline SVG")
        slot = _SLOT.search(text)
        if slot:
            add("slot", "fix", line, f"a layout slot left in the page: “{_snippet(text, slot.start(), 40)}”")
        filler = _FILLER.search(text)
        if filler:
            add("filler", "fix", line, f"filler text “{filler.group(0)}”; write real words or a visible [PLACEHOLDER]")
        proof = _PROOF.search(text)
        if proof:
            add("proof", "check", line, f"“{_snippet(text, proof.start(), 40)}” reads like proof; if you were not given it, make it a [PLACEHOLDER]")
    for line, tag in reader.clickable:
        add("clickable-div", "fix", line, f"a clickable <{tag}>; use <button> for an action or <a href> to go somewhere")
    if reader.collection and not state_pages:
        said = " ".join(text for _, text in reader.text)
        if not _STATES.search(said):
            add(
                "states",
                "check",
                reader.collection,
                "the page shows a collection and draws no loading, empty or error state; if it loads data, draw them as "
                "pages beside it (name.loading.html, name.empty.html, name.error.html)",
            )
    for line, tag, words in reader.labels:
        said = " ".join(words.split()).strip().lower().rstrip(".!")
        if said in _VAGUE:
            add("vague-label", "fix", line, f"a {tag} that says “{words.strip()}”; say what happens (“Save changes”)")
    out: list[LintFinding] = []
    for findings in found.values():
        out.extend(findings[:_PER_RULE])
        if len(findings) > _PER_RULE:
            first = findings[0]
            out.append(LintFinding(first.rule, first.kind, first.line, f"and {len(findings) - _PER_RULE} more like it"))
    return sorted(out, key=lambda item: (item.kind != "fix", item.line))
